import base64
import os
import threading
import time
from typing import Any

import cv2
import numpy as np

# Try to import new MediaPipe Tasks API
try:
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    mp_available = True
except ImportError:
    mp_available = False

try:
    # Disable heavy YOLO imports on Render or when DISABLE_YOLO is set to prevent 512MB OOM
    if os.getenv("RENDER", "").lower() in ("true", "1") or os.getenv("DISABLE_YOLO", "0").lower() in ("true", "1"):
        yolo_available = False
    else:
        from ultralytics import YOLO
        yolo_available = True
except Exception:
    yolo_available = False

class YOLOEnsemble:
    """Wrapper that combines multiple YOLO models into a single interface."""
    def __init__(self, model_candidates: list[tuple[str, str]]) -> None:
        self.models = []
        self.names = {}
        loaded_models = []
        
        for i, (model_type, model_path) in enumerate(model_candidates):
            if os.path.exists(model_path):
                try:
                    model = YOLO(model_path)
                    self.models.append((model_type, model))
                    loaded_models.append(model_type)
                    # Map the classes to the ensemble names dict
                    for cls_id, cls_name in model.names.items():
                        ensemble_cls_id = i * 1000 + cls_id
                        self.names[ensemble_cls_id] = f"{cls_name} ({model_type})"
                    print(f"[OK] Ensemble loaded {model_type}: {model_path}")
                except Exception as exc:
                    print(f"[FAIL] Ensemble failed to load {model_path}: {exc}")

        if self.models:
            self.status = f"ensemble: {', '.join(loaded_models)}"
            self.is_custom_yolo = any(m_type.startswith("custom") for m_type, _ in self.models)
        else:
            self.status = "disabled: no YOLO models loaded"
            self.is_custom_yolo = False

    def __call__(self, frame, verbose=False):
        class EnsembleBox:
            def __init__(self, cls_id, conf=0.0):
                self.cls = [cls_id]
                self.conf = float(conf)

        class EnsembleResult:
            def __init__(self, boxes):
                self.boxes = boxes

        ensemble_boxes = []
        for i, (model_type, model) in enumerate(self.models):
            try:
                results = model(frame, verbose=verbose)
                for r in results:
                    for box in r.boxes:
                        cls_id = int(box.cls[0])
                        ensemble_cls_id = i * 1000 + cls_id
                        try:
                            conf = float(box.conf[0])
                        except Exception:
                            conf = 0.0
                        ensemble_boxes.append(EnsembleBox(ensemble_cls_id, conf))
            except Exception:
                pass
        
        return [EnsembleResult(ensemble_boxes)]

class MeetingLockRegistry:
    """Per-meeting serialization locks with idle auto-cleanup.

    Each active meeting owns a threading.Lock so concurrent interviews never
    block each other on the shared detector instance (the previous single
    global lock serialized every candidate in every meeting). Locks are
    created lazily on first use and removed by a background janitor after
    MEETING_LOCK_TTL_S of inactivity; /session/leave also drops its lock
    immediately. The registry itself is thread-safe (a meta-lock guards the
    dict); the per-meeting locks are plain threading.Lock objects handed to
    callers, who acquire them without ever holding the meta-lock, so there is
    no lock-ordering hazard and no cross-meeting contention.
    """
    MEETING_LOCK_TTL_S = 900.0     # 15 minutes idle -> drop
    JANITOR_INTERVAL_S = 120.0     # janitor sweep cadence

    def __init__(self) -> None:
        self._locks: dict[str, dict] = {}  # meeting_id -> lock + last use
        self._guard = threading.Lock()
        self._janitor_started = False

    def get(self, meeting_id: str) -> threading.Lock:
        """Return the lock for a meeting, creating it on first use."""
        meeting_id = (meeting_id or "").upper() or "_default"
        with self._guard:
            entry = self._locks.get(meeting_id)
            if entry is None:
                entry = {"lock": threading.Lock(), "last_use": time.time()}
                self._locks[meeting_id] = entry
            entry["last_use"] = time.time()
            self._ensure_janitor()
            return entry["lock"]

    def drop(self, meeting_id: str) -> None:
        """Drop a meeting's lock immediately (session/meeting ended).

        A lock that is currently held (or has a waiter queued) is left in
        place: removing it while in flight would let a subsequent get() create
        a fresh lock while the old one is still being used, splitting the
        meeting's critical section into two. The idle janitor reclaims it once
        the last request finishes.
        """
        meeting_id = (meeting_id or "").upper() or "_default"
        with self._guard:
            entry = self._locks.get(meeting_id)
            if entry is not None and not entry["lock"].locked():
                self._locks.pop(meeting_id, None)

    def _sweep(self, now: float = None) -> int:
        """Remove idle, uncontended locks. Returns how many were dropped."""
        now = now if now is not None else time.time()
        with self._guard:
            stale = [
                mid
                for mid, entry in self._locks.items()
                if now - entry["last_use"] > self.MEETING_LOCK_TTL_S
                and not entry["lock"].locked()
            ]
            for mid in stale:
                del self._locks[mid]
            return len(stale)

    def _ensure_janitor(self) -> None:
        if self._janitor_started:
            return
        self._janitor_started = True
        threading.Thread(
            target=self._janitor_loop,
            name="meeting-lock-janitor",
            daemon=True,
        ).start()

    def _janitor_loop(self) -> None:
        while True:
            time.sleep(self.JANITOR_INTERVAL_S)
            try:
                self._sweep()
            except Exception:
                pass


try:
    # Disable torch on Render or when DISABLE_TORCH is set to keep memory <100MB
    if os.getenv("RENDER", "").lower() in ("true", "1") or os.getenv("DISABLE_TORCH", "0").lower() in ("true", "1"):
        torch_available = False
        torch = None
        nn = None
    else:
        import torch
        import torch.nn as nn
        torch_available = True
        try:
            torch.set_num_threads(1)
        except Exception:
            pass
except Exception:
    torch_available = False
    torch = None
    nn = None

if torch_available and nn is not None:
    class HeadPoseAutoencoder(nn.Module):
        def __init__(self, input_dim):
            super().__init__()
            hidden = max(8, input_dim // 2)
            bottleneck = max(4, input_dim // 8)
            self.encoder = nn.Sequential(
                nn.Linear(input_dim, hidden),
                nn.ReLU(),
                nn.Linear(hidden, bottleneck),
                nn.ReLU(),
            )
            self.decoder = nn.Sequential(
                nn.Linear(bottleneck, hidden),
                nn.ReLU(),
                nn.Linear(hidden, input_dim),
                nn.ReLU(),
            )

        def forward(self, x):
            return self.decoder(self.encoder(x))
else:
    class HeadPoseAutoencoder:
        pass


class SessionState:
    """Per-meeting temporal detection state.

    Every field here is scoped to ONE interview: pose EMA, time-based
    persistence clocks, direction debounce, adaptive AE threshold calibration,
    reflection streak, NO_FACE grace window, and the YOLO confirmation
    counters. The detector itself (MediaPipe mesh, YOLO ensemble, autoencoder)
    stays shared and stateless — only this data distinguishes one meeting from
    another, so two concurrent interviews can never read or wipe each other's
    history (the previous single set of instance attributes meant a candidate
    joining reset the live session of every other candidate).

    ``gen`` is the session generation: bumped on every reset so an in-flight
    background YOLO result from a previous session is discarded instead of
    polluting the new one.
    """

    def __init__(self) -> None:
        # EMA-smoothed head pose + time-based persistence state (kills
        # solvePnP jitter and single-frame pose anomalies; a pose must stay
        # abnormal for _POSE_ABNORMAL_PERSIST_S before it is flagged).
        self.pose_smooth = None
        self.abnormal_since = None
        self.normal_since = None
        self.pose_abnormal_flag = False
        # Corneal reflection temporal confirmation (2 of last frames).
        self.reflection_streak = 0
        # NO_FACE grace: keep the last known direction/pose briefly when the
        # face disappears for < 2s (occlusion), avoiding direction flicker.
        self.last_face_time = 0.0
        self.last_face_direction = "CENTER"
        self.last_face_pose = (0.0, 0.0, 0.0)
        # Adaptive pose-anomaly threshold (calibrated from the first frames of
        # each session) and gaze direction debounce state.
        self.pose_calibration_errors = []
        self.pose_calibrated = False
        self.pose_effective_threshold = None
        self.dir_last = None
        self.dir_streak = 0
        self.dir_output = "CENTER"
        # Throttle the annotated-frame PiP re-encode (per session).
        self.last_annotate_time = 0.0
        # Temporal confirmation: an object must persist across consecutive
        # YOLO runs (5s apart) before being reported, to kill one-off
        # single-frame false detections.
        self.yolo_hits: dict[str, int] = {}
        # Highest YOLO box confidence seen for the currently reported objects
        # (0-1); surfaced as objectsConfidence so the fusion layer can weight
        # the object signal by how sure the detector was.
        self.last_yolo_conf = 0.0
        self.last_yolo_objects: list[str] = []
        self.session_gen = 0
        # Wall-clock of the last analyze/reset touch. An idle janitor evicts
        # sessions quieter than _SESSION_TTL_S so an abandoned interview (tab
        # closed without /session/leave) cannot leak its SessionState forever;
        # a resumed meeting simply re-initialises (a fresh grace window).
        self.last_used = time.time()


class AdvancedGazeDetector:
    """MediaPipe gaze / head-pose / YOLO detector.

    All temporal state (pose EMA, hysteresis streaks, YOLO hit counters,
    NO_FACE grace) lives in a per-meeting :class:`SessionState`. Call
    `reset_meeting(meeting_id)` when a candidate joins and
    `drop_meeting(meeting_id)` when the interview ends, so state is created
    fresh per interview and never leaks — or gets wiped — across rooms.
    """

    # Outer and inner corner indices for face landmarker
    LEFT_EYE = [33, 133]
    RIGHT_EYE = [362, 263]
    # Iris center + contour landmarks: averaging the contour gives a far more
    # stable iris position than the single center landmark alone.
    LEFT_IRIS = [468, 474, 475, 476, 477]
    RIGHT_IRIS = [473, 469, 470, 471, 472]

    # How many AE reconstruction-error samples to collect before adapting the
    # anomaly threshold to the real face (the static threshold comes from
    # synthetic training data and does not match real facial noise).
    _POSE_CALIBRATION_SAMPLES = 12
    # The adaptive threshold is clamped so a candidate moving a lot during the
    # calibration window cannot push it out of a sane range.
    _POSE_CALIBRATION_CLAMP = (0.8, 3.0)
    # Gaze direction must persist this many consecutive analysis frames before
    # the reported direction flips (kills iris-ratio boundary flicker).
    _DIRECTION_PERSIST_FRAMES = 2
    # Head-pose anomaly must persist this long (seconds) before it is flagged,
    # and must stay normal that long before it clears — time-based, so the
    # verdict is independent of the frame rate (gaze runs at ~1 fps).
    _POSE_ABNORMAL_PERSIST_S = 2.0
    # A per-meeting SessionState idle longer than this is reclaimed by the
    # janitor (matches the meeting-lock TTL so both retire together). A
    # candidate who resumes after this simply re-initialises their session.
    _SESSION_TTL_S = 900.0
    _SESSION_SWEEP_INTERVAL_S = 120.0
    # YOLO object detection runs on a dedicated background worker thread: the
    # ensemble inference is the most expensive op in the pipeline (~1-2s), so
    # the /analyze request must never block on it. The worker re-analyzes the
    # newest offered frame at most this often.
    _YOLO_INTERVAL_S = 5.0
    _disabled_yolo_models = set()
    # Items that are genuinely suspicious DURING an interview. Deliberately
    # excluded: laptop / book / notebook — those are normal interview
    # surroundings and were the biggest source of YOLO false positives.
    SUSPICIOUS_OBJECT_LABELS = {
        "cell phone",
        "mobile phone",
        "phone",
        "earphone",
        "earphones",
        "earpiece",
        "headphone",
        "headphones",
        "smartwatch",
        "tablet",
        "pen camera",
        "mini device",
    }

    def __init__(self) -> None:
        self.face_mesh = None
        self._init_mediapipe()

        # Per-meeting temporal state + a small lock guarding the map itself
        # (the SessionState objects are only mutated under the meeting lock).
        self._sessions: dict[str, SessionState] = {}
        self._sessions_guard = threading.Lock()
        # Reclaims SessionState for interviews that ended without a clean
        # /session/leave (tab closed, crash). Started lazily on first use so
        # importing the module (or running unit tests) spawns no thread.
        self._session_janitor_started = False

        # Serializes analyze_frame calls PER MEETING: temporal state is
        # per-meeting now, but two concurrent requests for the SAME meeting
        # could still interleave read-modify-write on it. Each meeting owns
        # its own lock (MeetingLockRegistry) so two interviews never block
        # each other. With the combined /analyze endpoint the client sends
        # one request at a time (in-flight gated), but the lock makes it safe
        # even if a second caller fires concurrently.
        self.meeting_locks = MeetingLockRegistry()

        # MediaPipe FaceLandmarker instances are NOT thread-safe: concurrent
        # detect() calls from different meeting threads must serialize on
        # this global inference lock.
        self._mp_lock = threading.Lock()
        
        self.face_detector = self._load_face_detector()
        
        self.yolo_model = None
        self.last_yolo_time = 0
        self.is_custom_yolo = False
        self.object_detector_status = "unavailable"
        # Background YOLO worker plumbing: _yolo_lock guards the worker state;
        # _yolo_cond wakes the worker when new frames are offered. Pending
        # frames are QUEUED per meeting so concurrent interviews each get
        # detections (the old single-slot + instance-level hit counters meant
        # two rooms shared one YOLO verdict stream).
        self._yolo_lock = threading.Lock()
        self._yolo_cond = threading.Condition(self._yolo_lock)
        self._yolo_pending: list[tuple[str, Any, int]] = []
        self._yolo_worker_started = False

        if yolo_available:
            self._init_yolo()

        self.pose_autoencoder = None
        self.pose_mean = None
        self.pose_std = None
        self.pose_threshold = None
        self._init_pose_autoencoder()

        self.metadata = {
            "module": "advanced_gaze_detector.py",
            "engine": "MediaPipe FaceLandmarker" if self.face_mesh else "Haar Cascade",
            "estimatedAccuracy": 95 if self.face_mesh else 50,
            "nextSteps": [
                "Train a custom ML model on interview-specific data.",
            ],
            "objectDetector": self.object_detector_status,
        }

    # ─── Per-meeting session state ─────────────────────────────────────────

    def _key(self, meeting_id) -> str:
        """Normalize the meeting key ('' -> shared default bucket, for legacy
        callers and tests that run without a meeting context)."""
        return (meeting_id or "").strip().upper() or "_default"

    def get_session(self, meeting_id) -> SessionState:
        """Return (creating on first use) the SessionState for a meeting."""
        key = self._key(meeting_id)
        with self._sessions_guard:
            session = self._sessions.get(key)
            if session is None:
                session = SessionState()
                self._sessions[key] = session
            session.last_used = time.time()
            self._ensure_session_janitor()
            return session

    def reset_meeting(self, meeting_id) -> SessionState:
        """Install a FRESH SessionState for one meeting (candidate join).

        Only this meeting is affected — a new candidate joining can no longer
        wipe the EMA pose, calibration or YOLO confirmation state of another
        live interview. Drops the meeting's queued YOLO frames and bumps the
        session generation so an in-flight background result is discarded.
        """
        key = self._key(meeting_id)
        fresh = SessionState()
        with self._sessions_guard:
            old = self._sessions.get(key)
            fresh.session_gen = (old.session_gen + 1) if old else 1
            self._sessions[key] = fresh
        with self._yolo_lock:
            self._yolo_pending = [entry for entry in self._yolo_pending if entry[0] != key]
        return fresh

    def drop_meeting(self, meeting_id) -> None:
        """Delete all state for an ended interview (leave / janitor)."""
        key = self._key(meeting_id)
        with self._sessions_guard:
            self._sessions.pop(key, None)
        with self._yolo_lock:
            self._yolo_pending = [entry for entry in self._yolo_pending if entry[0] != key]
        self.meeting_locks.drop(key)

    def _sweep_sessions(self, now: float = None) -> int:
        """Evict SessionState idle past _SESSION_TTL_S. Returns the count.

        last_used is refreshed on every get_session (i.e. every analyze_frame),
        so a genuinely active interview can never look idle; only sessions with
        no traffic for the full TTL are reclaimed. This is the safety net for
        interviews that ended without a clean /session/leave (tab closed,
        browser crash): the meeting lock is dropped for them too.
        """
        now = now if now is not None else time.time()
        evicted = []
        with self._sessions_guard:
            for key, session in list(self._sessions.items()):
                if now - session.last_used > self._SESSION_TTL_S:
                    self._sessions.pop(key, None)
                    evicted.append(key)
        for key in evicted:
            with self._yolo_lock:
                self._yolo_pending = [
                    e for e in self._yolo_pending if e[0] != key
                ]
            self.meeting_locks.drop(key)
        return len(evicted)

    def _ensure_session_janitor(self) -> None:
        if self._session_janitor_started:
            return
        self._session_janitor_started = True
        threading.Thread(
            target=self._session_janitor_loop,
            name="session-state-janitor",
            daemon=True,
        ).start()

    def _session_janitor_loop(self) -> None:
        while True:
            time.sleep(self._SESSION_SWEEP_INTERVAL_S)
            try:
                self._sweep_sessions()
            except Exception:
                pass

    def active_session_ids(self) -> list[str]:
        with self._sessions_guard:
            return list(self._sessions.keys())

    def _init_yolo(self) -> None:
        if os.getenv("DISABLE_YOLO", "0").strip().lower() in ("1", "true", "yes"):
            self.yolo_model = None
            self.object_detector_status = "Disabled via DISABLE_YOLO"
            self.is_custom_yolo = False
            print("[YOLO] Disabled via DISABLE_YOLO environment variable (low-memory mode)")
            return

        # On memory-constrained hosts (e.g. Render 512MB), load only the best single model
        model_candidates = []
        if os.path.exists("cheating_detector_v2.pt"):
            model_candidates = [("custom-v2", "cheating_detector_v2.pt")]
        elif os.path.exists("cheating_detector.pt"):
            model_candidates = [("custom", "cheating_detector.pt")]
        elif os.path.exists("yolov8n.pt"):
            model_candidates = [("base", "yolov8n.pt")]

        ensemble = YOLOEnsemble(model_candidates)
        if ensemble.models:
            self.yolo_model = ensemble
            self.object_detector_status = ensemble.status
            self.is_custom_yolo = ensemble.is_custom_yolo
        else:
            self.yolo_model = None
            self.object_detector_status = ensemble.status
            self.is_custom_yolo = False

    # ─── Background YOLO worker ────────────────────────────────────────────────
    # The ensemble inference (~1-2s on CPU) is off the /analyze request path:
    # the request only offers a frame copy and reads the cached result back
    # instantly. All shared state (_yolo_hits, last_yolo_objects, conf, cadence,
    # pending frame) is guarded by _yolo_lock; the slow inference itself runs
    # WITHOUT the lock so a request offering a frame never blocks behind it.

    def _ensure_yolo_worker(self) -> None:
        """Start the singleton background YOLO worker (daemon, idempotent)."""
        with self._yolo_lock:
            if self._yolo_worker_started:
                return
            self._yolo_worker_started = True
        threading.Thread(
            target=self._yolo_worker_loop,
            name="yolo-background",
            daemon=True,
        ).start()

    def _offer_yolo_frame(self, meeting_id, frame) -> None:
        """Queue the newest frame for a meeting and wake the worker.

        The frame is copied so the worker never races with the main thread's
        later in-place annotation (cv2.putText) of the same array. Each entry
        carries the session generation it was offered under; results for
        stale generations are discarded.
        """
        key = self._key(meeting_id)
        try:
            session = self.get_session(key)
            with self._yolo_lock:
                # Keep only the newest frame per meeting — older queued
                # frames of the same room are already superseded.
                self._yolo_pending = [
                    entry for entry in self._yolo_pending if entry[0] != key
                ]
                self._yolo_pending.append((key, frame.copy(), session.session_gen))
                self._yolo_cond.notify()
        except Exception:
            pass

    def _yolo_worker_loop(self) -> None:
        while True:
            with self._yolo_cond:
                # Check-then-wait: peek BEFORE blocking so frames deposited
                # before this thread reached wait() (startup race) are picked
                # up immediately instead of after a full timeout tick.
                if not self._yolo_pending:
                    self._yolo_cond.wait(timeout=self._YOLO_INTERVAL_S)
                    if not self._yolo_pending:
                        continue
                now = time.time()
                if now - self.last_yolo_time < self._YOLO_INTERVAL_S:
                    # Not due yet — sleep until the cadence tick (a deposit
                    # mid-sleep wakes us harmlessly).
                    self._yolo_cond.wait(timeout=self._YOLO_INTERVAL_S)
                    continue
                batch = self._yolo_pending
                self._yolo_pending = []
                self.last_yolo_time = now
            # Inference runs OUTSIDE the lock: deposits must never block.
            for key, frame, gen in batch:
                self._run_yolo_detection(key, frame, gen)

    def _run_yolo_detection(self, meeting_key: str, frame, gen: int) -> None:
        """One throttled YOLO pass over `frame`, updating the meeting's cache.

        The (slow) model inference is lock-free; only the small state update
        takes the lock. `gen` is the session generation this frame belongs to:
        if the meeting was reset mid-run, the result is discarded so stale
        detections never leak into a fresh interview.
        """
        seen_this_run = set()
        run_max_conf = 0.0
        try:
            results = self.yolo_model(frame, verbose=False)
            for r in results:
                for box in r.boxes:
                    cls_id = int(box.cls[0])
                    cls_name = self.yolo_model.names[cls_id]
                    base_name = cls_name.split(" (")[0]
                    if base_name.lower() in self.SUSPICIOUS_OBJECT_LABELS:
                        seen_this_run.add(cls_name)
                        # Defensive: a malformed/non-float conf must not abort
                        # the run (which would lose every object after it).
                        try:
                            conf_val = float(getattr(box, "conf", 0.0))
                        except (TypeError, ValueError):
                            conf_val = 0.0
                        if conf_val > run_max_conf:
                            run_max_conf = conf_val
        except Exception:
            pass
        with self._sessions_guard:
            session = self._sessions.get(meeting_key)
        if session is None or session.session_gen != gen:
            return  # meeting dropped or reset mid-run — discard this batch
        if seen_this_run:
            session.last_yolo_conf = run_max_conf
        for cls_name in list(session.yolo_hits.keys()):
            session.yolo_hits[cls_name] += 1 if cls_name in seen_this_run else -1
            if session.yolo_hits[cls_name] <= 0:
                del session.yolo_hits[cls_name]
        for cls_name in seen_this_run:
            if cls_name not in session.yolo_hits:
                session.yolo_hits[cls_name] = 1
        session.last_yolo_objects = [
            c for c, hits in session.yolo_hits.items() if hits >= 2
        ]
        if not session.last_yolo_objects:
            session.last_yolo_conf = 0.0

    def _init_pose_autoencoder(self) -> None:
        if not torch_available or torch is None:
            self.pose_autoencoder = None
            print("[INFO] Head pose autoencoder skipped (running lightweight mode for cloud/Render)")
            return
        autoencoder_path = os.path.join("trained_models", "head_pose_autoencoder.pt")
        if os.path.exists(autoencoder_path):
            try:
                checkpoint = torch.load(autoencoder_path, map_location="cpu", weights_only=False)
                input_dim = checkpoint["input_dim"]
                self.pose_mean = checkpoint["mean"]
                self.pose_std = checkpoint["std"]
                self.pose_threshold = checkpoint["threshold"]
                
                self.pose_autoencoder = HeadPoseAutoencoder(input_dim)
                self.pose_autoencoder.load_state_dict(checkpoint["model_state"])
                self.pose_autoencoder.eval()
                try:
                    threshold_val = float(self.pose_threshold)
                except (TypeError, ValueError):
                    threshold_val = self.pose_threshold
                print(f"[OK] Loaded head pose autoencoder with threshold: {threshold_val}")
            except Exception as e:
                self.pose_autoencoder = None
                print(f"[FAIL] Failed to load head pose autoencoder: {e}")

    def _init_mediapipe(self):
        if not mp_available: return
        model_path = 'face_landmarker.task'
        if not os.path.exists(model_path):
            print(f"[AdvancedGazeDetector] Could not find {model_path}")
            return
            
        try:
            base_options = python.BaseOptions(model_asset_path=model_path)
            options = vision.FaceLandmarkerOptions(
                base_options=base_options,
                output_face_blendshapes=False,
                output_facial_transformation_matrixes=False,
                num_faces=4
            )
            self.face_mesh = vision.FaceLandmarker.create_from_options(options)
        except Exception as e:
            print(f"[AdvancedGazeDetector] Failed to initialize FaceLandmarker: {e}")
            self.face_mesh = None

    @property
    def mediapipe_available(self) -> bool:
        return self.face_mesh is not None

    def _load_face_detector(self) -> cv2.CascadeClassifier:
        # Try local file first, then fall back to cv2 data directory
        local_path = "haarcascade_frontalface_default.xml"
        if os.path.exists(local_path):
            return cv2.CascadeClassifier(local_path)
        
        data_dir = getattr(getattr(cv2, "data", None), "haarcascades", None)
        if data_dir:
            cascade_path = os.path.join(data_dir, "haarcascade_frontalface_default.xml")
            if os.path.exists(cascade_path):
                return cv2.CascadeClassifier(cascade_path)
        
        # Return empty classifier as fallback
        return cv2.CascadeClassifier()

    def _calibrate_pose_threshold(self, session: SessionState, mse_loss: float, forward_facing: bool = True) -> None:
        """Adapt the pose-anomaly threshold to this candidate's real face.

        The static threshold is computed from synthetic training data; real
        faces produce a different AE reconstruction-error scale, so the raw
        threshold false-positives (or misses) in practice. Collect the first
        N forward-facing errors of the session and build a robust baseline
        (median + 3*MAD, MAD-normalized) that is clamped to a sane band
        around the static threshold.

        Samples are only collected while the head is roughly forward-facing
        (|pitch|,|yaw| < 10 deg) — a candidate who cheats during the whole
        calibration window must hold a suspicious pose the entire time, and
        even then can only push the threshold to the clamp ceiling, never
        beyond. If no forward-facing samples accumulate, the static threshold
        stays in effect.
        """
        if session.pose_calibrated or self.pose_threshold is None:
            return
        if session.pose_calibration_errors is None:
            return
        if not forward_facing:
            return
        session.pose_calibration_errors.append(float(mse_loss))
        if len(session.pose_calibration_errors) < self._POSE_CALIBRATION_SAMPLES:
            return

        arr = np.array(session.pose_calibration_errors)
        median = float(np.median(arr))
        mad = float(np.median(np.abs(arr - median)))
        # 1.4826 scales MAD to a standard-deviation-equivalent; median + 3*MAD
        # ~= median + 3 sigma for Gaussian noise, without mean/std sensitivity.
        baseline = median + 3.0 * (1.4826 * mad) if mad > 1e-9 else median + 1.0
        low = self.pose_threshold * self._POSE_CALIBRATION_CLAMP[0]
        high = self.pose_threshold * self._POSE_CALIBRATION_CLAMP[1]
        session.pose_effective_threshold = float(np.clip(baseline, low, high))
        session.pose_calibrated = True
        session.pose_calibration_errors = None

    def _update_pose_abnormal_flag(self, session: SessionState, abnormal_now: bool, now: float) -> bool:
        """Time-based persistence for the pose-anomaly flag.

        A pose must stay abnormal for _POSE_ABNORMAL_PERSIST_S before the flag
        turns on (single-frame solvePnP spikes are ignored) and must stay
        normal that long before it turns off (no flicker on brief recovery).
        Being time-based (not frame-based) keeps the verdict identical at any
        analysis frame rate.
        """
        if abnormal_now:
            if session.abnormal_since is None:
                session.abnormal_since = now
            session.normal_since = None
        else:
            session.abnormal_since = None
            if session.normal_since is None:
                session.normal_since = now

        if session.abnormal_since is not None and (
            now - session.abnormal_since
        ) >= self._POSE_ABNORMAL_PERSIST_S:
            session.pose_abnormal_flag = True
        elif session.normal_since is not None and (
            now - session.normal_since
        ) >= self._POSE_ABNORMAL_PERSIST_S:
            session.pose_abnormal_flag = False
        return session.pose_abnormal_flag

    def _smooth_direction(self, session: SessionState, direction: str) -> str:
        """Debounce the reported gaze direction.

        The iris-ratio test sits on 0.40/0.60 boundaries, so a candidate
        looking near-center flickers between CENTER/LEFT/RIGHT frame to frame.
        Requiring the direction to persist for N consecutive frames kills that
        flicker while adding only ~N analysis frames of latency (the frontend
        dwell logic already tolerates seconds of latency). NO_FACE passes
        through untouched — the grace logic owns face loss. When there is no
        debounce history (session start or just after a long face loss), the
        first frame is trusted immediately rather than serving a stale value.
        """
        if direction == "NO_FACE":
            return direction
        if session.dir_last is None:
            session.dir_last = direction
            session.dir_streak = 1
            session.dir_output = direction
            return session.dir_output
        if direction == session.dir_last:
            session.dir_streak += 1
        else:
            session.dir_last = direction
            session.dir_streak = 1
        if session.dir_streak >= self._DIRECTION_PERSIST_FRAMES:
            session.dir_output = direction
        return session.dir_output

    def _get_gaze_direction(
        self,
        landmarks: list[Any],
        eye_points: list[int],
        iris_point: list[int],
        frame_w: int,
    ) -> str:
        left = landmarks[eye_points[0]]
        right = landmarks[eye_points[1]]

        left_x = int(left.x * frame_w)
        right_x = int(right.x * frame_w)

        min_x = min(left_x, right_x)
        max_x = max(left_x, right_x)
        span = max_x - min_x
        
        if span <= 0:
            return "CENTER"

        # Centroid of all available iris landmarks (center + contour) — much
        # more stable than the single iris-center landmark, which jitters.
        iris_xs = [
            landmarks[i].x * frame_w
            for i in iris_point
            if i < len(landmarks) and hasattr(landmarks[i], "x")
        ]
        if not iris_xs:
            return "CENTER"
        iris_x = int(sum(iris_xs) / len(iris_xs))

        ratio = (iris_x - min_x) / span
        
        # When a user looks to their left, the pupil moves to the right of the image (x=1.0)
        # So a high ratio (>0.60) means they are looking left!
        if ratio > 0.60:
            return "LEFT"
        if ratio < 0.40:
            return "RIGHT"
        return "CENTER"

    def _get_head_pose(self, landmarks, frame_w, frame_h):
        # 3D model points
        face3Dmodel = np.array([
            (0.0, 0.0, 0.0),            # Nose tip
            (0.0, -330.0, -65.0),       # Chin
            (-225.0, 170.0, -135.0),    # Left eye left corner
            (225.0, 170.0, -135.0),     # Right eye right corner
            (-150.0, -150.0, -125.0),   # Left mouth corner
            (150.0, -150.0, -125.0)     # Right mouth corner
        ], dtype=np.float64)

        # 2D image points from MediaPipe
        image_points = np.array([
            (landmarks[1].x * frame_w, landmarks[1].y * frame_h),
            (landmarks[152].x * frame_w, landmarks[152].y * frame_h),
            (landmarks[33].x * frame_w, landmarks[33].y * frame_h),
            (landmarks[263].x * frame_w, landmarks[263].y * frame_h),
            (landmarks[61].x * frame_w, landmarks[61].y * frame_h),
            (landmarks[291].x * frame_w, landmarks[291].y * frame_h)
        ], dtype=np.float64)

        focal_length = frame_w
        center = (frame_w / 2, frame_h / 2)
        camera_matrix = np.array([
            [focal_length, 0, center[0]],
            [0, focal_length, center[1]],
            [0, 0, 1]
        ], dtype=np.float64)

        dist_coeffs = np.zeros((4, 1))

        success, rotation_vector, translation_vector = cv2.solvePnP(
            face3Dmodel, image_points, camera_matrix, dist_coeffs, flags=cv2.SOLVEPNP_ITERATIVE
        )

        if not success:
            return 0, 0, 0

        rmat, _ = cv2.Rodrigues(rotation_vector)
        # NOTE: cv2.RQDecomp3x3 already returns Euler angles in DEGREES.
        # The previous `* 360` inflated every angle 360x, which made the
        # frontend thresholds (expecting degrees) and the head-pose
        # autoencoder (trained on degree-scale data) fire on every frame.
        angles, _, _, _, _, _ = cv2.RQDecomp3x3(rmat)

        pitch = angles[0]
        yaw = angles[1]
        roll = angles[2]

        return pitch, yaw, roll

    def _analyze_corneal_reflection(self, frame, landmarks, width, height):
        left_iris_indices = [474, 475, 476, 477]
        right_iris_indices = [469, 470, 471, 472]
        
        reflection_detected = False
        
        for indices in [left_iris_indices, right_iris_indices]:
            # Bounding box of the iris
            x_coords = [int(landmarks[i].x * width) for i in indices]
            y_coords = [int(landmarks[i].y * height) for i in indices]
            
            x_min, x_max = max(0, min(x_coords) - 5), min(width, max(x_coords) + 5)
            y_min, y_max = max(0, min(y_coords) - 5), min(height, max(y_coords) + 5)
            
            if x_max <= x_min or y_max <= y_min:
                continue
                
            # Extract iris patch
            iris_patch = frame[y_min:y_max, x_min:x_max]
            
            # OpenCV Algorithmic Model (Phase 1)
            try:
                gray = cv2.cvtColor(iris_patch, cv2.COLOR_BGR2GRAY)
                # Isolate extreme brightness (screen reflection)
                _, thresh = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY)
                
                contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                
                for contour in contours:
                    area = cv2.contourArea(contour)
                    if area > 4: # Size threshold for a screen reflection in a high-res iris patch
                        # Shape approximation
                        epsilon = 0.15 * cv2.arcLength(contour, True)
                        approx = cv2.approxPolyDP(contour, epsilon, True)
                        
                        # 4 corners = rectangular screen
                        if len(approx) == 4:
                            reflection_detected = True
                            cv2.drawContours(iris_patch, [approx], 0, (0, 0, 255), 1)
                            
                frame[y_min:y_max, x_min:x_max] = iris_patch
            except Exception as e:
                pass
                
        return reflection_detected

    def get_landmarks(self, frame: np.ndarray) -> list[dict[str, float]]:
        if not self.face_mesh:
            return []
        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            # One FaceLandmarker instance is shared by every meeting thread;
            # MediaPipe detect() is NOT thread-safe, so serialize inference
            # globally (state stays per-meeting).
            with self._mp_lock:
                result = self.face_mesh.detect(mp_image)
            if result and result.face_landmarks:
                landmarks = result.face_landmarks[0]
                return [{"x": l.x, "y": l.y, "z": l.z} for l in landmarks]
        except Exception:
            pass
        return []

    def analyze_frame(
        self, frame: np.ndarray, meeting_id=None, return_landmarks: bool = False
    ) -> dict[str, Any] | tuple[dict[str, Any], list]:
        """Analyze one frame for one meeting; optionally also return landmarks.

        All temporal smoothing/confirmation state is read and written on the
        meeting's SessionState, so concurrent interviews never share (or
        wipe) each other's history. When `return_landmarks` is True, returns
        (result, landmarks) where the landmarks are the MediaPipe mesh for
        THIS frame, kept local to the call so the liveness analyzer can reuse
        the same inference pass. They must stay local: a concurrent meeting
        could otherwise overwrite shared state between analyze_frame
        returning and the caller reading it.
        """
        session = self.get_session(meeting_id)
        height, width, _ = frame.shape
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        direction = "NO_FACE"
        face_detected = False
        multiple_faces = False
        reflection_detected = False
        engine = "none"
        local_landmarks: list = []
        
        pitch, yaw, roll = 0, 0, 0
        pose_abnormal = False

        # YOLO Object Detection runs on a BACKGROUND worker thread: the
        # ensemble inference is the most expensive op in the pipeline (~1-2s),
        # so the /analyze request only offers a frame copy and reads the latest
        # cached result back instantly — it never blocks on inference. The
        # worker re-analyzes at most every _YOLO_INTERVAL_S, and objects must
        # be seen in 2 consecutive runs (~10s) before being reported (they
        # clear after 2 missed runs) — killing single-run false positives
        # while still catching a phone that stays on screen.
        if getattr(self, "yolo_model", None) is not None and getattr(self.yolo_model, "models", None):
            self._ensure_yolo_worker()
            self._offer_yolo_frame(meeting_id, frame)
        objects_detected = list(session.last_yolo_objects)
        yolo_conf = session.last_yolo_conf

        if self.face_mesh:
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            # Shared FaceLandmarker is not thread-safe: serialize inference
            # globally while all smoothing state stays per-meeting.
            with self._mp_lock:
                result = self.face_mesh.detect(mp_image)
            
            if result and result.face_landmarks:
                face_detected = True
                multiple_faces = len(result.face_landmarks) > 1
                
                landmarks = result.face_landmarks[0]
                # Keep the mesh local to this call: the combined /analyze
                # route reuses it for liveness (one MediaPipe pass, no second
                # inference). Local, not shared — a concurrent meeting must
                # never swap another meeting's landmarks.
                local_landmarks = [
                    {"x": l.x, "y": l.y, "z": l.z} for l in landmarks
                ]
                left_gaze = self._get_gaze_direction(
                    landmarks,
                    self.LEFT_EYE,
                    self.LEFT_IRIS,
                    width,
                )
                right_gaze = self._get_gaze_direction(
                    landmarks,
                    self.RIGHT_EYE,
                    self.RIGHT_IRIS,
                    width,
                )
                
                # Head Pose Estimation with EMA smoothing (solvePnP jitters
                # frame-to-frame; smoothing keeps both the AE and the direction
                # thresholds stable).
                raw_pitch, raw_yaw, raw_roll = self._get_head_pose(landmarks, width, height)
                if session.pose_smooth is None:
                    session.pose_smooth = (raw_pitch, raw_yaw, raw_roll)
                else:
                    alpha = 0.45
                    sp, sy, sr = session.pose_smooth
                    session.pose_smooth = (
                        alpha * raw_pitch + (1 - alpha) * sp,
                        alpha * raw_yaw + (1 - alpha) * sy,
                        alpha * raw_roll + (1 - alpha) * sr,
                    )
                pitch, yaw, roll = session.pose_smooth
                
                # Run anomaly detection using the Autoencoder (on smoothed pose)
                if self.pose_autoencoder is not None:
                    try:
                        pose_vec = np.array([[pitch, yaw, roll]], dtype=np.float32)
                        pose_normalized = (pose_vec - self.pose_mean) / self.pose_std
                        pose_tensor = torch.tensor(pose_normalized, dtype=torch.float32)
                        with torch.no_grad():
                            recon = self.pose_autoencoder(pose_tensor)
                            mse_loss = ((recon - pose_tensor) ** 2).mean(dim=1).item()
                        
                        if self.pose_threshold is not None:
                            self._calibrate_pose_threshold(
                                session,
                                mse_loss,
                                forward_facing=abs(pitch) < 10.0 and abs(yaw) < 10.0,
                            )
                            effective = (
                                session.pose_effective_threshold
                                if session.pose_effective_threshold is not None
                                else self.pose_threshold
                            )
                            abnormal_now = bool(mse_loss > effective)
                            # Time-based persistence: flag only after the pose
                            # stays abnormal for 2s (independent of frame rate).
                            pose_abnormal = self._update_pose_abnormal_flag(
                                session, abnormal_now, time.time()
                            )
                    except Exception as e:
                        print(f"[AdvancedGazeDetector] Pose autoencoder inference error: {e}")
                
                # Phase 1: Corneal Reflection Analysis (debounced)
                refl_now = self._analyze_corneal_reflection(frame, landmarks, width, height)
                if refl_now:
                    session.reflection_streak = min(5, session.reflection_streak + 1)
                else:
                    session.reflection_streak = max(0, session.reflection_streak - 1)
                reflection_detected = session.reflection_streak >= 2
                
                if pitch < -10:
                    direction = "LOOKING_DOWN"
                elif pitch > 15:
                    direction = "LOOKING_UP"
                elif yaw < -15:
                    direction = "RIGHT"
                elif yaw > 15:
                    direction = "LEFT"
                elif left_gaze == right_gaze:
                    direction = left_gaze
                elif "LEFT" in (left_gaze, right_gaze):
                    direction = "LEFT"
                elif "RIGHT" in (left_gaze, right_gaze):
                    direction = "RIGHT"
                else:
                    direction = "CENTER"
                direction = self._smooth_direction(session, direction)
                engine = "mediapipe-tasks"
                session.last_face_time = time.time()
                session.last_face_direction = direction
                session.last_face_pose = (pitch, yaw, roll)
            else:
                # Face lost this frame: brief occlusions (< 2s) keep the last
                # known direction/pose so the UI does not flicker to NO_FACE;
                # faceDetected stays False so covering the camera is still
                # visible to the proctor immediately. Tradeoff: a real
                # camera-cover registers as a looking-away event after
                # grace (2s) + client dwell (~3s) ≈ 5s.
                local_landmarks = []
                session.pose_smooth = None
                if session.last_face_time and (time.time() - session.last_face_time) < 2.0:
                    direction = session.last_face_direction
                    pitch, yaw, roll = session.last_face_pose
                    engine = "mediapipe-tasks (grace)"
                else:
                    # Grace expired: clear the time-based pose clocks, the
                    # reflection streak and the direction-debounce history so a
                    # returning face starts clean instead of inheriting a
                    # half-built one or a stale direction.
                    session.abnormal_since = None
                    session.normal_since = None
                    session.pose_abnormal_flag = False
                    session.reflection_streak = 0
                    session.dir_last = None
                    session.dir_streak = 0

        # If MediaPipe fails or is not available, just use basic face detection.
        # We DO NOT try to guess left/right with Haar because it flickers wildly!
        if not face_detected and not self.face_detector.empty():
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self.face_detector.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(80, 80),
            )
            if len(faces) > 0:
                face_detected = True
                direction = "CENTER"
                engine = "haar-fallback"

        # Per-frame confidence of the gaze verdict: MediaPipe is the trusted
        # engine, the 2s NO_FACE grace keeps last-known direction (slightly
        # lower), Haar is a coarse fallback, and no face at all is low. The
        # fusion layer weights the gaze signal by this value.
        if engine.startswith("mediapipe"):
            verdict_confidence = 90 if face_detected else 30
        elif engine == "haar-fallback":
            verdict_confidence = 55
        else:
            verdict_confidence = 25

        # Encode the frame to base64 for the Live Eye Analysis PiP. The encode
        # is the most expensive non-inference work in the request path, so it
        # is throttled to ~once per 2s — the PiP is a small preview, not a
        # frame-rate video.
        annotated_b64 = None
        try:
            if face_detected and local_landmarks:
                # Draw face bounding box based on landmarks
                h, w = frame.shape[:2]
                landmark_x = [l['x'] for l in local_landmarks]
                landmark_y = [l['y'] for l in local_landmarks]
                
                min_x = int(min(landmark_x) * w)
                max_x = int(max(landmark_x) * w)
                min_y = int(min(landmark_y) * h)
                max_y = int(max(landmark_y) * h)
                
                # Draw face box with color based on monitoring status
                box_color = (0, 255, 0) if not pose_abnormal else (0, 0, 255)
                cv2.rectangle(frame, (min_x, min_y), (max_x, max_y), box_color, 2)
                
                # Draw face landmarks for monitoring visualization
                for landmark in local_landmarks:
                    x = int(landmark['x'] * w)
                    y = int(landmark['y'] * h)
                    cv2.circle(frame, (x, y), 1, (255, 255, 0), -1)
                
                # Add monitoring labels
                label_y = min_y - 10
                
                # Face detection status
                cv2.putText(frame, "FACE DETECTED", (min_x, label_y), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                label_y -= 25
                
                # Gaze direction
                cv2.putText(frame, f"GAZE: {direction}", (min_x, label_y), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
                label_y -= 25
                
                # Head pose information
                cv2.putText(frame, f"PITCH: {pitch:.1f}", (min_x, label_y), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
                label_y -= 20
                cv2.putText(frame, f"YAW: {yaw:.1f}", (min_x, label_y), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
                label_y -= 20
                cv2.putText(frame, f"ROLL: {roll:.1f}", (min_x, label_y), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
                
                # Monitoring status
                status_color = (0, 255, 0) if not pose_abnormal else (0, 0, 255)
                status_text = "MONITORING: OK" if not pose_abnormal else "MONITORING: ALERT"
                cv2.putText(frame, status_text, (min_x, max_y + 20), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, status_color, 2)
                
                # Add confidence score
                cv2.putText(frame, f"CONFIDENCE: {verdict_confidence}%", (min_x, max_y + 40), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
                
                # Add timestamp for continuous monitoring
                timestamp = time.strftime("%H:%M:%S", time.localtime())
                cv2.putText(frame, timestamp, (w - 100, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                
            elif face_detected:
                # Fallback for Haar cascade detection
                cv2.putText(frame, "FACE DETECTED (Haar)", (50, 50), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                cv2.putText(frame, f"GAZE: {direction}", (50, 80), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            else:
                # No face detected
                cv2.putText(frame, "NO FACE DETECTED", (50, 50), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            
            now = time.time()
            if session.last_annotate_time == 0.0 or now - session.last_annotate_time >= 2.0:
                session.last_annotate_time = now
                ret, buffer = cv2.imencode('.jpg', frame)
                if ret:
                    annotated_b64 = "data:image/jpeg;base64," + base64.b64encode(buffer).decode('utf-8')
        except Exception as e:
            print(f"[AdvancedGazeDetector] Annotation error: {e}")
            pass

        result = {
            "direction": direction,
            "lookingAway": direction in {"LEFT", "RIGHT", "NO_FACE", "LOOKING_DOWN", "LOOKING_UP"},
            "faceDetected": face_detected,
            "multipleFaces": multiple_faces,
            "reflectionDetected": reflection_detected,
            "confidence": verdict_confidence,
            "pose": {
                "pitch": pitch,
                "yaw": yaw,
                "roll": roll,
                "abnormal": pose_abnormal,
                # Time-persisted anomalies are high-confidence by construction
                # (2s persistence before flagging); expose for the fusion layer.
                "confidence": 80 if pose_abnormal else None,
            },
            "objectsDetected": objects_detected,
            "objectsConfidence": (
                max(40, min(100, round(yolo_conf * 100)))
                if objects_detected else None
            ),
            "timestamp": time.time(),
            "engine": engine,
            "estimatedAccuracy": self.metadata["estimatedAccuracy"],
            "annotatedFrame": annotated_b64
        }
        if return_landmarks:
            return result, local_landmarks
        return result

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
    from ultralytics import YOLO
    yolo_available = True
except ImportError:
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


import torch
import torch.nn as nn

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
        )

    def forward(self, x):
        return self.decoder(self.encoder(x))

class AdvancedGazeDetector:
    """MediaPipe gaze / head-pose / YOLO detector.

    NOTE: temporal state (pose EMA, hysteresis streaks, YOLO hit counters,
    NO_FACE grace) lives on this instance. It is safe for a single active
    candidate session; call `reset_temporal_state()` on candidate join so
    state never leaks between interviews.
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

        # Serializes analyze_frame calls PER MEETING: it mutates per-session
        # state (pose EMA, streaks, YOLO hit counters), so concurrent requests
        # for the same meeting must not interleave. Each meeting owns its own
        # lock (MeetingLockRegistry) so two interviews never block each other;
        # locks are dropped when idle. With the combined /analyze endpoint the
        # client sends one request at a time (in-flight gated), but the lock
        # makes it safe even if a second caller fires concurrently.
        self.meeting_locks = MeetingLockRegistry()
        # Throttle the annotated-frame PiP re-encode (base64 JPEG is the most
        # expensive non-inference work in the response path).
        self._last_annotate_time = 0.0
        
        self.face_detector = self._load_face_detector()
        
        self.yolo_model = None
        self.last_yolo_time = 0
        self.last_yolo_objects = []
        self.is_custom_yolo = False
        self.object_detector_status = "unavailable"
        # Temporal confirmation: an object must persist across consecutive
        # YOLO runs (5s apart) before being reported, to kill one-off
        # single-frame false detections.
        self._yolo_hits: dict[str, int] = {}
        # Highest YOLO box confidence seen for the currently reported objects
        # (0-1); surfaced as objectsConfidence so the fusion layer can weight
        # the object signal by how sure the detector was.
        self._last_yolo_conf = 0.0
        # Background YOLO worker plumbing: _yolo_lock guards all shared YOLO
        # state; _yolo_cond wakes the worker when a new frame is offered.
        # _session_gen is bumped on every reset so an in-flight inference from
        # a previous session is discarded instead of polluting the new one.
        self._yolo_lock = threading.Lock()
        self._yolo_cond = threading.Condition(self._yolo_lock)
        self._yolo_pending_frame = None
        self._yolo_worker_started = False
        self._session_gen = 0
        # EMA-smoothed head pose + time-based persistence state (kills
        # solvePnP jitter and single-frame pose anomalies; a pose must stay
        # abnormal for _POSE_ABNORMAL_PERSIST_S before it is flagged).
        self._pose_smooth = None
        self._abnormal_since = None
        self._normal_since = None
        self._pose_abnormal_flag = False
        # Corneal reflection temporal confirmation (2 of last frames).
        self._reflection_streak = 0
        # NO_FACE grace: keep the last known direction/pose briefly when the
        # face disappears for < 2s (occlusion), avoiding direction flicker.
        self._last_face_time = 0.0
        self._last_face_direction = "CENTER"
        self._last_face_pose = (0.0, 0.0, 0.0)
        # Adaptive pose-anomaly threshold (calibrated from the first frames of
        # each session) and gaze direction debounce state.
        self._pose_calibration_errors = []
        self._pose_calibrated = False
        self._pose_effective_threshold = None
        self._dir_last = None
        self._dir_streak = 0
        self._dir_output = "CENTER"

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

    def reset_temporal_state(self) -> None:
        """Clear all per-session smoothing / confirmation state.

        Call when a candidate joins so EMA pose, anomaly streaks, YOLO hit
        counters, reflection debounce, and the NO_FACE grace window never leak
        from one interview into the next.
        """
        self._pose_smooth = None
        self._abnormal_since = None
        self._normal_since = None
        self._pose_abnormal_flag = False
        self._reflection_streak = 0
        # Locked: the YOLO worker thread may be mid-run, so never mutate its
        # shared state outside the lock. Bumping _session_gen discards any
        # stale in-flight result from the previous session.
        with self._yolo_lock:
            self._session_gen += 1
            self._yolo_hits.clear()
            self.last_yolo_time = 0
            self.last_yolo_objects = []
            self._last_yolo_conf = 0.0
            self._yolo_pending_frame = None
        self._last_face_time = 0.0
        self._last_face_direction = "CENTER"
        self._last_face_pose = (0.0, 0.0, 0.0)
        self._pose_calibration_errors = []
        self._pose_calibrated = False
        self._pose_effective_threshold = None
        self._dir_last = None
        self._dir_streak = 0
        self._dir_output = "CENTER"
        self._last_annotate_time = 0.0

    def _init_yolo(self) -> None:
        model_candidates = [
            ("custom-v2", "cheating_detector_v2.pt"),
            ("custom", "cheating_detector.pt"),
        ]

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

    def _offer_yolo_frame(self, frame) -> None:
        """Hand the worker the newest frame (copied) and wake it."""
        try:
            with self._yolo_lock:
                # Copy so the worker never races with the main thread's later
                # in-place annotation (cv2.putText) of the same array.
                self._yolo_pending_frame = frame.copy()
                self._yolo_cond.notify()
        except Exception:
            pass

    def _yolo_worker_loop(self) -> None:
        while True:
            with self._yolo_cond:
                # Check-then-wait: peek BEFORE blocking so a frame deposited
                # before this thread reached wait() (startup race) is picked up
                # immediately instead of after a full timeout tick. If nothing
                # is pending, sleep until the next offer or the cadence tick.
                frame = self._yolo_pending_frame
                if frame is None:
                    self._yolo_cond.wait(timeout=self._YOLO_INTERVAL_S)
                    frame = self._yolo_pending_frame
                if frame is None:
                    continue
                now = time.time()
                if now - self.last_yolo_time < self._YOLO_INTERVAL_S:
                    # Not due yet — keep the newest frame and sleep until the
                    # cadence tick (a deposit mid-sleep wakes us harmlessly).
                    self._yolo_cond.wait(timeout=self._YOLO_INTERVAL_S)
                    continue
                self._yolo_pending_frame = None  # consume this frame
                self.last_yolo_time = now
                gen = self._session_gen
            # Inference runs OUTSIDE the lock: deposits must never block.
            self._run_yolo_detection(frame, gen)

    def _run_yolo_detection(self, frame, gen: int) -> None:
        """One throttled YOLO pass over `frame`, updating the cached result.

        The (slow) model inference is lock-free; only the small state update
        takes the lock. `gen` is the session generation this frame belongs to:
        if a session reset happened mid-run, the result is discarded so stale
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
        with self._yolo_lock:
            if gen != self._session_gen:
                return  # stale session — discard this run
            if seen_this_run:
                self._last_yolo_conf = run_max_conf
            for cls_name in list(self._yolo_hits.keys()):
                self._yolo_hits[cls_name] += 1 if cls_name in seen_this_run else -1
                if self._yolo_hits[cls_name] <= 0:
                    del self._yolo_hits[cls_name]
            for cls_name in seen_this_run:
                if cls_name not in self._yolo_hits:
                    self._yolo_hits[cls_name] = 1
            self.last_yolo_objects = [
                c for c, hits in self._yolo_hits.items() if hits >= 2
            ]
            if not self.last_yolo_objects:
                self._last_yolo_conf = 0.0

    def _init_pose_autoencoder(self) -> None:
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

    def _calibrate_pose_threshold(self, mse_loss: float, forward_facing: bool = True) -> None:
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
        if self._pose_calibrated or self.pose_threshold is None:
            return
        if self._pose_calibration_errors is None:
            return
        if not forward_facing:
            return
        self._pose_calibration_errors.append(float(mse_loss))
        if len(self._pose_calibration_errors) < self._POSE_CALIBRATION_SAMPLES:
            return

        arr = np.array(self._pose_calibration_errors)
        median = float(np.median(arr))
        mad = float(np.median(np.abs(arr - median)))
        # 1.4826 scales MAD to a standard-deviation-equivalent; median + 3*MAD
        # ~= median + 3 sigma for Gaussian noise, without mean/std sensitivity.
        baseline = median + 3.0 * (1.4826 * mad) if mad > 1e-9 else median + 1.0
        low = self.pose_threshold * self._POSE_CALIBRATION_CLAMP[0]
        high = self.pose_threshold * self._POSE_CALIBRATION_CLAMP[1]
        self._pose_effective_threshold = float(np.clip(baseline, low, high))
        self._pose_calibrated = True
        self._pose_calibration_errors = None

    def _update_pose_abnormal_flag(self, abnormal_now: bool, now: float) -> bool:
        """Time-based persistence for the pose-anomaly flag.

        A pose must stay abnormal for _POSE_ABNORMAL_PERSIST_S before the flag
        turns on (single-frame solvePnP spikes are ignored) and must stay
        normal that long before it turns off (no flicker on brief recovery).
        Being time-based (not frame-based) keeps the verdict identical at any
        analysis frame rate.
        """
        if abnormal_now:
            if self._abnormal_since is None:
                self._abnormal_since = now
            self._normal_since = None
        else:
            self._abnormal_since = None
            if self._normal_since is None:
                self._normal_since = now

        if self._abnormal_since is not None and (
            now - self._abnormal_since
        ) >= self._POSE_ABNORMAL_PERSIST_S:
            self._pose_abnormal_flag = True
        elif self._normal_since is not None and (
            now - self._normal_since
        ) >= self._POSE_ABNORMAL_PERSIST_S:
            self._pose_abnormal_flag = False
        return self._pose_abnormal_flag

    def _smooth_direction(self, direction: str) -> str:
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
        if self._dir_last is None:
            self._dir_last = direction
            self._dir_streak = 1
            self._dir_output = direction
            return self._dir_output
        if direction == self._dir_last:
            self._dir_streak += 1
        else:
            self._dir_last = direction
            self._dir_streak = 1
        if self._dir_streak >= self._DIRECTION_PERSIST_FRAMES:
            self._dir_output = direction
        return self._dir_output

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
            result = self.face_mesh.detect(mp_image)
            if result and result.face_landmarks:
                landmarks = result.face_landmarks[0]
                return [{"x": l.x, "y": l.y, "z": l.z} for l in landmarks]
        except Exception:
            pass
        return []

    def analyze_frame(
        self, frame: np.ndarray, return_landmarks: bool = False
    ) -> dict[str, Any] | tuple[dict[str, Any], list]:
        """Analyze one frame; optionally also return the face landmarks.

        When `return_landmarks` is True, returns (result, landmarks) where the
        landmarks are the MediaPipe mesh for THIS frame, kept local to the call
        so the liveness analyzer can reuse the same inference pass. They must
        stay local: with per-meeting locks a concurrent meeting could otherwise
        overwrite a shared attribute between analyze_frame returning and the
        caller reading it, swapping one candidate's landmarks for another's.
        """
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
            self._offer_yolo_frame(frame)
        with self._yolo_lock:
            objects_detected = list(self.last_yolo_objects)
            yolo_conf = self._last_yolo_conf

        if self.face_mesh:
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
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
                if self._pose_smooth is None:
                    self._pose_smooth = (raw_pitch, raw_yaw, raw_roll)
                else:
                    alpha = 0.45
                    sp, sy, sr = self._pose_smooth
                    self._pose_smooth = (
                        alpha * raw_pitch + (1 - alpha) * sp,
                        alpha * raw_yaw + (1 - alpha) * sy,
                        alpha * raw_roll + (1 - alpha) * sr,
                    )
                pitch, yaw, roll = self._pose_smooth
                
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
                                mse_loss,
                                forward_facing=abs(pitch) < 10.0 and abs(yaw) < 10.0,
                            )
                            effective = (
                                self._pose_effective_threshold
                                if self._pose_effective_threshold is not None
                                else self.pose_threshold
                            )
                            abnormal_now = bool(mse_loss > effective)
                            # Time-based persistence: flag only after the pose
                            # stays abnormal for 2s (independent of frame rate).
                            pose_abnormal = self._update_pose_abnormal_flag(
                                abnormal_now, time.time()
                            )
                    except Exception as e:
                        print(f"[AdvancedGazeDetector] Pose autoencoder inference error: {e}")
                
                # Phase 1: Corneal Reflection Analysis (debounced)
                refl_now = self._analyze_corneal_reflection(frame, landmarks, width, height)
                if refl_now:
                    self._reflection_streak = min(5, self._reflection_streak + 1)
                else:
                    self._reflection_streak = max(0, self._reflection_streak - 1)
                reflection_detected = self._reflection_streak >= 2
                
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
                direction = self._smooth_direction(direction)
                engine = "mediapipe-tasks"
                self._last_face_time = time.time()
                self._last_face_direction = direction
                self._last_face_pose = (pitch, yaw, roll)
            else:
                # Face lost this frame: brief occlusions (< 2s) keep the last
                # known direction/pose so the UI does not flicker to NO_FACE;
                # faceDetected stays False so covering the camera is still
                # visible to the proctor immediately. Tradeoff: a real
                # camera-cover registers as a looking-away event after
                # grace (2s) + client dwell (~3s) ≈ 5s.
                local_landmarks = []
                self._pose_smooth = None
                if self._last_face_time and (time.time() - self._last_face_time) < 2.0:
                    direction = self._last_face_direction
                    pitch, yaw, roll = self._last_face_pose
                    engine = "mediapipe-tasks (grace)"
                else:
                    # Grace expired: clear the time-based pose clocks, the
                    # reflection streak and the direction-debounce history so a
                    # returning face starts clean instead of inheriting a
                    # half-built one or a stale direction.
                    self._abnormal_since = None
                    self._normal_since = None
                    self._pose_abnormal_flag = False
                    self._reflection_streak = 0
                    self._dir_last = None
                    self._dir_streak = 0

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
            # Enhanced face annotation with continuous monitoring labels
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
            if self._last_annotate_time == 0.0 or now - self._last_annotate_time >= 2.0:
                self._last_annotate_time = now
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

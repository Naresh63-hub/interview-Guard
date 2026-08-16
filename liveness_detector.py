"""
InterviewOS — Liveness Detection Module

Replaces the old brightness/sharpness placeholder with a blink-based model:

  - Eye Aspect Ratio (EAR) is computed from MediaPipe face landmarks
    (left eye: 159/145 vertical, 33/133 horizontal; right: 386/374, 362/263).
  - A "closure event" is recorded whenever a closed-eye sample is followed by
    an open-eye sample. Real humans blink every few seconds; a static photo or
    video replay of a screen produces no blink signature.
  - Brightness and sharpness are kept as auxiliary signals (very dark or
    heavily blurred frames remain suspicious).

The detector is stateless across HTTP requests only by design: it keeps a
small module-level rolling history of EAR samples + timestamps so the blink
signature accumulates over successive /liveness-frame calls.

Two complementary signals decide liveness:

  1. Blink signature — a "closure event" (closed-eye sample followed by an
     open-eye sample). Real humans blink every few seconds; a static photo
     or screen replay does not.
  2. Face-region motion — a real face is never pixel-still (breathing,
     micro-movements, blinking); a photo / replay is. Frames whose
     normalized mean-abs-difference stays below a tiny threshold for a
     sustained stretch are flagged as a static-face (presentation) attack.
"""

import math
import time
from collections import deque

import cv2
import numpy as np

# ─── Blink thresholds / timing ────────────────────────────────────────────────
_EAR_BLINK_THRESHOLD = 0.25   # below this the eye is considered closed
# A blink older than this no longer proves the feed is alive. This doubles as
# the "photo swap" window: a verified blinker who goes this long without a
# single blink (while still visible) is highly suspicious.
_BLINK_WINDOW_S = 90.0
# No blink EVER seen within this long (face present) = photo from session start.
_NO_BLINK_GRACE_S = 60.0
# A verified blinker who then goes this long without a single blink on a
# face that is NOT pixel-still (photo held with slight shake / frozen person).
# The pixel-still case is owned by the motion detector; this catches the
# "visible but blinking stopped" edge. Must stay == _BLINK_WINDOW_S: if the
# gap threshold ever exceeded the alive window, a 90s+ gap would still match
# recent_blinks and the swap alarm would silently never fire.
_NO_RECENT_BLINK_S = _BLINK_WINDOW_S
# A swap alarm is only armed once a SUSTAINED blink pattern is proven (>=2
# caught blinks in the lookback). A single caught blink could be a slow
# blinker whose next catch is minutes away — arming on one would false-
# positive on present-and-moving candidates.
_SWAP_ARM_LOOKBACK_S = _BLINK_WINDOW_S * 2
_DARK_BRIGHTNESS = 20.0
_BLUR_SHARPNESS = 40.0        # Laplacian variance below this = blurred

# ─── Static-face (photo / replay) detection ───────────────────────────────────
# Frames are captured at 320x240 JPEG q0.72, so two encodes of the SAME static
# scene differ by a tiny, bounded amount. A real face moves far more.
_STATIC_MOTION_THRESHOLD = 0.012   # normalized mean-abs-diff below = static
_STATIC_FACE_MIN_DURATION_S = 30.0 # wait for camera AGC / auto-exposure to settle
_STATIC_FACE_GRACE_S = 20.0        # static face for this long = presentation attack
_FACE_CROP_SIZE = 48               # resized face-region crop for diffing

# ─── Liveness challenge (random prompt / response) ────────────────────────────
# A photo or screen replay cannot react to a prompt on cue: occasionally the
# candidate is asked (subtly, on their own screen) to blink or turn their head,
# and the response must arrive AFTER the prompt within a short window. Events
# in the first CHALLENGE_REACT_S are ignored so a pre-prompt blink does not
# count.
CHALLENGE_REACT_S = 1.5            # response must start after the prompt shows
CHALLENGE_WINDOW_S = 6.0           # …and arrive before this deadline

challenge_state = {
    "active": False,
    "type": None,                  # "blink" | "head"
    "issued_at": 0.0,
    "deadline": 0.0,
    "status": "idle",             # idle | pending | passed | failed
}


def reset_challenge():
    """Clear challenge state (session reset / tests)."""
    challenge_state.update(
        {
            "active": False,
            "type": None,
            "issued_at": 0.0,
            "deadline": 0.0,
            "status": "idle",
        }
    )


def issue_challenge(challenge_type: str) -> dict:
    """Start a liveness challenge. Returns {type, expiresAt} or an error."""
    if challenge_type not in ("blink", "head"):
        return {"error": "unknown challenge type", "type": challenge_type}
    now = time.time()
    challenge_state.update(
        {
            "active": True,
            "type": challenge_type,
            "issued_at": now,
            "deadline": now + CHALLENGE_WINDOW_S,
            "status": "pending",
        }
    )
    return {"type": challenge_type, "expiresAt": challenge_state["deadline"]}

# ─── Rolling liveness state (across /liveness-frame calls) ────────────────────
_LIVENESS_STATE = {
    "ear_samples": deque(maxlen=40),    # (timestamp, ear)
    "closure_events": deque(maxlen=40), # timestamps of closed->open transitions
    "last_ear_bin": None,               # "closed" | "open" | None
    "face_seen_since": None,            # timestamp when a face was first seen
    "last_face_at": 0.0,                # timestamp of the last frame with a face
    "last_blink_at": None,              # timestamp of the most recent blink
    "face_crop_prev": None,             # last normalized gray face crop
    "face_static_since": None,          # when the current static period began
    "challenge_motion_at": 0.0,         # head-move evidence during a challenge
}

# MediaPipe face landmark indices for the eyes
_LEFT_EYE_V = (159, 145)
_LEFT_EYE_H = (33, 133)
_RIGHT_EYE_V = (386, 374)
_RIGHT_EYE_H = (362, 263)


def _reset_liveness_state():
    """Clear all accumulated liveness state (used by tests / session reset)."""
    _LIVENESS_STATE["ear_samples"].clear()
    _LIVENESS_STATE["closure_events"].clear()
    _LIVENESS_STATE["last_ear_bin"] = None
    _LIVENESS_STATE["face_seen_since"] = None
    _LIVENESS_STATE["last_face_at"] = 0.0
    _LIVENESS_STATE["last_blink_at"] = None
    _LIVENESS_STATE["face_crop_prev"] = None
    _LIVENESS_STATE["face_static_since"] = None
    _LIVENESS_STATE["challenge_motion_at"] = 0.0
    reset_challenge()


def _coord(landmark, key):
    """Read a coordinate from either a dict or an object with attributes."""
    if isinstance(landmark, dict):
        return landmark.get(key, 0.0)
    return getattr(landmark, key, 0.0)


def compute_ear(landmarks):
    """Eye Aspect Ratio from MediaPipe face landmarks (list of x/y dicts)."""
    try:
        def dist(a, b):
            ax, ay = _coord(landmarks[a], "x"), _coord(landmarks[a], "y")
            bx, by = _coord(landmarks[b], "x"), _coord(landmarks[b], "y")
            return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5

        left = dist(*_LEFT_EYE_V) / max(dist(*_LEFT_EYE_H), 1e-9)
        right = dist(*_RIGHT_EYE_V) / max(dist(*_RIGHT_EYE_H), 1e-9)
        return float((left + right) / 2.0)
    except Exception:
        return None


def _update_blink_state(ear, now):
    """Record a closure event when a closed-eye sample recovers to open."""
    st = _LIVENESS_STATE
    if ear is None:
        st["last_ear_bin"] = None
        return

    st["ear_samples"].append((now, ear))
    bin_now = "closed" if ear < _EAR_BLINK_THRESHOLD else "open"
    if st["last_ear_bin"] == "closed" and bin_now == "open":
        st["closure_events"].append(now)
        st["last_blink_at"] = now
        # EAR proves the person is alive regardless of pixel change — a blink
        # breaks the static-face clock even when the eye region is too small
        # for the motion diff to notice (distant / small face).
        st["face_static_since"] = None
        st["face_crop_prev"] = None
    st["last_ear_bin"] = bin_now


def _face_crop_gray(frame, landmarks):
    """Normalized gray crop of the face region for frame-difference motion.

    The crop is resized to a fixed square and divided by its own mean so the
    comparison is robust to slow camera AGC / exposure gain drift (which would
    otherwise make even a static photo look "moving"). Returns None when there
    is no usable face region.
    """
    if frame is None or not landmarks or len(landmarks) < 400:
        return None
    xs, ys = [], []
    try:
        for l in landmarks:
            try:
                x, y = float(l["x"]), float(l["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(x) and math.isfinite(y):
                xs.append(x)
                ys.append(y)
    except Exception:
        return None
    # MediaPipe can emit the odd non-finite / out-of-range point; never let a
    # single bad frame crash the endpoint. Need a sane spread to crop.
    if len(xs) < 20 or len(ys) < 20:
        return None
    h, w = frame.shape[:2]
    margin = 0.03
    x0 = max(0, int((min(xs) - margin) * w))
    x1 = min(w, int((max(xs) + margin) * w))
    y0 = max(0, int((min(ys) - margin) * h))
    y1 = min(h, int((max(ys) + margin) * h))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    crop = frame[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (_FACE_CROP_SIZE, _FACE_CROP_SIZE))
    m = float(gray.mean())
    if m < 1e-6:
        return None
    return gray.astype(np.float32) / m


def analyze_liveness(frame, landmarks=None):
    """Return a liveness verdict for a decoded webcam frame.

    Args:
        frame: BGR image from the candidate's webcam.
        landmarks: MediaPipe face landmarks (list of {x, y, z} dicts) for the
            frame, or None/[] when no face was detected.
    """
    now = time.time()
    if frame is None:
        return {
            "is_live": False,
            "confidence": 0.0,
            "risk": "high",
            "reason": "no_frame",
            "timestamp": now,
        }

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    st = _LIVENESS_STATE
    has_face = bool(landmarks) and len(landmarks) > 400  # MediaPipe mesh = 478 pts
    ear = None

    if has_face:
        st["last_face_at"] = now
        if st["face_seen_since"] is None:
            st["face_seen_since"] = now
        ear = compute_ear(landmarks)
        _update_blink_state(ear, now)
        # Frame-difference motion: a photo / replay is pixel-still; a real
        # face is not. Track the age of the current static period.
        try:
            crop = _face_crop_gray(frame, landmarks)
            if crop is not None:
                if st["face_crop_prev"] is not None:
                    diff = float(np.abs(crop - st["face_crop_prev"]).mean())
                    if diff < _STATIC_MOTION_THRESHOLD:
                        if st["face_static_since"] is None:
                            st["face_static_since"] = now
                    else:
                        st["face_static_since"] = None
                        # Head-move evidence for an active head challenge
                        if (
                            challenge_state["active"]
                            and challenge_state["type"] == "head"
                            and now >= challenge_state["issued_at"] + CHALLENGE_REACT_S
                        ):
                            st["challenge_motion_at"] = now
                st["face_crop_prev"] = crop
        except Exception:
            # Never let a bad crop take down the liveness endpoint.
            pass
    else:
        st["last_ear_bin"] = None
        st["face_crop_prev"] = None
        st["face_static_since"] = None

    # Prune stale blink evidence
    recent_blinks = [t for t in st["closure_events"] if now - t <= _BLINK_WINDOW_S]
    face_duration = (now - st["face_seen_since"]) if st["face_seen_since"] else 0.0

    # ─── Liveness challenge evaluation ───────────────────────────────────────
    # Mirror the current state so a resolved verdict (passed/failed) keeps
    # being reported until the next challenge is issued.
    ch = challenge_state
    challenge_info = {"active": ch["active"], "type": ch["type"], "status": ch["status"]}
    if ch["active"] and ch["status"] == "pending":
        respond_by = ch["issued_at"] + CHALLENGE_REACT_S
        if now > ch["deadline"]:
            # Deadline reached. Only a candidate who was actually VISIBLE during
            # the window (face present, no response) is a failure. If no face
            # was seen at all the check is inconclusive → "expired", which the
            # frontend must not count against the candidate.
            if st.get("last_face_at", 0.0) >= ch["issued_at"]:
                ch["status"] = "failed"
            else:
                ch["status"] = "expired"
            ch["active"] = False
        elif has_face and now >= respond_by:
            if ch["type"] == "blink":
                # A fresh blink AFTER the prompt (pre-prompt blinks are old).
                if any(respond_by <= t <= ch["deadline"] for t in st["closure_events"]):
                    ch["status"] = "passed"
            elif ch["type"] == "head":
                if st.get("challenge_motion_at", 0.0) >= respond_by:
                    ch["status"] = "passed"
        challenge_info = {"active": ch["active"], "type": ch["type"], "status": ch["status"]}

    base = {
        "brightness": brightness,
        "sharpness": sharpness,
        "ear": ear,
        "blinkDetected": len(recent_blinks) > 0,
        "faceDetected": has_face,
        "challenge": challenge_info,
        "timestamp": now,
    }

    if not has_face:
        if brightness < _DARK_BRIGHTNESS:
            return {
                **base,
                "is_live": False,
                "confidence": 0.35,
                "risk": "high",
                "reason": "frame_too_dark",
            }
        return {
            **base,
            "is_live": False,
            "confidence": 0.45,
            "risk": "medium",
            "reason": "face_not_detected",
        }

    # Presentation attack: the face region stayed pixel-still for a sustained
    # stretch (photo / screen replay). Blinking IS motion, so any blink caught
    # during the window implicitly clears this condition.
    static_since = st["face_static_since"]
    if (
        static_since is not None
        and face_duration >= _STATIC_FACE_MIN_DURATION_S
        and (now - static_since) >= _STATIC_FACE_GRACE_S
    ):
        return {
            **base,
            "is_live": False,
            "confidence": 0.85,
            "risk": "high",
            "reason": "static_face",
            "faceDurationSec": round(face_duration, 1),
        }

    # Photo-swap safety net: only for candidates with a PROVEN sustained blink
    # pattern (>=2 caught blinks recently) whose blinks then stop for a long
    # stretch while they stay visible — a photo held with slight shake, or a
    # frozen person. Static feeds are owned by static_face above; slow-but-
    # present blinkers (whose blinks are rarely sampled) never arm this.
    blinks_recent = [t for t in st["closure_events"] if now - t <= _SWAP_ARM_LOOKBACK_S]
    last_blink_age = (now - st["last_blink_at"]) if st["last_blink_at"] else None
    if len(blinks_recent) >= 2 and last_blink_age is not None and last_blink_age > _NO_RECENT_BLINK_S:
        return {
            **base,
            "is_live": False,
            "confidence": 0.70,
            "risk": "high",
            "reason": "no_recent_blink",
            "blinkAgeSec": round(last_blink_age, 1),
        }

    if recent_blinks:
        return {
            **base,
            "is_live": True,
            "confidence": 0.90,
            "risk": "low",
            "reason": "blink_detected",
            "blinkCount": len(recent_blinks),
        }

    if face_duration < _NO_BLINK_GRACE_S:
        # Still calibrating — do not flag yet (sampling every ~1.5s means the
        # first blink may not be caught for a while).
        return {
            **base,
            "is_live": True,
            "confidence": 0.50,
            "risk": "medium",
            "reason": "awaiting_blink",
            "faceDurationSec": round(face_duration, 1),
        }

    # Face present for 60s+ with zero blink evidence ever: likely a photo or a
    # screen replay (real humans blink every few seconds).
    if sharpness < _BLUR_SHARPNESS:
        reason = "blurred_static_frame"
    else:
        reason = "no_blink_detected"
    return {
        **base,
        "is_live": False,
        "confidence": 0.80,
        "risk": "high",
        "reason": reason,
        "faceDurationSec": round(face_duration, 1),
    }

import time

import cv2


def analyze_liveness(frame):
    """Return a lightweight placeholder liveness result for a decoded webcam frame."""
    if frame is None:
        return {
            "is_live": False,
            "confidence": 0.0,
            "risk": "high",
            "reason": "no_frame",
            "timestamp": time.time(),
        }

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    if brightness < 20:
        return {
            "is_live": False,
            "confidence": 0.35,
            "risk": "medium",
            "reason": "frame_too_dark",
            "brightness": brightness,
            "sharpness": sharpness,
            "timestamp": time.time(),
        }

    return {
        "is_live": True,
        "confidence": 0.65,
        "risk": "low",
        "reason": "placeholder_liveness_model",
        "brightness": brightness,
        "sharpness": sharpness,
        "timestamp": time.time(),
    }

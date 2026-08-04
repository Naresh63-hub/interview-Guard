import time

meetings = []
active_participants = {}
meeting_rooms = {}
session_started_at = None
SESSION_TIMEOUT_SECONDS = 15
calibration_samples = []

current_gaze = {
    "direction": "WAITING",
    "lookingAway": False,
    "faceDetected": False,
    "timestamp": time.time(),
}


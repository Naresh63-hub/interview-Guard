import time
from database import db

# Legacy in-memory state (used as fallback if MongoDB unavailable)
meetings = []
active_participants = {}
meeting_rooms = {}
session_started_at = None
SESSION_TIMEOUT_SECONDS = 300
calibration_samples = []

current_gaze = {
    "direction": "WAITING",
    "lookingAway": False,
    "faceDetected": False,
    "timestamp": time.time(),
}

# MongoDB database instance
try:
    db.connect()
    print("[State] MongoDB connected successfully")
except Exception as e:
    print(f"[State] MongoDB connection failed, using in-memory fallback: {e}")

def get_meeting_room(meeting_id):
    """Get meeting room from MongoDB or fallback to in-memory."""
    if db.connected:
        room = db.get_meeting_room(meeting_id)
        if room:
            # Convert MongoDB structure to match expected structure
            room_id = meeting_id.upper()
            standardized_room = {
                "id": room_id,
                "title": room.get("title", ""),
                "host": room.get("host", ""),
                "createdAt": room.get("created_at", time.time()),
                "participants": room.get("participants", []),
                "status": room.get("status", "waiting"),
                "metadata": room.get("metadata", {}),
                "proctoringSettings": room.get("proctoring_settings", {})
            }
            # Sync to in-memory state for performance
            meeting_rooms[room_id] = standardized_room
            return standardized_room
    return meeting_rooms.get(meeting_id.upper())

def get_latest_meeting_for_user(user_id):
    """Get the host's most recently created meeting room (Mongo only)."""
    if not db.connected or not user_id:
        return None
    room = db.get_latest_meeting_for_user(user_id)
    if not room:
        return None
    room_id = room.get("meeting_id", "").upper()
    return {
        "id": room_id,
        "title": room.get("title", ""),
        "host": room.get("host", ""),
        "createdAt": room.get("created_at", time.time()),
        "participants": room.get("participants", []),
        "status": room.get("status", "waiting"),
        "metadata": room.get("metadata", {}),
        "proctoringSettings": room.get("proctoring_settings", {})
    }

def create_meeting_room(meeting_id, host, title, metadata=None):
    """Create meeting room in MongoDB and sync to in-memory."""
    if db.connected:
        success = db.create_meeting_room(meeting_id, host, title, metadata)
        if success:
            room = db.get_meeting_room(meeting_id)
            if room:
                # Convert MongoDB structure to match expected structure
                room_id = meeting_id.upper()
                meeting_rooms[room_id] = {
                    "id": room_id,
                    "title": room.get("title", title),
                    "host": room.get("host", host),
                    "createdAt": room.get("created_at", time.time()),
                    "participants": room.get("participants", []),
                    "status": room.get("status", "waiting"),
                    "metadata": room.get("metadata", metadata or {}),
                    "proctoringSettings": room.get("proctoring_settings", {})
                }
                return meeting_rooms[room_id]
    # Fallback to in-memory
    meeting_id = meeting_id.upper()
    meeting_rooms[meeting_id] = {
        "id": meeting_id,
        "title": title,
        "host": host,
        "createdAt": time.time(),
        "participants": [],
        "status": "waiting",
        "metadata": metadata or {}
    }
    return meeting_rooms[meeting_id]

def add_participant(meeting_id, user_id, user_name, role, socket_id=None):
    """Add participant to MongoDB and sync to in-memory."""
    if db.connected:
        db.add_participant(meeting_id, user_id, user_name, role, socket_id)
    # Sync to in-memory (dedupe: sockets.py already appends a `socketId`
    # entry, so never create a second row for the same socket).
    room = get_meeting_room(meeting_id.upper())
    if room:
        participant = {
            "user_id": user_id,
            "user_name": user_name,
            "role": role,
            "socketId": socket_id,
            "joinedAt": time.time()
        }
        existing = [
            p for p in room["participants"]
            if (p.get("socketId") or p.get("socket_id")) == socket_id
        ]
        if not existing:
            room["participants"].append(participant)

def update_participant_activity(meeting_id, user_id):
    """Update participant activity in MongoDB."""
    if db.connected:
        db.update_participant_activity(meeting_id, user_id)

def remove_participant(meeting_id, user_id):
    """Remove participant from MongoDB and sync to in-memory."""
    if db.connected:
        db.remove_participant(meeting_id, user_id)
    # Sync to in-memory
    room = get_meeting_room(meeting_id.upper())
    if room:
        room["participants"] = [p for p in room["participants"] if p.get("user_id") != user_id]

def add_audit_log(meeting_id, event_type, title, message, confidence="", is_critical=False, metadata=None):
    """Add audit log to MongoDB."""
    if db.connected:
        db.add_audit_log(meeting_id, event_type, title, message, confidence, is_critical, metadata)

def start_session(meeting_id):
    """Start session in MongoDB."""
    global session_started_at
    if db.connected:
        db.create_meeting_session(meeting_id)
    session_started_at = time.time()

def end_session(meeting_id):
    """End session in MongoDB."""
    global session_started_at
    if db.connected:
        db.end_session(meeting_id)
    session_started_at = None


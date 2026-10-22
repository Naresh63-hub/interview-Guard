import time
from collections import deque
from database import db

# In-memory state (fallback when the database is unavailable)
meetings = []
active_participants = {}
meeting_rooms = {}
session_started_at = {}
SESSION_TIMEOUT_SECONDS = 300
# Bounded: one sample per calibration target click (plus client retries)
# would otherwise grow this list for the lifetime of the process.
calibration_samples = deque(maxlen=200)

# Latest gaze verdict per meeting: {meeting_id: analyze_frame result}. The
# /gaze and /gaze-frame routes both index this by meeting id; a flat
# single-meeting dict here used to make /gaze-frame wipe every other
# meeting's live state on each request.
current_gaze = {}

# Database instance
try:
    if db.connect():
        print("[State] Supabase connected successfully")
    else:
        print("[State] Supabase connection failed, using in-memory fallback")
except Exception as e:
    print(f"[State] Supabase connection error: {e}")

def get_meeting_room(meeting_id):
    """Get meeting room from Supabase or fall back to in-memory."""
    if db.connected:
        room = db.get_meeting_room(meeting_id)
        if room:
            # Convert the Supabase row to the expected structure
            room_id = meeting_id.upper()
            existing_room = meeting_rooms.get(room_id, {})
            standardized_room = {
                "id": room_id,
                "title": room.get("title", ""),
                "host": room.get("host", ""),
                "createdAt": room.get("created_at", time.time()),
                "participants": existing_room.get("participants", []),
                "status": room.get("status", "waiting"),
                "metadata": room.get("metadata", {}),
                "proctoringSettings": room.get("proctoring_settings", {})
            }
            # Sync to in-memory state for performance
            meeting_rooms[room_id] = standardized_room
            return standardized_room
    return meeting_rooms.get(meeting_id.upper())

def get_latest_meeting_for_user(user_id):
    """Get the host's most recently created meeting room (Supabase only)."""
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
    """Create meeting room in Supabase and sync to in-memory."""
    if db.connected:
        success = db.create_meeting_room(meeting_id, host, title, metadata)
        if success:
            room = db.get_meeting_room(meeting_id)
            if room:
                # Convert the Supabase row to the expected structure
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
    """Add participant to Supabase and sync to in-memory."""
    if db.connected:
        db.add_participant(meeting_id, user_id, user_name, role, socket_id)
    # Sync to in-memory (dedupe by BOTH user_id and socket_id: matching on
    # socket_id alone made two None-socket participants collide, silently
    # dropping the second person from the room).
    room = get_meeting_room(meeting_id.upper())
    if room:
        participant = {
            "user_id": user_id,
            "user_name": user_name,
            "userName": user_name,
            "role": role,
            "socketId": socket_id,
            "joinedAt": time.time()
        }
        already_present = any(
            (socket_id and (p.get("socketId") or p.get("socket_id")) == socket_id)
            or p.get("user_id") == user_id
            for p in room["participants"]
        )
        if not already_present:
            room["participants"].append(participant)

def update_participant_activity(meeting_id, user_id):
    """Update participant activity in Supabase."""
    if db.connected:
        db.update_participant_activity(meeting_id, user_id)

def remove_participant(meeting_id, user_id):
    """Remove participant from Supabase and sync to in-memory."""
    if db.connected:
        db.remove_participant(meeting_id, user_id)
    # Sync to in-memory. Match user_id OR socket id: entries appended
    # straight from sockets.py carry no user_id, so the old exact user_id
    # check left those rows in the room forever (stale ghost participants
    # survived every join/leave cycle).
    room = get_meeting_room(meeting_id.upper())
    if room:
        room["participants"] = [
            p for p in room["participants"]
            if p.get("user_id") != user_id
            and (p.get("socketId") or p.get("socket_id")) != user_id
        ]

def add_audit_log(meeting_id, event_type, title, message, confidence=None, is_critical=False, metadata=None):
    """Add audit log to Supabase."""
    if db.connected:
        db.add_audit_log(meeting_id, event_type, title, message, confidence, is_critical, metadata)

def start_session(meeting_id):
    """Start session in Supabase."""
    if db.connected:
        db.create_meeting_session(meeting_id)
    session_started_at[meeting_id] = time.time()

def end_session(meeting_id):
    """End session in Supabase."""
    if db.connected:
        db.end_session(meeting_id)
    session_started_at.pop(meeting_id, None)


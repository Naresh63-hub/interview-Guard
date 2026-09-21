import time
from flask import request
from flask_socketio import join_room as sio_join
from flask_socketio import leave_room as sio_leave
from flask_socketio import emit
import state
from database import db

def register_sockets(socketio):
    def _socket_key(participant):
        """Return the participant's socket id regardless of key casing.

        In-memory participants were appended with ``socketId`` while Supabase
        persisted them under ``socket_id``; read both so a room re-fetched
        from the database authorizes the same socket that joined it.
        """
        return participant.get("socketId") or participant.get("socket_id")

    def _participant_role(meeting_id):
        room = state.get_meeting_room((meeting_id or "").upper())
        if not room:
            return None

        for participant in room.get("participants", []):
            if _socket_key(participant) == request.sid:
                return participant.get("role")
        return None

    def _is_candidate_socket(meeting_id):
        return _participant_role(meeting_id) == "candidate"

    def _is_host_socket(meeting_id):
        return _participant_role(meeting_id) in ("interviewer", "host", "admin")

    @socketio.on("join_meeting")
    def on_join_meeting(data):
        meeting_id = (data.get("meetingId") or "").upper()
        user_name = (data.get("userName") or "User").strip()
        role = (data.get("role") or "participant").strip()
        room = state.get_meeting_room(meeting_id)
        if not room:
            emit("error", {"message": "Room not found"})
            return
            
        from flask import session
        if role == "interviewer":
            if not session.get('host_authenticated'):
                emit("error", {"message": "Unauthorized: Host session invalid"})
                return
        elif role == "candidate":
            if session.get('candidate_verified_meeting') != meeting_id:
                emit("error", {"message": "Unauthorized: Candidate session invalid"})
                return
        else:
            emit("error", {"message": "Unauthorized: Invalid role"})
            return

        sio_join(meeting_id)
        existing = [p for p in room["participants"] if _socket_key(p) == request.sid]
        if not existing:
            room["participants"].append(
                {
                    "socketId": request.sid,
                    "userName": user_name,
                    "role": role,
                    "joinedAt": time.time(),
                }
            )
            
            # Add participant to MongoDB for persistence
            user_id = f"{role}_{user_name}_{meeting_id}"
            state.add_participant(meeting_id, user_id, user_name, role, request.sid)
        # Initialize room settings if they do not exist yet
        if "proctoringSettings" not in room:
            room["proctoringSettings"] = {
                "gazeSensitivity": "medium",
                "allowedTabSwitches": 3,
                "gazeCheck": True,
                "audioCheck": True,
                "vmCheck": True,
                "dualMonitorCheck": True,
                "devToolsCheck": True,
                "clipboardCheck": True
            }

        emit(
            "user_joined",
            {
                "socketId": request.sid,
                "userName": user_name,
                "role": role,
            },
            to=meeting_id,
            skip_sid=request.sid,
        )
        emit(
            "room_info",
            {
                "meetingId": meeting_id,
                "title": room["title"],
                "host": room["host"],
                "proctoringSettings": room["proctoringSettings"],
                "networkStats": room.get("networkStats"),
                "participants": [
                    {
                        "socketId": _socket_key(p),
                        "userName": p["userName"],
                        "role": p["role"],
                    }
                    for p in room["participants"]
                ],
            },
            to=meeting_id,
        )
        
        # Emit session status update to all participants when someone joins
        participant_count = len(room["participants"])
        both_present = any(p["role"] == "interviewer" for p in room["participants"]) and \
                       any(p["role"] == "candidate" for p in room["participants"])
        
        emit(
            "session_status",
            {
                "bothPresent": both_present,
                "participantCount": participant_count,
                "timestamp": time.time(),
            },
            to=meeting_id,
        )

    @socketio.on("offer")
    def on_offer(data):
        from flask_socketio import rooms
        if len(rooms(request.sid)) <= 1: return
        emit(
            "offer",
            {"sdp": data.get("sdp"), "from": request.sid},
            to=data.get("to"),
        )

    @socketio.on("answer")
    def on_answer(data):
        from flask_socketio import rooms
        if len(rooms(request.sid)) <= 1: return
        emit(
            "answer",
            {"sdp": data.get("sdp"), "from": request.sid},
            to=data.get("to"),
        )

    @socketio.on("ice_candidate")
    def on_ice_candidate(data):
        from flask_socketio import rooms
        if len(rooms(request.sid)) <= 1: return
        emit(
            "ice_candidate",
            {"candidate": data.get("candidate"), "from": request.sid},
            to=data.get("to"),
        )

    @socketio.on("leave_meeting")
    def on_leave_meeting(data):
        meeting_id = (data.get("meetingId") or "").upper()
        room = state.get_meeting_room(meeting_id)
        if room:
            room["participants"] = [
                p for p in room["participants"] if _socket_key(p) != request.sid
            ]
            emit("user_left", {"socketId": request.sid}, to=meeting_id)
        sio_leave(meeting_id)

    @socketio.on("chat_message")
    def on_chat_message(data):
        meeting_id = (data.get("meetingId") or "").upper()
        if not _participant_role(meeting_id): return
        emit(
            "chat_message",
            {
                "sender": data.get("sender", "User"),
                "message": data.get("message", ""),
                "timestamp": time.time(),
            },
            to=meeting_id,
        )

    @socketio.on("audit_event")
    def on_audit_event(data):
        meeting_id = (data.get("meetingId") or "").upper()
        if not _is_candidate_socket(meeting_id):
            return
        emit(
            "audit_event",
            {
                "title": data.get("title", "Event"),
                "message": data.get("message", ""),
                "confidence": data.get("confidence", ""),
                "isCritical": data.get("isCritical", False),
                "timestamp": time.time(),
            },
            to=meeting_id,
        )

    @socketio.on("badge_update")
    def on_badge_update(data):
        meeting_id = (data.get("meetingId") or "").upper()
        if not _is_candidate_socket(meeting_id):
            return
        emit("badge_update", data, to=meeting_id, skip_sid=request.sid)

    @socketio.on("gaze_update")
    def on_gaze_update(data):
        meeting_id = (data.get("meetingId") or "").upper()
        if not _is_candidate_socket(meeting_id):
            return
        emit("gaze_update", data, to=meeting_id, skip_sid=request.sid)

    @socketio.on("settings_update")
    def on_settings_update(data):
        meeting_id = (data.get("meetingId") or "").upper()
        # Only the host/interviewer may change proctoring configuration.
        if not _is_host_socket(meeting_id):
            return
        room = state.get_meeting_room(meeting_id)
        if room and "settings" in data:
            room["proctoringSettings"] = data["settings"]
        emit("settings_update", data, to=meeting_id, skip_sid=request.sid)

    @socketio.on("remote_control")
    def on_remote_control(data):
        meeting_id = (data.get("meetingId") or "").upper()
        # Remote mute/camera control is a moderator capability.
        if not _is_host_socket(meeting_id):
            return
        emit("remote_control", data, to=meeting_id, skip_sid=request.sid)

    @socketio.on("browser_stats_update")
    def on_browser_stats_update(data):
        meeting_id = (data.get("meetingId") or "").upper()
        if not _is_candidate_socket(meeting_id):
            return
        emit("browser_stats_update", data, to=meeting_id, skip_sid=request.sid)

    @socketio.on("media_state_change")
    def on_media_state_change(data):
        meeting_id = (data.get("meetingId") or "").upper()
        # Anyone in the meeting may report their own media state, but an
        # outsider with no room membership must not be able to broadcast.
        if _participant_role(meeting_id) is None:
            return
        emit("media_state_change", data, to=meeting_id, skip_sid=request.sid)

    @socketio.on("audio_metrics_update")
    def on_audio_metrics_update(data):
        meeting_id = (data.get("meetingId") or "").upper()
        if not _is_candidate_socket(meeting_id):
            return
        emit("audio_metrics_update", data, to=meeting_id, skip_sid=request.sid)

    @socketio.on("network_stats_update")
    def on_network_stats_update(data):
        meeting_id = (data.get("meetingId") or "").upper()
        if not _is_candidate_socket(meeting_id):
            return
        room = state.get_meeting_room(meeting_id)
        if room:
            room["networkStats"] = {
                "isp": data.get("isp"),
                "location": data.get("location"),
                "isVpnOrProxy": data.get("isVpnOrProxy")
            }
        emit("network_stats_update", data, to=meeting_id, skip_sid=request.sid)

    @socketio.on("disconnect")
    def on_sio_disconnect():
        for meeting_id, room in list(state.meeting_rooms.items()):
            for p in room["participants"]:
                if _socket_key(p) == request.sid:
                    room["participants"] = [
                        x for x in room["participants"] if _socket_key(x) != request.sid
                    ]
                    emit("user_left", {"socketId": request.sid}, to=meeting_id)
                    sio_leave(meeting_id)
                    break

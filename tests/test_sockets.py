"""Socket.IO gating tests: join_meeting authorizes against the server-side
session before admitting anyone to a room. An unauthenticated socket must be
refused (never added to the participant list), regardless of the role it claims.
"""
import time

import pytest


@pytest.fixture()
def sio_room(flask_app):
    """Create a real room in the in-memory state so join reaches the auth gate
    (join_meeting rejects unknown rooms before it ever checks credentials)."""
    import state

    meeting_id = f"SIO{int(time.time() * 1000) % 1000000}"
    state.create_meeting_room(meeting_id, "Host", "Socket Test Room", {})
    yield meeting_id
    state.meeting_rooms.pop(meeting_id, None)


def test_join_refuses_unauthenticated_interviewer(flask_app, sio_room):
    import app as app_module
    import state

    client = app_module.socketio.test_client(flask_app)
    client.emit(
        "join_meeting",
        {"meetingId": sio_room, "userName": "Mallory", "role": "interviewer"},
    )
    received = client.get_received()
    assert any(m["name"] == "error" for m in received), received

    # Never admitted: the room's participant list is still empty of this socket.
    room = state.get_meeting_room(sio_room)
    assert all(
        (p.get("socketId") or p.get("socket_id")) != client.sid
        for p in room.get("participants", [])
    )


def test_join_refuses_invalid_role(flask_app, sio_room):
    import app as app_module

    client = app_module.socketio.test_client(flask_app)
    client.emit(
        "join_meeting",
        {"meetingId": sio_room, "userName": "Eve", "role": "attacker"},
    )
    received = client.get_received()
    assert any(
        m["name"] == "error" and "role" in m["args"][0].get("message", "").lower()
        for m in received
    ), received


def test_join_unknown_room_errors(flask_app):
    import app as app_module

    client = app_module.socketio.test_client(flask_app)
    client.emit(
        "join_meeting",
        {"meetingId": "NOSUCHROOM", "userName": "Zed", "role": "candidate"},
    )
    received = client.get_received()
    assert any(m["name"] == "error" for m in received), received

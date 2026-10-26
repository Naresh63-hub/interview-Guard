"""Socket.IO gating tests: join_meeting authorizes against the server-side
capability token before admitting anyone to a room. An unauthenticated socket must be
refused (never added to the participant list), regardless of the role it claims.
"""
import time

import pytest


@pytest.fixture()
def sio_room(flask_app):
    """Create a real room in the in-memory state so join reaches the auth gate.
    Returns (meeting_id, host_token)."""
    import state

    meeting_id = f"SIO{int(time.time() * 1000) % 1000000}"
    room = state.create_meeting_room(meeting_id, "Host", "Socket Test Room", {})
    token = room.get("hostToken", "")
    yield meeting_id, token
    state.meeting_rooms.pop(meeting_id, None)


def test_join_refuses_interviewer_without_token(flask_app, sio_room):
    """No hostToken in the join payload -> rejected."""
    import app as app_module
    import state

    meeting_id, token = sio_room
    client = app_module.socketio.test_client(flask_app)
    client.emit(
        "join_meeting",
        {"meetingId": meeting_id, "userName": "Mallory", "role": "interviewer"},
    )
    received = client.get_received()
    assert any(m["name"] == "error" for m in received), received

    room = state.get_meeting_room(meeting_id)
    assert all(
        (p.get("socketId") or p.get("socket_id")) != client.eio_sid
        for p in room.get("participants", [])
    )


def test_join_refuses_interviewer_with_wrong_token(flask_app, sio_room):
    """Invalid hostToken -> rejected."""
    import app as app_module
    import state

    meeting_id, _ = sio_room
    client = app_module.socketio.test_client(flask_app)
    client.emit(
        "join_meeting",
        {"meetingId": meeting_id, "userName": "Mallory", "role": "interviewer",
         "hostToken": "wrong-token-value"},
    )
    received = client.get_received()
    assert any(
        m["name"] == "error" and "capability token" in str(m["args"]).lower()
        for m in received
    ), received

    room = state.get_meeting_room(meeting_id)
    assert not any(
        p.get("role") == "interviewer" for p in room.get("participants", [])
    )


def test_join_refuses_invalid_role(flask_app, sio_room):
    import app as app_module

    meeting_id, _ = sio_room
    client = app_module.socketio.test_client(flask_app)
    client.emit(
        "join_meeting",
        {"meetingId": meeting_id, "userName": "Eve", "role": "attacker"},
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


def test_join_with_valid_host_token(flask_app, sio_room):
    """Correct capability token -> interviewer admitted."""
    import app as app_module
    import state

    meeting_id, token = sio_room
    client = app_module.socketio.test_client(flask_app)
    client.emit(
        "join_meeting",
        {"meetingId": meeting_id, "userName": "Host", "role": "interviewer",
         "hostToken": token},
    )
    received = client.get_received()
    # Should get room_info, not error
    assert any(m["name"] == "room_info" for m in received), received

    room = state.get_meeting_room(meeting_id)
    # Verify interviewer participant was added
    assert any(p.get("role") == "interviewer" for p in room.get("participants", []))


def test_end_meeting_from_non_member_is_ignored(flask_app, sio_room):
    """An anonymous socket that never joined (and has no token) must not end a meeting."""
    import app as app_module

    meeting_id, _ = sio_room
    client = app_module.socketio.test_client(flask_app)
    client.emit("end_meeting", {"meetingId": meeting_id})
    received = client.get_received()
    assert not any(m["name"] == "meeting_ended" for m in received), received


def test_host_ends_meeting_broadcasts_to_candidate(flask_app, sio_room):
    import app as app_module
    import state

    meeting_id, token = sio_room

    # Host connects with valid token
    host_sio = app_module.socketio.test_client(flask_app)
    host_sio.emit("join_meeting", {
        "meetingId": meeting_id, "userName": "Host", "role": "interviewer",
        "hostToken": token,
    })
    host_sio.get_received()

    # Candidate connects via session
    with flask_app.test_client() as http_cand:
        with http_cand.session_transaction() as sess:
            sess["candidate_verified_meeting"] = meeting_id
            sess["role"] = "candidate"
        cand_sio = app_module.socketio.test_client(flask_app, flask_test_client=http_cand)
        cand_sio.emit("join_meeting", {"meetingId": meeting_id, "userName": "Candidate", "role": "candidate"})
        cand_sio.get_received()

    # Host ends meeting
    host_sio.emit("end_meeting", {"meetingId": meeting_id, "hostToken": token})

    cand_received = cand_sio.get_received()
    assert any(m["name"] == "meeting_ended" for m in cand_received), cand_received

    room = state.get_meeting_room(meeting_id)
    assert room.get("status") == "ended"


def test_candidate_audit_event_relays_to_host_and_persists(flask_app, sio_room, monkeypatch):
    """A candidate alert must reach the host live AND be written to the audit trail."""
    import app as app_module
    import state

    meeting_id, token = sio_room

    persisted = []
    monkeypatch.setattr(
        state,
        "add_audit_log",
        lambda mid, etype, title, message, confidence=None, is_critical=False, metadata=None: persisted.append(
            (mid, etype, title, is_critical)
        ),
    )

    # Host joins with token
    host_sio = app_module.socketio.test_client(flask_app)
    host_sio.emit("join_meeting", {
        "meetingId": meeting_id, "userName": "Host", "role": "interviewer",
        "hostToken": token,
    })
    host_sio.get_received()

    with flask_app.test_client() as http_cand:
        with http_cand.session_transaction() as sess:
            sess["candidate_verified_meeting"] = meeting_id
            sess["role"] = "candidate"
        cand_sio = app_module.socketio.test_client(flask_app, flask_test_client=http_cand)
        cand_sio.emit("join_meeting", {"meetingId": meeting_id, "userName": "Candidate", "role": "candidate"})
        cand_sio.get_received()
        host_sio.get_received()

        cand_sio.emit(
            "audit_event",
            {"meetingId": meeting_id, "title": "Tab switch", "message": "Left the tab", "isCritical": True},
        )

    assert (meeting_id, "proctoring", "Tab switch", True) in persisted, persisted

    host_received = host_sio.get_received()
    assert any(
        m["name"] == "audit_event" and m["args"][0].get("title") == "Tab switch"
        for m in host_received
    ), host_received

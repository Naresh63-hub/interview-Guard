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


def test_join_refuses_authenticated_candidate_as_interviewer(flask_app, sio_room):
    """An authenticated user whose role is 'candidate' must NOT be admitted
    as an interviewer merely because authenticated=True is set."""
    import app as app_module
    import state

    with flask_app.test_client() as http_client:
        with http_client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["role"] = "candidate"
            sess["user_id"] = "user-candidate-123"
        client = app_module.socketio.test_client(flask_app, flask_test_client=http_client)
        client.emit(
            "join_meeting",
            {"meetingId": sio_room, "userName": "Bob", "role": "interviewer"},
        )
        received = client.get_received()
        assert any(m["name"] == "error" and "Host session invalid" in str(m["args"]) for m in received), received

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


def test_end_meeting_from_non_member_is_ignored(flask_app, sio_room):
    """An anonymous socket that never joined (and has no host/candidate
    session) must not be able to end another room's meeting."""
    import app as app_module

    client = app_module.socketio.test_client(flask_app)
    client.emit("end_meeting", {"meetingId": sio_room, "role": "candidate"})
    received = client.get_received()
    assert not any(m["name"] == "meeting_ended" for m in received), received


def test_host_ends_meeting_broadcasts_to_candidate(flask_app, sio_room):
    import app as app_module
    import state

    # Connect host
    with flask_app.test_client() as http_host:
        with http_host.session_transaction() as sess:
            sess["host_authenticated"] = True
            sess["role"] = "interviewer"
        host_sio = app_module.socketio.test_client(flask_app, flask_test_client=http_host)
        host_sio.emit("join_meeting", {"meetingId": sio_room, "userName": "Host", "role": "interviewer"})
        host_sio.get_received()

    # Connect candidate
    with flask_app.test_client() as http_cand:
        with http_cand.session_transaction() as sess:
            sess["candidate_verified_meeting"] = sio_room
            sess["role"] = "candidate"
        cand_sio = app_module.socketio.test_client(flask_app, flask_test_client=http_cand)
        cand_sio.emit("join_meeting", {"meetingId": sio_room, "userName": "Candidate", "role": "candidate"})
        cand_sio.get_received()

    # Host ends meeting
    host_sio.emit("end_meeting", {"meetingId": sio_room})
    
    cand_received = cand_sio.get_received()
    assert any(m["name"] == "meeting_ended" for m in cand_received), cand_received
    
    room = state.get_meeting_room(sio_room)
    assert room.get("status") == "ended"


def test_candidate_audit_event_relays_to_host_and_persists(flask_app, sio_room, monkeypatch):
    """A candidate alert must reach the host live AND be written to the audit
    trail (the regression: audit_event was relayed but never persisted, so the
    host's "View Full Audit Log" stayed empty apart from room_created)."""
    import app as app_module
    import state

    persisted = []
    monkeypatch.setattr(
        state,
        "add_audit_log",
        lambda mid, etype, title, message, confidence=None, is_critical=False, metadata=None: persisted.append(
            (mid, etype, title, is_critical)
        ),
    )

    with flask_app.test_client() as http_host:
        with http_host.session_transaction() as sess:
            sess["host_authenticated"] = True
            sess["role"] = "interviewer"
        host_sio = app_module.socketio.test_client(flask_app, flask_test_client=http_host)
        host_sio.emit("join_meeting", {"meetingId": sio_room, "userName": "Host", "role": "interviewer"})
        host_sio.get_received()

    with flask_app.test_client() as http_cand:
        with http_cand.session_transaction() as sess:
            sess["candidate_verified_meeting"] = sio_room
            sess["role"] = "candidate"
        cand_sio = app_module.socketio.test_client(flask_app, flask_test_client=http_cand)
        cand_sio.emit("join_meeting", {"meetingId": sio_room, "userName": "Candidate", "role": "candidate"})
        cand_sio.get_received()
        host_sio.get_received()

        cand_sio.emit(
            "audit_event",
            {"meetingId": sio_room, "title": "Tab switch", "message": "Left the tab", "isCritical": True},
        )

    assert (sio_room, "proctoring", "Tab switch", True) in persisted, persisted

    host_received = host_sio.get_received()
    assert any(
        m["name"] == "audit_event" and m["args"][0].get("title") == "Tab switch"
        for m in host_received
    ), host_received

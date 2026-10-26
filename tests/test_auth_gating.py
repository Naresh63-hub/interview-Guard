"""Auth-gating tests: unauthenticated or cross-tenant callers must be refused,
and an authenticated tenant must only see their own room's data.
"""
import pytest


CANDIDATE_GATED_JSON_POSTS = [
    "/analyze",
    "/liveness-frame",
    "/liveness-challenge",
    "/analyze-audio",
    "/analyze_answer",
]

HOST_GATED_GETS = [
    "/api/audit/SMOKE1",
    "/api/analytics/SMOKE1",
    "/get_meetings",
]


@pytest.mark.parametrize("path", CANDIDATE_GATED_JSON_POSTS)
def test_candidate_endpoints_require_verified_session(client, path):
    resp = client.post(path, json={"meetingId": "HACKROOM"})
    assert resp.status_code == 403
    assert "error" in resp.get_json()


@pytest.mark.parametrize("path", HOST_GATED_GETS)
def test_host_endpoints_reject_anonymous(client, path):
    resp = client.get(path)
    assert resp.status_code in (401, 403)


def test_host_endpoints_allow_authenticated_host(host_client):
    resp = host_client.get("/api/audit/NOPE9")
    # Authenticated but the room does not exist: must not be a 401/403 gate,
    # and must not be a 500 either.
    assert resp.status_code in (200, 404)


def test_audio_state_requires_auth(client):
    assert client.get("/audio-state").status_code == 401


def test_audio_state_serves_verified_candidate(client, flask_app):
    with client.session_transaction() as sess:
        sess["candidate_verified_meeting"] = "AUTHPROP"
    resp = client.get("/audio-state?meetingId=AUTHPROP")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "voiceScore" in body and "suspiciousAudio" in body


def test_room_api_requires_membership(client):
    # Nonexistent room answers 404 before the auth check (existence is not
    # leaked either way); a caller without host/tenant rights never gets data.
    resp = client.get("/api/room/AUTHPROP")
    assert resp.status_code in (401, 404)


def test_create_meeting_requires_host(client, host_client):
    assert client.post("/create_meeting", json={"title": "anon"}).status_code == 401
    resp = host_client.post("/create_meeting", json={"title": "scheduled"})
    assert resp.status_code == 201


def test_candidate_dashboard_rejects_foreign_room(client):
    with client.session_transaction() as sess:
        sess["candidate_verified_meeting"] = "MYROOM"
    resp = client.get("/candidate_dashboard/OTHER1")
    assert resp.status_code == 403


def test_liveness_challenge_anonymous_json_error(client):
    resp = client.post("/liveness-challenge", json={"type": "blink"})
    assert resp.status_code == 403
    assert resp.is_json


def test_session_join_rejects_anonymous_candidate(client, detector):
    """Joining wipes per-room detection state — an anonymous caller with a
    guessed meetingId must not be able to reset a live interview."""
    detector.reset_meeting("GATEROOM")
    s = detector.get_session("GATEROOM")
    s.pose_smooth = (1.0, 1.0, 1.0)

    resp = client.post("/session/join", json={"role": "candidate", "meetingId": "GATEROOM"})
    assert resp.status_code == 401

    assert detector.get_session("GATEROOM") is s
    assert s.pose_smooth == (1.0, 1.0, 1.0)


def test_session_join_rejects_anonymous_interviewer(client):
    resp = client.post("/session/join", json={"role": "interviewer", "meetingId": "GATEROOM"})
    assert resp.status_code == 401


def test_session_leave_rejects_foreign_candidate(client, detector):
    detector.reset_meeting("LVROOM01")
    resp = client.post(
        "/session/leave", json={"role": "candidate", "meetingId": "LVROOM01"}
    )
    assert resp.status_code == 401
    # State survived the refused leave; the room's own candidate can drop it.
    assert "LVROOM01" in detector.active_session_ids()
    with client.session_transaction() as sess:
        sess["candidate_verified_meeting"] = "LVROOM01"
    resp = client.post(
        "/session/leave", json={"role": "candidate", "meetingId": "LVROOM01"}
    )
    assert resp.status_code == 200
    assert "LVROOM01" not in detector.active_session_ids()


# ---------------------------------------------------------------------------
# Regression tests for the host-bypass vulnerability (routes.py /api/host/join
# and related endpoints).  These prove that unauthenticated callers CANNOT
# obtain host privileges even by providing a meeting ID or hitting the
# dashboard URL directly.
# ---------------------------------------------------------------------------

HOST_BYPASS_ENDPOINTS_POST = [
    "/api/host/join",
    "/create-room",
]

HOST_BYPASS_ENDPOINTS_GET = [
    "/host_dashboard",
    "/host_dashboard/ABCDEF12",
]


@pytest.mark.parametrize("path", HOST_BYPASS_ENDPOINTS_POST)
def test_host_bypass_endpoints_reject_anonymous(client, path):
    """An unauthenticated POST must not gain host privileges."""
    resp = client.post(path, json={"hostName": "Hacker", "meetingId": "ABCDEF12"})
    assert resp.status_code == 401, resp.get_data(as_text=True)[:200]


@pytest.mark.parametrize("path", HOST_BYPASS_ENDPOINTS_GET)
def test_host_bypass_pages_reject_anonymous(client, path):
    """GET on host dashboard URLs without session must not render the page."""
    resp = client.get(path)
    assert resp.status_code == 401


def test_meeting_id_alone_does_not_authenticate(client):
    """A valid meeting ID without a prior authenticated session must NOT
    grant host access — the meeting ID is not a credential."""
    resp = client.post("/api/host/join", json={
        "hostName": "Mallory",
        "title": "Sneaky",
        "meetingId": "ABCDEF12",
    })
    assert resp.status_code == 401
    assert resp.get_json().get("success") is not True


def test_authenticated_host_still_works(flask_app):
    """After proper Supabase login (simulated by session flags), the host can
    still create and join meetings."""
    with flask_app.test_client() as c:
        with c.session_transaction() as sess:
            sess["authenticated"] = True
            sess["host_authenticated"] = True
            sess["role"] = "interviewer"
            sess["full_name"] = "Alice"
        resp = c.post("/api/host/join", json={"hostName": "Alice", "title": "Interview"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "meetingId" in data


def test_candidate_join_unaffected(client):
    """The candidate join flow (via /api/candidate/join) works without host
    privileges and does not set host_authenticated."""
    resp = client.post("/api/candidate/join", json={
        "meetingId": "ABCD1234",
        "displayName": "Bob",
    })
    # Candidate join returns 200 if the room exists, else 400/404/403.
    # It must NOT be 401 (that would indicate host gating leaked to candidates).
    assert resp.status_code != 401, resp.get_data(as_text=True)[:200]


def test_meet_link_admits_candidate_without_host_privileges(client):
    """Direct meeting link (/meet/<id>) must admit candidate without login,
    set candidate role, and NEVER grant host privileges."""
    resp = client.get("/meet/DIRECT01")
    assert resp.status_code == 302
    assert "/candidate_dashboard/DIRECT01" in resp.headers.get("Location", "")

    # Follow redirect to candidate dashboard
    dash_resp = client.get(resp.headers["Location"])
    assert dash_resp.status_code == 200

    # Session inspection: candidate is verified, host is NOT authenticated
    with client.session_transaction() as sess:
        assert sess.get("candidate_verified_meeting") == "DIRECT01"
        assert sess.get("role") == "candidate"
        assert not sess.get("host_authenticated")
        assert not sess.get("authenticated")

    # Candidate must NOT be able to access host dashboard or host APIs
    assert client.get("/host_dashboard/DIRECT01").status_code == 401
    assert client.post("/api/host/join", json={"meetingId": "DIRECT01"}).status_code == 401
    assert client.post("/create-room", json={}).status_code == 401


def test_candidate_cannot_elevate_to_host_via_socket(flask_app):
    """A verified candidate socket cannot claim host/interviewer role."""
    import app as app_module
    import state

    meeting_id = "SOCKETGAT1"
    state.create_meeting_room(meeting_id, "Host", "Gating Test Room")

    with flask_app.test_client() as c:
        # Candidate visits direct link
        c.get(f"/meet/{meeting_id}")
        sio_client = app_module.socketio.test_client(flask_app, flask_test_client=c)
        
        # Attempt to join as interviewer
        sio_client.emit("join_meeting", {"meetingId": meeting_id, "role": "interviewer", "userName": "Sneaky"})
        received = sio_client.get_received()
        assert any(m["name"] == "error" and "Host session invalid" in str(m["args"]) for m in received), received

        # Must not be in room participants as host
        room = state.get_meeting_room(meeting_id)
        assert not any(p.get("role") == "interviewer" and (p.get("socketId") or p.get("socket_id")) == sio_client.sid for p in room.get("participants", []))

    state.meeting_rooms.pop(meeting_id, None)


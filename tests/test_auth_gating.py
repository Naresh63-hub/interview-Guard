"""Capability-token authentication gating tests.

The meeting workflow no longer requires login.  Host authorization is a
per-meeting, server-generated token (secrets.token_urlsafe(32)) returned once
at creation time.  These tests prove:
  1. A host can create a meeting without any prior login.
  2. Host-only controls require the correct meeting-specific capability token.
  3. A user without the token cannot access host-only controls.
  4. A candidate can join without login.
  5. A candidate cannot escalate to host by changing role.
  6. A host token for one meeting cannot control another meeting.
  7. Tokens are never exposed in candidate-facing data.
"""
import pytest

import state


CANDIDATE_GATED_JSON_POSTS = [
    "/analyze",
    "/liveness-frame",
    "/liveness-challenge",
    "/analyze-audio",
    "/analyze_answer",
]


@pytest.mark.parametrize("path", CANDIDATE_GATED_JSON_POSTS)
def test_candidate_endpoints_require_verified_session(client, path):
    resp = client.post(path, json={"meetingId": "HACKROOM"})
    assert resp.status_code == 403
    assert "error" in resp.get_json()


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
    resp = client.get("/api/room/AUTHPROP")
    assert resp.status_code in (401, 404)


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
    assert "LVROOM01" in detector.active_session_ids()
    with client.session_transaction() as sess:
        sess["candidate_verified_meeting"] = "LVROOM01"
    resp = client.post(
        "/session/leave", json={"role": "candidate", "meetingId": "LVROOM01"}
    )
    assert resp.status_code == 200
    assert "LVROOM01" not in detector.active_session_ids()


# ─── Capability-Token Tests (New Architecture) ───────────────────────────────


def test_create_room_returns_token(client):
    """POST /create-room with no auth at all -> 200 with a hostToken."""
    resp = client.post("/create-room", json={"hostName": "Alice", "title": "Interview"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["meetingId"]
    assert data["hostToken"]
    assert len(data["hostToken"]) >= 20  # cryptographic randomness


def test_host_join_creates_meeting_without_login(client):
    """POST /api/host/join without auth -> creates room, returns token."""
    resp = client.post("/api/host/join", json={"hostName": "Bob", "title": "Tech Chat"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["hostToken"]
    assert data["meetingId"]


def test_session_join_interviewer_requires_token(client):
    """POST /session/join with interviewer role and NO token -> 401."""
    resp = client.post(
        "/session/join", json={"role": "interviewer", "meetingId": "NOTOKEN"}
    )
    assert resp.status_code == 401


def test_session_join_interviewer_with_valid_token(host_client_with_token):
    """POST /session/join with interviewer + token header -> 200."""
    c, meeting_id, token = host_client_with_token
    resp = c.post(
        "/session/join",
        json={"role": "interviewer", "meetingId": meeting_id},
        headers={"X-Meeting-Host-Token": token},
    )
    assert resp.status_code == 200


def test_invalid_token_rejected(client, host_client_with_token):
    """A wrong token value must be rejected."""
    _, meeting_id, _ = host_client_with_token
    resp = client.post(
        "/session/join",
        json={"role": "interviewer", "meetingId": meeting_id},
        headers={"X-Meeting-Host-Token": "totally-fake-token"},
    )
    assert resp.status_code == 401


def test_cross_meeting_token_invalidation(client):
    """Token from meeting A must NOT authorize operations for meeting B."""
    resp_a = client.post("/create-room", json={"hostName": "H1", "title": "A"})
    data_a = resp_a.get_json()
    token_a = data_a["hostToken"]
    mid_a = data_a["meetingId"]

    # Create a second meeting via different client so tokens are separate
    c2 = client.application.test_client()
    resp_b = c2.post("/create-room", json={"hostName": "H2", "title": "B"})
    data_b = resp_b.get_json()
    mid_b = data_b["meetingId"]

    # Try token_a on meeting_b -> must fail
    resp = client.post(
        "/session/join",
        json={"role": "interviewer", "meetingId": mid_b},
        headers={"X-Meeting-Host-Token": token_a},
    )
    assert resp.status_code == 401

    # Clean up
    state.meeting_rooms.pop(mid_a, None)
    state.meeting_rooms.pop(mid_b, None)


def test_candidate_joins_without_login(client, host_client_with_token):
    """POST /api/candidate/join works without login or token."""
    _, meeting_id, _ = host_client_with_token
    resp = client.post(
        "/api/candidate/join",
        json={"meetingId": meeting_id, "displayName": "Eve"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True


def test_candidate_cannot_escalate_to_host(client, host_client_with_token):
    """Candidate session cannot pass _is_host_request gate."""
    _, meeting_id, _ = host_client_with_token
    client.post("/api/candidate/join", json={"meetingId": meeting_id, "displayName": "X"})
    # Try session/join as interviewer (no token in candidate session)
    resp = client.post(
        "/session/join", json={"role": "interviewer", "meetingId": meeting_id}
    )
    assert resp.status_code == 401


def test_host_dashboard_renders_with_token(host_client_with_token):
    """Host can access /host_dashboard/<id> via session-stored token."""
    c, meeting_id, token = host_client_with_token
    resp = c.get(f"/host_dashboard/{meeting_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert token in html  # Token is passed to template


def test_invite_link_requires_host_token(client, host_client_with_token):
    """GET /api/room/<id>/invite without token -> 401."""
    _, meeting_id, _ = host_client_with_token
    resp = client.get(f"/api/room/{meeting_id}/invite")
    assert resp.status_code == 401


def test_invite_link_with_valid_session(host_client_with_token):
    """GET /api/room/<id>/invite with session-stored token -> 200."""
    c, meeting_id, _ = host_client_with_token
    resp = c.get(f"/api/room/{meeting_id}/invite")
    assert resp.status_code == 200


def test_audit_logs_require_token(host_client_with_token):
    """GET /api/audit/<id> works with the capability token."""
    c, meeting_id, token = host_client_with_token
    resp = c.get(f"/api/audit/{meeting_id}", headers={"X-Meeting-Host-Token": token})
    assert resp.status_code == 200


def test_meet_link_admits_candidate_without_host_privileges(client):
    """Direct meeting link (/meet/<id>) must admit candidate without login,
    set candidate role, and NEVER grant host privileges."""
    resp = client.get("/meet/DIRECT01")
    assert resp.status_code == 302
    assert "/candidate_dashboard/DIRECT01" in resp.headers.get("Location", "")

    # Follow redirect to candidate dashboard
    dash_resp = client.get(resp.headers["Location"])
    assert dash_resp.status_code == 200

    # Session inspection: candidate is verified, NOT a host
    with client.session_transaction() as sess:
        assert sess.get("candidate_verified_meeting") == "DIRECT01"
        assert sess.get("role") == "candidate"

    # Candidate must NOT access host APIs (no token)
    assert client.post("/api/host/join", json={"meetingId": "DIRECT01"}).status_code == 401


# ─── Token Secrecy Tests ─────────────────────────────────────────────────────


def test_candidate_join_response_excludes_token(client, host_client_with_token):
    """POST /api/candidate/join response must not contain any host token."""
    _, meeting_id, host_token = host_client_with_token
    resp = client.post(
        "/api/candidate/join",
        json={"meetingId": meeting_id, "displayName": "Eve"},
    )
    assert resp.status_code == 200
    body_text = resp.get_data(as_text=True)
    assert host_token not in body_text
    data = resp.get_json()
    assert "hostToken" not in data


def test_candidate_dashboard_html_excludes_token(client, host_client_with_token):
    """The candidate_dashboard page must not render the host token."""
    _, meeting_id, host_token = host_client_with_token
    client.post("/api/candidate/join", json={"meetingId": meeting_id, "displayName": "E"})
    resp = client.get(f"/candidate_dashboard/{meeting_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert host_token not in html


def test_host_dashboard_no_session_renders_empty_token(host_client_with_token, flask_app):
    """A different client without token session gets HOST_TOKEN='' in template."""
    c, meeting_id, _ = host_client_with_token
    c2 = flask_app.test_client()
    resp = c2.get(f"/host_dashboard/{meeting_id}")
    # Page shell still renders
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'window.HOST_TOKEN = ""' in html

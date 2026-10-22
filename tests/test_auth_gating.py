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

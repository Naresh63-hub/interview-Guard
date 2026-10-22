"""Smoke tests: every route must answer without crashing (NameError, TypeError,
BuildError, 500s). The bug-fix pass refactored call sites across routes.py and
the AI modules; these tests catch broken imports or renamed symbols immediately.
"""
import pytest

# (rule, expected statuses). Auth-gated pages legitimately answer 401/403 or
# redirect; anything else in 200..499 proves the handler executed cleanly.
GET_ROUTES = [
    "/",
    "/login",
    "/login.html",
    "/register",
    "/register.html",
    "/login/host",
    "/login/candidate",
    "/host_dashboard",
    "/host_dashboard/SMOKE1",
    "/candidate_dashboard/SMOKE1",
    "/audit_log",
    "/calibration",
    "/session/status?meetingId=SMOKE1",
    "/api/room/SMOKE1",
    "/api/room/SMOKE1/invite-link",
    "/api/room/SMOKE1/invite",
    "/api/audit/SMOKE1",
    "/api/analytics/SMOKE1",
    "/embed-view",
    "/meet/SMOKE1",
    "/dashboard/SMOKE1",
    "/get_meetings",
    "/gaze",
    "/health",
    "/audio-state",
]

# JSON POSTs with an empty body: must be rejected by validation/auth gates,
# not by a crash. 400/401/403 all prove the guard code ran to completion.
POST_ROUTES = [
    "/api/candidate/join",
    "/api/calibrate",
    "/session/join",
    "/session/heartbeat",
    "/leave",
    "/session/leave",
    "/create-room",
    "/create_meeting",
    "/ai_insights",
    "/analyze",
    "/liveness-frame",
    "/liveness-challenge",
    "/analyze-audio",
    "/analyze_answer",
    "/analyze_network",
    "/api/session/SMOKE1/start",
    "/api/session/SMOKE1/end",
]


@pytest.mark.parametrize("path", GET_ROUTES)
def test_get_routes_respond(client, path):
    resp = client.get(path)
    assert resp.status_code in (200, 302, 400, 401, 403, 404), (
        f"GET {path} -> {resp.status_code}: {resp.get_data(as_text=True)[:200]}"
    )


@pytest.mark.parametrize("path", POST_ROUTES)
def test_post_routes_reject_cleanly(client, path):
    resp = client.post(path, json={})
    assert resp.status_code in (200, 400, 401, 403), (
        f"POST {path} -> {resp.status_code}: {resp.get_data(as_text=True)[:200]}"
    )


def test_health_is_json_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.is_json


def test_analyze_route_symbol_surface(detector):
    """The per-meeting refactor renamed the detector API — old global-reset
    callers must be gone and the scoped API must exist."""
    assert not hasattr(detector, "reset_temporal_state")
    for name in ("get_session", "reset_meeting", "drop_meeting", "analyze_frame"):
        assert callable(getattr(detector, name, None)), name

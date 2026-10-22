"""Shared pytest fixtures.

The Flask app (with MediaPipe/YOLO/autoencoder singletons) is expensive to
boot, so everything is wrapped in session-scoped fixtures and imported only
once. Supabase is unreachable in CI/dev sandboxes — the app falls back to
in-memory state, which is exactly what these tests exercise.
"""
import pytest


@pytest.fixture(scope="session")
def flask_app():
    import app as app_module

    # Flask-Limiter registers itself under its class name in `extensions`
    # (a set as of flask-limiter 3.x) — find the limiter via its
    # documented attribute instead of guessing the key.
    limiter = getattr(app_module.app, "extensions", {})
    for value in list(limiter.values()):
        for candidate in value if isinstance(value, (list, set, tuple)) else [value]:
            if hasattr(candidate, "enabled") and hasattr(candidate, "_route_limits"):
                # Tests hammer endpoints far past the 200/min default.
                candidate.enabled = False
    return app_module.app


@pytest.fixture(scope="session")
def detector(flask_app):
    """The shared AdvancedGazeDetector singleton used by every route."""
    import routes

    return routes.gaze_detector


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture
def host_client(flask_app):
    """Client whose session carries the host_authenticated flag."""
    c = flask_app.test_client()
    with c.session_transaction() as sess:
        sess["host_authenticated"] = True
    return c


def make_landmarks(ear_value: float = 0.35):
    """478 fake normalized landmarks whose eye geometry yields `ear_value`.

    Indices match liveness_detector: left eye (33,133)/(159,145),
    right eye (362,263)/(386,374). Horizontal span is fixed at 0.1 so the
    requested EAR maps to a vertical gap of exactly `ear_value * 0.1`.
    """
    n = 478
    pts = [{"x": 0.5, "y": 0.5, "z": 0.0} for _ in range(n)]
    v = ear_value * 0.1

    def pair(a, b, c, d):
        pts[a].update(x=0.20, y=0.30)
        pts[b].update(x=0.30, y=0.30)
        pts[c].update(x=0.25, y=0.30 - v / 2)
        pts[d].update(x=0.25, y=0.30 + v / 2)

    pair(33, 133, 159, 145)    # left eye
    pair(362, 263, 386, 374)   # right eye
    return pts

import os
import secrets

from dotenv import load_dotenv
from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# --------------------------------------------------------------------------- #
# Load environment variables
# IMPORTANT: This must happen before reading SECRET_KEY / OAuth settings.
# --------------------------------------------------------------------------- #
load_dotenv()

# --------------------------------------------------------------------------- #
# App Setup
# --------------------------------------------------------------------------- #
app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)

# --------------------------------------------------------------------------- #
# Flask Secret Key
# --------------------------------------------------------------------------- #
default_secret = os.getenv("SECRET_KEY")

if not default_secret:
    default_secret = secrets.token_hex(32)
    print(
        "[SECURITY] Generated random SECRET_KEY. "
        "Set SECRET_KEY environment variable for production."
    )

app.secret_key = default_secret

# --------------------------------------------------------------------------- #
# Session Configuration
# --------------------------------------------------------------------------- #
# SESSION_COOKIE_SECURE is read from the environment: set it to 1 (or true)
# in production, where the app is served over HTTPS. Locally over plain HTTP
# at http://127.0.0.1:5000 browsers only accept the cookie when it is False.
session_cookie_secure = os.getenv(
    "SESSION_COOKIE_SECURE", "0"
).strip().lower() in ("1", "true", "yes")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=session_cookie_secure,

    # Raw frame/audio uploads:
    # A 640x480 JPEG is typically ~50-150KB and PCM audio is tens of KB.
    # 4MB is generous for legitimate traffic while limiting oversized uploads.
    MAX_CONTENT_LENGTH=4 * 1024 * 1024,
)

# --------------------------------------------------------------------------- #
# Rate Limiting
# --------------------------------------------------------------------------- #
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=[
        "200 per minute",
        "50 per second",
    ],
    storage_uri="memory://",
)

# --------------------------------------------------------------------------- #
# CORS Configuration
# --------------------------------------------------------------------------- #
default_cors = (
    "http://localhost:5000,"
    "https://localhost:5000,"
    "http://127.0.0.1:5000,"
    "https://127.0.0.1:5000"
)

cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", default_cors).split(",")
    if origin.strip()
]

CORS(
    app,
    origins=cors_origins,
    supports_credentials=True,
)

# --------------------------------------------------------------------------- #
# Security Headers
# --------------------------------------------------------------------------- #
@app.after_request
def add_security_headers(response):
    response.headers.setdefault(
        "X-Content-Type-Options",
        "nosniff",
    )

    response.headers.setdefault(
        "Referrer-Policy",
        "strict-origin-when-cross-origin",
    )

    response.headers.setdefault(
        "X-Frame-Options",
        "SAMEORIGIN",
    )

    # Allow the application itself to use camera/microphone.
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(self), microphone=(self), display-capture=(self)",
    )

    return response


# --------------------------------------------------------------------------- #
# Socket.IO
# --------------------------------------------------------------------------- #
socketio_origins = (
    cors_origins if cors_origins != ["*"] else "*"
)

socketio = SocketIO(
    app,
    cors_allowed_origins=socketio_origins,
    async_mode="threading",
)

# --------------------------------------------------------------------------- #
# Import routes and sockets
# These imports attach the application's existing functionality.
# --------------------------------------------------------------------------- #
import routes
import sockets
import auth_routes

print("[App] Registering routes...")
routes.register_routes(app)

print("[App] Registering sockets...")
sockets.register_sockets(socketio)

print("[App] Registering auth routes...")
auth_routes.register_auth_routes(app)

print("[App] All routes registered successfully")

# --------------------------------------------------------------------------- #
# Startup
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    print("=" * 60)
    print("  Intervue Backend     ->  http://127.0.0.1:5000")
    print("  Dashboard            ->  http://127.0.0.1:5000/host_dashboard")
    print("  Host Credentials     ->  set via HOST_PASSWORD")
    print("=" * 60)

    # Helpful OAuth configuration check without exposing secrets.
    google_client_id = os.getenv("GOOGLE_CLIENT_ID")
    google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    google_redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")

    print(
        "[OAuth] Google Client ID configured:",
        bool(google_client_id),
    )

    print(
        "[OAuth] Google Client Secret configured:",
        bool(google_client_secret),
    )

    print(
        "[OAuth] Google Redirect URI:",
        google_redirect_uri or "(not configured)",
    )

    print(
        "[Session] SECRET_KEY loaded:",
        bool(os.getenv("SECRET_KEY")),
    )

    print(
        "[Session] SESSION_COOKIE_SECURE:",
        app.config["SESSION_COOKIE_SECURE"],
    )

    # --------------------------------------------------------------------- #
    # Server settings
    # --------------------------------------------------------------------- #
    # debug=True is a development convenience and MUST NOT be enabled in
    # production (it exposes the interactive debugger to remote clients).
    # Set FLASK_DEBUG=1 to enable it; allow_unsafe_werkzeug (which silences
    # the "do not run over HTTP in production" warning) follows debug.
    debug_mode = os.getenv("FLASK_DEBUG", "0").strip().lower() in ("1", "true", "yes")
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))

    if debug_mode:
        print("[WARN] FLASK_DEBUG=1: running with the interactive debugger. Do not use in production.")

    socketio.run(
        app,
        host=host,
        port=port,
        debug=debug_mode,
        use_reloader=False,
        allow_unsafe_werkzeug=debug_mode,
    )

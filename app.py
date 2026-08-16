import os
import secrets
from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# --------------------------------------------------------------------------- #
# App Setup
# --------------------------------------------------------------------------- #
app = Flask(__name__, template_folder="templates", static_folder="static")

# Generate secure secret key if not provided
default_secret = os.getenv("SECRET_KEY")
if not default_secret:
    default_secret = secrets.token_hex(32)
    print("[SECURITY] Generated random SECRET_KEY. Set SECRET_KEY environment variable for production.")

app.secret_key = default_secret
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "1") == "1",  # Enable secure cookies by default
    # Raw frame/audio uploads: a 640x480 JPEG is ~50-150KB and PCM audio is
    # tens of KB, so 4MB is generous for legitimate traffic while capping
    # decompression bombs before cv2.imdecode (which runs BEFORE the 960px
    # downscale and would otherwise allocate huge buffers).
    MAX_CONTENT_LENGTH=4 * 1024 * 1024,
)

# Rate limiting setup
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per minute", "50 per second"],
    storage_uri="memory://"
)

# More secure CORS defaults - only allow localhost by default
default_cors = "http://localhost:5000,https://localhost:5000,http://127.0.0.1:5000,https://127.0.0.1:5000"
cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", default_cors).split(",")
    if origin.strip()
]
CORS(app, origins=cors_origins)


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    # Explicitly allow the app itself to use camera/mic. Without this, some
    # browsers refuse getUserMedia when the app is embedded in an iframe whose
    # parent page does not delegate the permission.
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(self), microphone=(self), display-capture=(self)",
    )
    return response

socketio_origins = cors_origins if cors_origins != ["*"] else "*"
socketio = SocketIO(app, cors_allowed_origins=socketio_origins, async_mode="threading")

# Import routes and sockets so they attach to the app
import routes
import sockets
import auth_routes
routes.register_routes(app)
sockets.register_sockets(socketio)
auth_routes.register_auth_routes(app)

if __name__ == "__main__":
    print("=" * 60)
    print("  Intervue Backend     ->  http://127.0.0.1:5000")
    print("  Dashboard            ->  http://127.0.0.1:5000/host_dashboard")
    print("  Host Credentials     ->  admin123 (HOST_PASSWORD)")
    print("=" * 60)
    
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=True,
        use_reloader=False,
        allow_unsafe_werkzeug=True
    )

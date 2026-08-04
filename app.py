import os
from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO

# --------------------------------------------------------------------------- #
# App Setup
# --------------------------------------------------------------------------- #
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0") == "1",
)

cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "*").split(",")
    if origin.strip()
]
CORS(app, origins=cors_origins)


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return response

socketio_origins = cors_origins if cors_origins != ["*"] else "*"
socketio = SocketIO(app, cors_allowed_origins=socketio_origins, async_mode="threading")

# Import routes and sockets so they attach to the app
import routes
import sockets
routes.register_routes(app)
sockets.register_sockets(socketio)

if __name__ == "__main__":
    print("=" * 60)
    print("  Intervue Backend     ->  https://127.0.0.1:5000")
    print("  Dashboard            ->  https://127.0.0.1:5000/host_dashboard")
    print("  Host Credentials     ->  admin123 (HOST_PASSWORD)")
    print("=" * 60)
    
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=True,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
        ssl_context=("ssl.crt", "ssl.key")
    )

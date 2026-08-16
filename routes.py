import os
import time
import uuid
import base64
import cv2
import numpy as np
import hmac
import hashlib
from flask import Blueprint, jsonify, redirect, render_template, request, url_for, session, current_app
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import state
from advanced_gaze_detector import AdvancedGazeDetector
from database import db

# Rate limiter for API routes
limiter = Limiter(key_func=get_remote_address, default_limits=["100 per minute"])

bp = Blueprint('main', __name__)

def register_routes(app):
    app.register_blueprint(bp)

# --------------------------------------------------------------------------- #
# OpenAI / Gaze Setup
# --------------------------------------------------------------------------- #
try:
    from openai import OpenAI
    _api_key = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=_api_key) if _api_key else None
except ImportError:
    client = None

gaze_detector = AdvancedGazeDetector()

# --------------------------------------------------------------------------- #
# Helper Functions
# --------------------------------------------------------------------------- #
def _generate_signed_link(meeting_id: str, ttl_seconds: int = 86400) -> str:
    """Build a signed /meet/<id>?sig=...&expires=... link for the candidate.

    The signature is deterministic per (meeting_id, expires), so links can be
    regenerated on demand (e.g. for the host dashboard Invite button) without
    persisting them.
    """
    expires = int(time.time()) + ttl_seconds
    sig_payload = f"{meeting_id}-{expires}"
    signature = hmac.new(
        current_app.secret_key.encode(), sig_payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"/meet/{meeting_id}?sig={signature}&expires={expires}"


def _prune_inactive_participants(now: float = None) -> None:
    now = now or time.time()
    expired_roles = [
        role
        for role, participant in state.active_participants.items()
        if now - participant.get("lastSeen", 0) > state.SESSION_TIMEOUT_SECONDS
    ]
    for role in expired_roles:
        state.active_participants.pop(role, None)

def _session_status_payload() -> dict:
    now = time.time()
    _prune_inactive_participants(now)

    both_present = all(
        role in state.active_participants for role in ("interviewer", "candidate")
    )
    if both_present:
        if state.session_started_at is None:
            state.session_started_at = now
    else:
        state.session_started_at = None

    return {
        "bothPresent": both_present,
        "startedAt": state.session_started_at,
        "participants": {
            role: {
                "displayName": participant.get("displayName") or role.title(),
                "lastSeen": participant.get("lastSeen"),
            }
            for role, participant in state.active_participants.items()
        },
    }

def _is_candidate_request() -> bool:
    payload = request.get_json(silent=True) or {}
    meeting_id = (payload.get("meetingId") or "").strip().upper()
    verified_meeting = session.get("candidate_verified_meeting")
    if meeting_id:
        return verified_meeting == meeting_id
    return bool(verified_meeting)


def _request_meeting_id() -> str:
    """Best-effort meeting id for the current candidate request.

    Used to pick the per-meeting analysis lock: two interviews must never
    block each other, but requests for the SAME meeting still serialize (the
    detector's per-session state must not interleave).
    """
    # Binary uploads (Content-Type: image/*) carry no JSON body — the
    # frontend sends the meeting id as a header instead.
    meeting_id = (request.headers.get("X-Meeting-Id") or "").strip().upper()
    if meeting_id:
        return meeting_id
    payload = request.get_json(silent=True) or {}
    meeting_id = (payload.get("meetingId") or "").strip().upper()
    if meeting_id:
        return meeting_id
    return (session.get("candidate_verified_meeting") or "").upper()


def _decode_frame_bytes(image_bytes: bytes):
    """Decode raw encoded image bytes (JPEG/PNG) into a downscaled BGR frame.

    Shared by the binary (/analyze with Content-Type: image/*) and legacy
    base64 paths so the decode + resize path is identical everywhere (and only
    implemented once). Frames are capped at 960px wide: MediaPipe + YOLO run
    fine below this size and it bounds per-request CPU and bandwidth.
    """
    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)

    if frame is None:
        return None

    max_width = 960
    if frame.shape[1] > max_width:
        scale = max_width / float(frame.shape[1])
        frame = cv2.resize(
            frame,
            (max_width, int(frame.shape[0] * scale)),
            interpolation=cv2.INTER_AREA,
        )
    return frame


def _decode_frame_image(image_data: str):
    """Decode a base64 data-URL image into a downscaled BGR numpy frame.

    Legacy path, kept for compatibility with clients that still send frames
    as base64 JSON (and used by the test suite). Prefer raw binary uploads:
    ~25% fewer bytes on the wire and no client-side base64 encode.
    """
    if "," in image_data:
        image_data = image_data.split(",", 1)[1]

    padding = 4 - len(image_data) % 4
    if padding != 4:
        image_data += "=" * padding

    return _decode_frame_bytes(base64.b64decode(image_data))


def _decode_request_frame():
    """Decode the frame carried by the current request.

    Two wire formats are accepted:
    * Raw binary: Content-Type image/* (or application/octet-stream), body is
      the raw encoded image bytes — the fast path used by the frontend.
    * Legacy JSON: {"image": "data:image/jpeg;base64,..."}.

    Returns (frame, None) on success, or (None, error_message) when the image
    is missing or undecodable.
    """
    ctype = (request.content_type or "").lower()
    if ctype.startswith("image/") or ctype == "application/octet-stream":
        image_bytes = request.get_data()
        if not image_bytes:
            return None, "Missing image"
        frame = _decode_frame_bytes(image_bytes)
        if frame is None:
            return None, "Could not decode image"
        return frame, None

    payload = request.get_json(silent=True) or {}
    image_data = payload.get("image")
    if not image_data:
        return None, "Missing image"
    frame = _decode_frame_image(image_data)
    if frame is None:
        return None, "Could not decode image"
    return frame, None

def local_meeting_insights(text: str) -> str:
    lowered = text.lower()
    urgent_terms = ["urgent", "asap", "immediately", "deadline", "blocked", "critical", "incident"]
    followup_terms = ["follow up", "reply", "send", "review", "approve", "prepare", "share"]

    priority = "High" if any(t in lowered for t in urgent_terms) else "Medium"
    if len(text.strip()) < 80 and priority != "High":
        priority = "Low"

    should_attend = priority == "High" or any(t in lowered for t in ["decision", "client", "interview", "review"])
    action = "Follow up required" if any(t in lowered for t in followup_terms) else "Capture notes and confirm next owner"
    suggestion = "Attend live" if should_attend else "Reschedule or request async notes"

    return (
        f"Summary: {text.strip()[:220] or 'No meeting description provided.'}\n\n"
        f"Priority: {priority}\n\n"
        f"Action items:\n- {action}\n- Confirm meeting owner and expected outcome\n\n"
        f"Suggestion: {suggestion}"
    )

# --------------------------------------------------------------------------- #
# HTML Page Routes
# --------------------------------------------------------------------------- #
@bp.route("/")
def home():
    return render_template("login.html")

@bp.route("/login")
@bp.route("/login.html")
def login():
    return render_template("login.html")

@bp.route("/register")
@bp.route("/register.html")
def register():
    # Redirect to login page with signup tab
    return render_template("login.html")

@bp.route("/login/host")
def login_host():
    return render_template("login_host.html")

@bp.route("/login/candidate")
def login_candidate():
    # Public entry point: the candidate enters a Meeting ID + name here. The
    # meeting_id query param (set when arriving via a signed /meet link) just
    # pre-fills the form; actual access control happens in /api/candidate/join
    # (room must exist) and candidate_dashboard (session must be verified).
    return render_template("login_candidate.html")


@bp.route("/api/candidate/join", methods=["POST", "OPTIONS"])
@limiter.limit("10 per minute")
def candidate_join():
    """Join a meeting as a candidate and establish the verified session.

    The candidate provides the Meeting ID (and optionally their name). If the
    room exists, we record the verification in the session so the candidate
    can reach /candidate_dashboard/<id> and the protected analysis routes.
    Candidates arriving via the host's signed /meet link are already verified
    by that route and may skip this call (the form POSTs anyway and it is
    idempotent).
    """
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.get_json(silent=True) or {}
    meeting_id = (data.get("meetingId") or "").strip().upper()
    display_name = (data.get("displayName") or "").strip()

    if not meeting_id:
        return jsonify({"error": "Meeting ID is required"}), 400

    room = state.get_meeting_room(meeting_id)
    if not room:
        return jsonify({"error": "Room not found. Please check the Meeting ID."}), 404

    # Verify (or re-verify) this browser for the meeting so the dashboard and
    # /analyze-family routes accept subsequent requests.
    session["candidate_verified_meeting"] = meeting_id

    return jsonify({
        "meetingId": meeting_id,
        "displayName": display_name or "Candidate",
    }), 200

@bp.route("/host_dashboard/<meeting_id>")
def host_dashboard(meeting_id):
    if not session.get('host_authenticated'):
        return redirect("/login/host")
    room = state.get_meeting_room(meeting_id.upper())
    if not room:
        return redirect("/login/host")
    return render_template(
        "host_dashboard.html",
        meeting_id=meeting_id.upper(),
        room_title=room["title"],
        room_host=room["host"],
    )

@bp.route("/candidate_dashboard/<meeting_id>")
def candidate_dashboard(meeting_id):
    meeting_id = meeting_id.upper()
    if session.get('candidate_verified_meeting') != meeting_id:
        return "Unauthorized: Please use the signed link sent by the host", 403
    room = state.meeting_rooms.get(meeting_id)
    if not room:
        return redirect(f"/login/candidate?meeting_id={meeting_id}")
    return render_template(
        "candidate_dashboard.html",
        meeting_id=meeting_id,
        room_title=room["title"],
        room_host=room["host"],
    )

@bp.route("/audit_log")
def audit_log():
    return render_template("audit_log.html")

@bp.route("/calibration")
def calibration():
    return render_template("calibration.html")

@bp.route("/api/calibrate", methods=["POST", "OPTIONS"])
def api_calibrate():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    
    data = request.get_json(silent=True) or {}
    target_x = data.get("x")
    target_y = data.get("y")
    image_data = data.get("image")
    
    if target_x is None or target_y is None or not image_data:
        return jsonify({"error": "Missing target coordinates or image data"}), 400
        
    try:
        if "," in image_data:
            image_data = image_data.split(",", 1)[1]

        padding = 4 - len(image_data) % 4
        if padding != 4:
            image_data += "=" * padding

        image_bytes = base64.b64decode(image_data)
        image_array = np.frombuffer(image_bytes, dtype=np.uint8)
        frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        
        if frame is None:
            return jsonify({"error": "Failed to decode image"}), 400
            
        landmarks = gaze_detector.get_landmarks(frame)
        if not landmarks:
            return jsonify({"error": "No face detected. Please ensure your face is fully visible in the frame."}), 400
            
        sample = {
            "x": target_x,
            "y": target_y,
            "landmarks": landmarks,
            "timestamp": time.time()
        }
        
        state.calibration_samples.append(sample)
        
        import json
        filepath = "calibration_data.json"
        existing_data = []
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                try:
                    existing_data = json.load(f)
                except Exception:
                    existing_data = []
        existing_data.append(sample)
        with open(filepath, "w") as f:
            json.dump(existing_data, f, indent=2)
            
        return jsonify({"success": True, "samples_count": len(state.calibration_samples)}), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --------------------------------------------------------------------------- #
# API Routes
# --------------------------------------------------------------------------- #
@bp.route("/session/join", methods=["POST", "OPTIONS"])
@bp.route("/session/heartbeat", methods=["POST", "OPTIONS"])
def session_join():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.get_json(silent=True) or {}
    role = (data.get("role") or "").strip().lower()
    display_name = (data.get("displayName") or "").strip()

    if role not in {"interviewer", "candidate"}:
        return jsonify({"error": "role must be interviewer or candidate"}), 400

    state.active_participants[role] = {
        "displayName": display_name or role.title(),
        "lastSeen": time.time(),
    }

    if role == "candidate":
        # Fresh interview: clear all per-session detector state (pose EMA,
        # anomaly/reflection streaks, YOLO hit counters, blink history) so
        # nothing leaks between candidates or interviews.
        gaze_detector.reset_temporal_state()
        _reset_liveness_state()

    return jsonify(_session_status_payload())

@bp.route("/session/leave", methods=["POST", "OPTIONS"])
@bp.route("/leave", methods=["POST", "OPTIONS"])
def session_leave():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.get_json(silent=True) or {}
    role = (data.get("role") or "").strip().lower()
    if role:
        state.active_participants.pop(role, None)

    if role == "candidate":
        # The candidate left: drop the meeting's analysis lock right away
        # (the idle janitor is the safety net for abrupt disconnects). Use the
        # SAME key the analysis routes lock under (payload meetingId, else the
        # verified session meeting) so a leave can't miss the in-use lock.
        gaze_detector.meeting_locks.drop(_request_meeting_id())

    return jsonify(_session_status_payload())

@bp.route("/session/status", methods=["GET"])
def session_status():
    return jsonify(_session_status_payload())

@bp.route("/create-room", methods=["POST", "OPTIONS"])
@limiter.limit("10 per minute")  # Limit room creation to prevent abuse
def create_room():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    
    # Check for session-based authentication first
    if session.get('authenticated'):
        # Check if user has permission to create rooms
        user_role = session.get('role', 'interviewer')
        if user_role not in ['admin', 'interviewer']:
            return jsonify({"error": "Insufficient permissions to create rooms"}), 403
        
        data = request.get_json(silent=True) or {}
    else:
        # Fall back to password-based auth for backward compatibility
        data = request.get_json(silent=True) or {}
        password = data.get("password", "").strip()
        expected_password = os.getenv("HOST_PASSWORD", "admin123")
        if password != expected_password:
            return jsonify({"error": "Invalid host credentials"}), 401
    
    host_name = (data.get("hostName") or session.get('full_name', 'Host')).strip()
    title = (data.get("title") or "Interview Session").strip()
    meeting_id = str(uuid.uuid4())[:8].upper()
    
    # Create meeting room in MongoDB with persistence
    metadata = {
        "partner_id": data.get("partnerId", ""),
        "created_by": session.get('username', 'web'),
        "user_id": session.get('user_id', ''),
        "user_role": session.get('role', 'interviewer'),
        "host_ip": request.remote_addr
    }
    room = state.create_meeting_room(meeting_id, host_name, title, metadata)
    
    # Authenticate host (session-based)
    session['host_authenticated'] = True
    session['host_name'] = host_name
    session['host_role'] = session.get('role', 'interviewer')
    
    # Generate signed link for candidate
    signed_link = _generate_signed_link(meeting_id)
    
    # Log room creation to audit log
    state.add_audit_log(meeting_id, "room_created", "Meeting Room Created", 
                     f"Room {meeting_id} created by {host_name} ({session.get('role', 'interviewer')})", 
                     confidence="High", is_critical=False)
    
    return jsonify({"meetingId": meeting_id, "link": signed_link}), 201

# ============================================================
# AUTHENTICATION ENDPOINTS (NEW)
# ============================================================

@bp.route("/auth/register", methods=["POST", "OPTIONS"])
@limiter.limit("5 per minute")
def register_user():
    """Register a new user account."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    email = data.get("email", "").strip()
    password = data.get("password", "")
    full_name = data.get("fullName", "").strip()
    role = data.get("role", "interviewer").strip().lower()
    
    # Validation
    if not username or len(username) < 3:
        return jsonify({"error": "Username must be at least 3 characters"}), 400
    if not email or "@" not in email:
        return jsonify({"error": "Valid email is required"}), 400
    if not password or len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    
    # Check if this is the first user (make them admin)
    existing_users = db.get_all_users()
    if len(existing_users) == 0:
        role = "admin"
    
    result = db.create_user(username, email, password, full_name, role)
    
    if result.get("success"):
        return jsonify(result), 201
    else:
        return jsonify(result), 400

@bp.route("/auth/login", methods=["POST", "OPTIONS"])
@limiter.limit("10 per minute")
def login_user():
    """Authenticate a user and create a session."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    remember_me = data.get("rememberMe", False)
    
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400
    
    # Authenticate user
    auth_result = db.authenticate_user(username, password)
    
    if auth_result.get("success"):
        # Create session
        session_result = db.create_session(
            auth_result["user_id"], 
            remember_me
        )
        
        if session_result.get("success"):
            # Set session data
            session['user_id'] = auth_result["user_id"]
            session['username'] = auth_result["username"]
            session['email'] = auth_result["email"]
            session['full_name'] = auth_result["full_name"]
            session['role'] = auth_result["role"]
            session['session_token'] = session_result["session_token"]
            session['authenticated'] = True
            
            return jsonify({
                "success": True,
                "user": {
                    "username": auth_result["username"],
                    "email": auth_result["email"],
                    "full_name": auth_result["full_name"],
                    "role": auth_result["role"]
                },
                "session_token": session_result["session_token"],
                "expires_at": session_result["expires_at"]
            }), 200
        else:
            return jsonify({"error": "Failed to create session"}), 500
    else:
        return jsonify({"error": auth_result.get("error", "Authentication failed")}), 401

@bp.route("/auth/logout", methods=["POST", "OPTIONS"])
def logout_user():
    """Logout the current user."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    
    # Clear session
    session_token = session.get('session_token')
    if session_token:
        db.delete_session(session_token)
    
    session.clear()
    
    return jsonify({"success": True, "message": "Logged out successfully"}), 200

@bp.route("/auth/me", methods=["GET"])
def get_current_user():
    """Get current authenticated user info."""
    if not session.get('authenticated'):
        return jsonify({"error": "Not authenticated"}), 401
    
    return jsonify({
        "success": True,
        "user": {
            "username": session.get('username'),
            "email": session.get('email'),
            "full_name": session.get('full_name'),
            "role": session.get('role')
        }
    }), 200

@bp.route("/auth/users", methods=["GET", "OPTIONS"])
def get_users():
    """Get all users (admin only)."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    
    # Check if user is admin
    if session.get('role') != 'admin':
        return jsonify({"error": "Admin access required"}), 403
    
    users = db.get_all_users()
    return jsonify({"success": True, "users": users}), 200

@bp.route("/auth/users/<user_id>", methods=["PUT", "OPTIONS"])
def update_user_role(user_id):
    """Update user role (admin only)."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    
    # Check if user is admin
    if session.get('role') != 'admin':
        return jsonify({"error": "Admin access required"}), 403
    
    data = request.get_json(silent=True) or {}
    new_role = data.get("role", "").strip().lower()
    
    if not new_role:
        return jsonify({"error": "Role is required"}), 400
    
    result = db.update_user_role(user_id, new_role)
    
    if result.get("success"):
        return jsonify(result), 200
    else:
        return jsonify(result), 400

@bp.route("/auth/users/<user_id>", methods=["DELETE", "OPTIONS"])
def delete_user(user_id):
    """Delete a user (admin only)."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    
    # Check if user is admin
    if session.get('role') != 'admin':
        return jsonify({"error": "Admin access required"}), 403
    
    result = db.delete_user(user_id)
    
    if result.get("success"):
        return jsonify(result), 200
    else:
        return jsonify(result), 400

# ============================================================
# MFA/TOTP ENDPOINTS (NEW)
# ============================================================

@bp.route("/auth/mfa/enable", methods=["POST", "OPTIONS"])
def enable_mfa():
    """Enable MFA for the current user."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    
    if not session.get('authenticated'):
        return jsonify({"error": "Not authenticated"}), 401
    
    user_id = session.get('user_id')
    result = db.enable_mfa(user_id)
    
    if result.get("success"):
        # Generate QR code for TOTP setup
        import qrcode
        from io import BytesIO
        
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(result["qr_code_url"])
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Convert to base64
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        
        return jsonify({
            "success": True,
            "secret": result["secret"],
            "qr_code": f"data:image/png;base64,{img_str}",
            "setup_url": result["qr_code_url"]
        }), 200
    else:
        return jsonify(result), 400

@bp.route("/auth/mfa/disable", methods=["POST", "OPTIONS"])
def disable_mfa():
    """Disable MFA for the current user."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    
    if not session.get('authenticated'):
        return jsonify({"error": "Not authenticated"}), 401
    
    user_id = session.get('user_id')
    result = db.disable_mfa(user_id)
    
    if result.get("success"):
        return jsonify(result), 200
    else:
        return jsonify(result), 400

@bp.route("/auth/mfa/verify", methods=["POST", "OPTIONS"])
def verify_mfa():
    """Verify TOTP code during login."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    
    if not session.get('authenticated'):
        return jsonify({"error": "Not authenticated"}), 401
    
    data = request.get_json(silent=True) or {}
    totp_code = data.get("code", "").strip()
    user_id = session.get('user_id')
    
    if not totp_code:
        return jsonify({"error": "TOTP code is required"}), 400
    
    if db.verify_totp(user_id, totp_code):
        # Mark MFA as verified in session
        session['mfa_verified'] = True
        return jsonify({"success": True, "message": "MFA verified successfully"}), 200
    else:
        return jsonify({"error": "Invalid TOTP code"}), 401

@bp.route("/api/room/<meeting_id>", methods=["GET"])
def get_room(meeting_id):
    room = state.get_meeting_room(meeting_id.upper())
    if not room:
        return jsonify({"error": "Room not found"}), 404
    return jsonify(room)

@bp.route("/api/room/<meeting_id>/invite-link", methods=["GET"])
def regenerate_invite_link(meeting_id):
    """Regenerate signed invite link for an existing room."""
    if not session.get('host_authenticated'):
        return jsonify({"error": "Unauthorized"}), 401
    
    room = state.get_meeting_room(meeting_id.upper())
    if not room:
        return jsonify({"error": "Room not found"}), 404
    
    signed_link = _generate_signed_link(meeting_id.upper())
    return jsonify({"link": signed_link})

@bp.route("/embed-view")
def embed_view():
    """White-label embed view for partner integration."""
    meeting_id = request.args.get('meeting_id', '').upper()
    partner_id = request.args.get('partner_id', '')
    api_key = request.args.get('api_key', '')
    
    # Validate API key for partner access
    if not _validate_partner_api_key(partner_id, api_key):
        return "Unauthorized: Invalid partner credentials", 403
    
    # Verify meeting exists
    room = state.meeting_rooms.get(meeting_id)
    if not room:
        return "Meeting not found", 404
    
    # Parse configuration
    try:
        import json
        branding = json.loads(request.args.get('branding', '{}'))
        features = json.loads(request.args.get('features', '{}'))
        show_header = request.args.get('show_header', 'true').lower() == 'true'
        show_controls = request.args.get('show_controls', 'true').lower() == 'true'
        show_stats = request.args.get('show_stats', 'true').lower() == 'true'
        theme = request.args.get('theme', 'light')
    except:
        branding = {}
        features = {}
        show_header = True
        show_controls = True
        show_stats = True
        theme = 'light'
    
    return render_template(
        "embed_view.html",
        meeting_id=meeting_id,
        room_title=room["title"],
        room_host=room["host"],
        partner_id=partner_id,
        branding=branding,
        features=features,
        show_header=show_header,
        show_controls=show_controls,
        show_stats=show_stats,
        theme=theme
    )

def _validate_partner_api_key(partner_id, api_key):
    """Validate partner API key for white-label access."""
    # In production, this would check against a database of partners
    # For now, we'll allow basic validation
    if not partner_id or not api_key:
        return False
    
    # TODO: Implement proper partner validation
    # - Check against database of registered partners
    # - Validate API key signature
    # - Check if partner is active and in good standing
    # - Rate limit per partner
    
    # For development, allow any non-empty values
    return len(partner_id) > 0 and len(api_key) > 0

# ─── Analytics and Audit Log Routes ────────────────────────────────────────

@bp.route("/api/audit/<meeting_id>", methods=["GET"])
def get_audit_logs(meeting_id):
    """Get audit logs for a meeting from MongoDB."""
    if not session.get('host_authenticated'):
        return jsonify({"error": "Unauthorized"}), 401
    
    limit = request.args.get('limit', 100, type=int)
    logs = db.get_audit_logs(meeting_id.upper(), limit)
    
    return jsonify({
        "meeting_id": meeting_id.upper(),
        "logs": logs,
        "total": len(logs)
    })

@bp.route("/api/analytics/<meeting_id>", methods=["GET"])
def get_meeting_analytics(meeting_id):
    """Get comprehensive analytics for a meeting."""
    if not session.get('host_authenticated'):
        return jsonify({"error": "Unauthorized"}), 401
    
    analytics = db.get_meeting_analytics(meeting_id.upper())
    
    return jsonify(analytics)

@bp.route("/api/partner/<partner_id>/analytics", methods=["GET"])
def get_partner_analytics(partner_id):
    """Get analytics for a partner over time period."""
    if not session.get('host_authenticated'):
        return jsonify({"error": "Unauthorized"}), 401
    
    days = request.args.get('days', 30, type=int)
    analytics = db.get_partner_analytics(partner_id, days)
    
    return jsonify(analytics)

@bp.route("/api/session/<meeting_id>/start", methods=["POST"])
def start_session():
    """Start a session record in MongoDB."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    
    data = request.get_json(silent=True) or {}
    meeting_id = data.get("meetingId", "").upper()
    
    if not _is_candidate_request():
        return jsonify({"error": "Unauthorized"}), 403
    
    state.start_session(meeting_id)
    
    return jsonify({"success": True, "meeting_id": meeting_id})

@bp.route("/api/session/<meeting_id>/end", methods=["POST"])
def end_session():
    """End a session record in MongoDB."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    
    data = request.get_json(silent=True) or {}
    meeting_id = data.get("meetingId", "").upper()
    
    if not _is_candidate_request():
        return jsonify({"error": "Unauthorized"}), 403
    
    state.end_session(meeting_id)
    
    return jsonify({"success": True, "meeting_id": meeting_id})

@bp.route("/api/room/<meeting_id>/invite", methods=["GET", "OPTIONS"])
def room_invite(meeting_id):
    """Return a freshly signed candidate invite link (host only)."""
    if request.method == "OPTIONS":
        return jsonify({}), 200

    if not session.get("host_authenticated"):
        return jsonify({"error": "Host authentication required"}), 401

    meeting_id = meeting_id.upper()
    if meeting_id not in state.meeting_rooms:
        return jsonify({"error": "Room not found"}), 404

    return jsonify({"meetingId": meeting_id, "link": _generate_signed_link(meeting_id)}), 200

@bp.route("/meet/<meeting_id>")
def meeting_room(meeting_id):
    meeting_id = meeting_id.upper()
    sig = request.args.get("sig")
    expires_str = request.args.get("expires")
    
    if not sig or not expires_str:
        return "Unauthorized: Missing signature or expiry parameters. Please use the signed link sent by the host.", 403
        
    try:
        expires = int(expires_str)
    except ValueError:
        return "Unauthorized: Invalid expiry parameter.", 403
        
    if time.time() > expires:
        return "Unauthorized: Invitation link has expired.", 403
        
    sig_payload = f"{meeting_id}-{expires}"
    expected_sig = hmac.new(current_app.secret_key.encode(), sig_payload.encode(), hashlib.sha256).hexdigest()
    
    if not hmac.compare_digest(sig, expected_sig):
        return "Unauthorized: Invalid signature.", 403
        
    session['candidate_verified_meeting'] = meeting_id
    return redirect(f"/login/candidate?meeting_id={meeting_id}")

@bp.route("/dashboard/<meeting_id>")
def dashboard_room(meeting_id):
    return redirect(url_for('host_dashboard', meeting_id=meeting_id))

@bp.route("/create_meeting", methods=["POST", "OPTIONS"])
def create_meeting():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.get_json(silent=True) or {}
    meeting = {
        "sender": data.get("sender", "").strip(),
        "title": data.get("title", "").strip(),
        "description": data.get("description", "").strip(),
        "time": data.get("time", "").strip(),
        "createdAt": time.time(),
    }
    state.meetings.append(meeting)
    return jsonify({"message": "Meeting created successfully", "meeting": meeting}), 201

@bp.route("/get_meetings", methods=["GET"])
def get_meetings():
    return jsonify(state.meetings)

@bp.route("/ai_insights", methods=["POST", "OPTIONS"])
def ai_insights():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.get_json(silent=True) or {}
    text = data.get("description", "").strip()

    if not text:
        return jsonify({"error": "Meeting description is required"}), 400

    if not client:
        return jsonify(
            {
                "insights": local_meeting_insights(text),
                "source": "local-fallback",
                "message": "Set OPENAI_API_KEY to enable OpenAI-powered analysis.",
            }
        )

    prompt = (
        "Analyze this meeting description and return concise meeting intelligence.\n\n"
        f"Meeting:\n{text}\n\n"
        "Include:\n"
        "1. Summary\n"
        "2. Priority: High, Medium, or Low\n"
        "3. Importance prediction: urgent or not urgent\n"
        "4. Action items\n"
        "5. Smart suggestion (reschedule / attend / delegate / follow up)\n"
    )

    try:
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": "You are an AI meeting intelligence assistant. Be concise, practical, and decisive.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
        )
        return jsonify({"insights": response.choices[0].message.content, "source": "openai"})
    except Exception as exc:
        return jsonify({
            "insights": local_meeting_insights(text),
            "source": "local-fallback",
            "error": str(exc),
        }), 200

@bp.route("/gaze", methods=["GET"])
def gaze():
    return jsonify(state.current_gaze)

@bp.route("/gaze-frame", methods=["POST", "OPTIONS"])
@limiter.limit("10 per second")  # Limit frame analysis to prevent abuse
def gaze_frame():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    if not _is_candidate_request():
        return jsonify({"error": "Candidate session required for gaze analysis"}), 403

    frame, decode_error = _decode_request_frame()
    if decode_error:
        return jsonify({"error": decode_error}), 400

    try:
        with gaze_detector.meeting_locks.get(_request_meeting_id()):
            state.current_gaze = gaze_detector.analyze_frame(frame)
        return jsonify(state.current_gaze)

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@bp.route("/health", methods=["GET"])
def health():
    return jsonify(
        {
            "ok": True,
            "mode": "browser-frame-analysis",
            "mediapipe": gaze_detector.mediapipe_available,
            "openaiConfigured": client is not None,
            "advancedDetector": gaze_detector.metadata,
        }
    )

from audio_analyzer import process_audio_chunk, audio_state
from liveness_detector import analyze_liveness, _reset_liveness_state, issue_challenge

@bp.route("/analyze", methods=["POST", "OPTIONS"])
@limiter.limit("5 per second")  # Limit analysis to prevent abuse
def analyze():
    """Combined gaze + liveness analysis in one request.

    The frontend previously fired two independent requests every ~1.5s
    (/gaze-frame at 1s + /liveness-frame at 1.5s), each decoding the base64
    frame and running MediaPipe face mesh on ~the same image — the single
    most expensive inference in the pipeline — twice. This endpoint runs
    face mesh ONCE, feeds the same landmarks to both the gaze detector and
    the blink-based liveness analyzer, and returns both verdicts in one
    response: one HTTP round-trip, one inference, one decode.
    """
    if request.method == "OPTIONS":
        return jsonify({}), 200

    if not _is_candidate_request():
        return jsonify({"error": "Candidate session required for frame analysis"}), 403

    frame, decode_error = _decode_request_frame()
    if decode_error:
        return jsonify({"error": decode_error}), 400

    try:
        # Serialize detector access PER MEETING: analyze_frame mutates
        # per-session state (pose EMA, streaks, YOLO hits), so concurrent
        # requests for the same meeting must not interleave — but different
        # meetings each get their own lock (no cross-user blocking). One
        # candidate sends one request at a time (the client gates with an
        # in-flight flag), and the lock makes it safe even under concurrent
        # callers within the meeting.
        with gaze_detector.meeting_locks.get(_request_meeting_id()):
            # return_landmarks keeps the MediaPipe mesh local to this call: a
            # concurrent meeting must never be able to overwrite a shared
            # attribute between analyze_frame returning and this route reading
            # it, or one candidate's liveness would be computed from another's
            # face landmarks.
            result, landmarks = gaze_detector.analyze_frame(
                frame, return_landmarks=True
            )
            result["liveness"] = analyze_liveness(
                frame, landmarks=landmarks
            )
        return jsonify(result)

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@bp.route("/liveness-frame", methods=["POST", "OPTIONS"])
@limiter.limit("10 per second")  # Limit liveness checks to prevent abuse
def liveness_frame():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    if not _is_candidate_request():
        return jsonify({"error": "Candidate session required for liveness analysis"}), 403

    frame, decode_error = _decode_request_frame()
    if decode_error:
        return jsonify({"error": decode_error}), 400

    try:
        # Reuse the gaze detector's MediaPipe landmarks (no second model load)
        # — blink-based liveness needs them to compute the Eye Aspect Ratio.
        # Held under the meeting lock: get_landmarks runs face_mesh.detect() on
        # the shared detector, and concurrent MediaPipe detect calls on one
        # instance are not thread-safe (per-meeting, so interviews don't block
        # each other).
        with gaze_detector.meeting_locks.get(_request_meeting_id()):
            landmarks = gaze_detector.get_landmarks(frame)
            return jsonify(analyze_liveness(frame, landmarks=landmarks))

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@bp.route("/liveness-challenge", methods=["POST", "OPTIONS"])
def liveness_challenge():
    """Issue a random blink/head-move liveness challenge to the candidate.

    The frontend shows a subtle prompt on the candidate's screen right after
    this returns; the ongoing /liveness-frame responses carry the challenge
    verdict (passed/failed) once the response window elapses.
    """
    if request.method == "OPTIONS":
        return jsonify({}), 200

    if not _is_candidate_request():
        return jsonify({"error": "Candidate session required for liveness challenge"}), 403

    data = request.get_json(silent=True) or {}
    challenge_type = (data.get("type") or "blink").strip().lower()
    result = issue_challenge(challenge_type)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result), 200

@bp.route("/analyze-audio", methods=["POST", "OPTIONS"])
@limiter.limit("10 per second")  # Limit audio analysis to prevent abuse
def analyze_audio():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    if not _is_candidate_request():
        return jsonify({"error": "Candidate session required for audio analysis"}), 403

    payload     = request.get_json(silent=True) or {}
    audio_data  = payload.get("audio")       # base64 PCM
    sample_rate = payload.get("sampleRate", 16000)
    mouth_open  = payload.get("mouthOpen",  True)

    if not audio_data:
        return jsonify({"error": "Missing audio data"}), 400

    try:
        # Decode base64 PCM
        if "," in audio_data:
            audio_data = audio_data.split(",", 1)[1]

        padding = 4 - len(audio_data) % 4
        if padding != 4:
            audio_data += "=" * padding

        pcm_bytes = base64.b64decode(audio_data)

        result = process_audio_chunk(
            pcm_bytes   = pcm_bytes,
            sample_rate = sample_rate,
            mouth_open  = mouth_open,
        )
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/audio-state", methods=["GET"])
def audio_state_route():
    return jsonify(audio_state)

import json
@bp.route("/analyze_answer", methods=["POST", "OPTIONS"])
def analyze_answer():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    if not _is_candidate_request():
        return jsonify({"error": "Candidate session required for answer analysis"}), 403

    if client is None:
        return jsonify({"error": "OpenAI client not configured"}), 500

    data = request.get_json(silent=True) or {}
    voice_metrics = data.get("voiceMetrics", {})
    if not voice_metrics:
        return jsonify({"error": "No voice metrics provided"}), 400
    
    prompt_content = f"Vocal Tone Context (from audio analysis of the candidate's last answer):\n"
    prompt_content += f"- Speaking Duration: {voice_metrics.get('durationSeconds')} seconds\n"
    prompt_content += f"- Pitch Variation: {voice_metrics.get('pitchStdDev')}Hz (Monotone Flag: {voice_metrics.get('isMonotone')})\n"
    prompt_content += f"- Whisper Incidents Detected: {voice_metrics.get('whisperCount')}\n"
    prompt_content += f"- Vocal Stress Level: {voice_metrics.get('stressLevel')}\n"
    prompt_content += "\nAnalyze these vocal telemetry metrics. If the delivery is highly monotone with extremely low pitch variation (no stress), it strongly suggests reading from a screen or robotic AI behavior. If there are whispers, it suggests off-camera coaching. Normal human speech has high pitch variation and mild stress. Return your analysis."

    try:
        result = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{
                "role": "system",
                "content": "You are a cheating detection AI. Evaluate the candidate's vocal delivery metrics to determine if they sound suspiciously robotic, reading from a script, or getting off-camera help. Return ONLY a JSON object: {\"score\": 0-100, \"reason\": \"string\", \"verdict\": \"human\"|\"suspicious\"|\"ai_generated\"}"
            }, {
                "role": "user",
                "content": prompt_content
            }],
            max_tokens=250,
            temperature=0.2
        )
        
        content = result.choices[0].message.content
        analysis_json = json.loads(content)
        return jsonify({"analysis": analysis_json})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

import urllib.request
@bp.route("/analyze_network", methods=["POST", "OPTIONS"])
def analyze_network():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    if not _is_candidate_request():
        return jsonify({"error": "Candidate session required for network analysis"}), 403

    data = request.get_json(silent=True) or {}
    client_ip = data.get("ip")
    
    if not client_ip:
        return jsonify({"error": "No IP provided"}), 400

    try:
        # Query ip-api.com for proxy/VPN data (HTTP is fine from backend)
        url = f"http://ip-api.com/json/{client_ip}?fields=status,country,city,isp,proxy,hosting,query"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode())
            
        if result.get("status") != "success":
            return jsonify({"error": "IP lookup failed"}), 500

        is_suspicious = result.get("proxy", False) or result.get("hosting", False)
        
        return jsonify({
            "ip": result.get("query"),
            "location": f"{result.get('city')}, {result.get('country')}",
            "isp": result.get("isp"),
            "is_vpn_or_proxy": is_suspicious
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

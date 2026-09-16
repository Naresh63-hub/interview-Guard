import os
import time
import uuid
import base64
import hmac
import hashlib
import json
import re
import urllib.request
import urllib.parse
import cv2
import numpy as np
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
    limiter.init_app(app)
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
    # Redirect to main login page
    return redirect(url_for('main.login'))

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
    # Accept both wire names: the login page sends displayName, but the
    # older candidate form used candidateName.
    display_name = (data.get("displayName") or data.get("candidateName") or "").strip()

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

@bp.route("/host_dashboard")
def host_dashboard_landing():
    """Post-login landing: send an authenticated host straight into a meeting.

    Reuses the host's most recent meeting when one exists (so a Google /
    email login drops them back into the room they were in); otherwise a
    fresh meeting is created on the spot. Never shows the login page again
    once the host is authenticated.
    """
    is_authenticated_host = session.get('authenticated') and session.get('role') in ('admin', 'interviewer')
    if not (is_authenticated_host or session.get('host_authenticated')):
        return redirect(url_for('main.home'))

    # Reuse the host's most recent meeting if one exists
    user_id = session.get('user_id')
    if user_id:
        room = state.get_latest_meeting_for_user(user_id)
        if room and room.get("id"):
            session['host_authenticated'] = True
            return redirect(url_for('main.host_dashboard', meeting_id=room["id"]))

    # Otherwise create a fresh meeting and go straight in
    meeting_id, _ = _create_meeting({})
    return redirect(url_for('main.host_dashboard', meeting_id=meeting_id))

@bp.route("/host_dashboard/<meeting_id>")
def host_dashboard(meeting_id):
    is_authenticated_host = session.get('authenticated') and session.get('role') in ('admin', 'interviewer')
    if not (is_authenticated_host or session.get('host_authenticated')):
        return redirect(url_for('main.login'))
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
    # Mongo-aware lookup: after a server restart the in-memory dict is empty,
    # so a raw dict check would bounce the candidate back to the join page
    # even though the room exists in MongoDB.
    room = state.get_meeting_room(meeting_id)
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

        # Keep samples in memory only: raw face landmarks are privacy-
        # sensitive and were previously written to calibration_data.json on
        # disk. Nothing reads that file back, so drop the persistence.
        state.calibration_samples.append(sample)

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
        expected_password = os.getenv("HOST_PASSWORD")
        if not expected_password or not hmac.compare_digest(password, expected_password):
            return jsonify({"error": "Invalid host credentials"}), 401

    meeting_id, signed_link = _create_meeting(data)
    return jsonify({"meetingId": meeting_id, "link": signed_link}), 201

def _create_meeting(data):
    """Shared room-creation core used by /create-room and the post-login
    /host_dashboard landing. Returns (meeting_id, signed_link) and sets the
    host session flags."""
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
                     confidence=1.0, is_critical=False)
    
    return meeting_id, signed_link

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
    
    # Verify meeting exists (Mongo-aware; the in-memory dict is empty after
    # a server restart).
    room = state.get_meeting_room(meeting_id)
    if not room:
        return "Meeting not found", 404
    
    # Parse configuration
    try:
        branding = json.loads(request.args.get('branding', '{}'))
        features = json.loads(request.args.get('features', '{}'))
        show_header = request.args.get('show_header', 'true').lower() == 'true'
        show_controls = request.args.get('show_controls', 'true').lower() == 'true'
        show_stats = request.args.get('show_stats', 'true').lower() == 'true'
        theme = request.args.get('theme', 'light')
    except (TypeError, ValueError, json.JSONDecodeError):
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

def _load_partner_keys() -> dict:
    """Parse PARTNER_API_KEYS="partner1:key1,partner2:key2" from the env.

    Returned map is {partner_id: api_key}. The keys live in the environment,
    never in code, so they can be rotated without a redeploy.
    """
    raw = os.getenv("PARTNER_API_KEYS", "")
    partners = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        pid, _, key = entry.partition(":")
        pid = pid.strip().lower()
        key = key.strip()
        if pid and key:
            partners[pid] = key
    return partners


def _validate_partner_api_key(partner_id, api_key):
    """Validate partner API key for white-label access against PARTNER_API_KEYS."""
    if not partner_id or not api_key:
        return False
    expected = _load_partner_keys().get(str(partner_id).strip().lower())
    if expected is None:
        return False
    # Constant-time compare so a timing side channel can't leak the key.
    return hmac.compare_digest(expected, str(api_key).strip())

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
def start_session(meeting_id):
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
def end_session(meeting_id):
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
    # Use the Mongo-aware lookup: the in-memory dict is empty after a server
    # restart, so checking `state.meeting_rooms` alone would 404 on rooms
    # that exist in MongoDB.
    if not state.get_meeting_room(meeting_id):
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
    if not _is_candidate_request() and not _is_host_request():
        return jsonify({"error": "Session required"}), 403
    meeting_id = _request_meeting_id()
    if not meeting_id:
        return jsonify({"error": "No meeting"}), 400
    
    gaze_state = state.current_gaze.get(meeting_id, {
        "direction": "WAITING",
        "lookingAway": False,
        "faceDetected": False,
        "timestamp": time.time(),
    }) if isinstance(state.current_gaze, dict) and meeting_id in state.current_gaze else {
        "direction": "WAITING",
        "lookingAway": False,
        "faceDetected": False,
        "timestamp": time.time(),
    }
    return jsonify(gaze_state)

@bp.route("/gaze-frame", methods=["POST", "OPTIONS"])
@limiter.limit("10 per second")
def gaze_frame():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    if not _is_candidate_request():
        return jsonify({"error": "Candidate session required for gaze analysis"}), 403

    frame, decode_error = _decode_request_frame()
    if decode_error:
        return jsonify({"error": decode_error}), 400

    try:
        meeting_id = _request_meeting_id()
        with gaze_detector.meeting_locks.get(meeting_id):
            if not isinstance(state.current_gaze, dict) or "direction" in state.current_gaze:
                state.current_gaze = {}
            res = gaze_detector.analyze_frame(frame)
            state.current_gaze[meeting_id] = res
        return jsonify(res)

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
        # Query ip-api.com for proxy/VPN data (HTTPS: avoids sending the
        # probe over plaintext and closes the earlier SSRF-via-IP-parameter
        # vector; the IP is still user-supplied, so it is URL-encoded and
        # restricted to literal IPv4/IPv6 addresses below).
        client_ip = client_ip.strip()
        if not re.fullmatch(r"[0-9a-fA-F:.%]+", client_ip):
            return jsonify({"error": "Invalid IP address format"}), 400
        url = f"https://ip-api.com/json/{urllib.parse.quote(client_ip)}?fields=status,country,city,isp,proxy,hosting,query"
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

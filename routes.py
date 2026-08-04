import os
import time
import uuid
import base64
import cv2
import numpy as np
import hmac
import hashlib
from flask import Blueprint, jsonify, redirect, render_template, request, url_for, session, current_app

import state
from advanced_gaze_detector import AdvancedGazeDetector

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

@bp.route("/login/host")
def login_host():
    return render_template("login_host.html")

@bp.route("/login/candidate")
def login_candidate():
    meeting_id = request.args.get('meeting_id', '').upper()
    if session.get('candidate_verified_meeting') != meeting_id:
        return "Unauthorized: Please use the signed link sent by the host", 403
    return render_template("login_candidate.html")

@bp.route("/host_dashboard/<meeting_id>")
def host_dashboard(meeting_id):
    if not session.get('host_authenticated'):
        return redirect("/login/host")
    room = state.meeting_rooms.get(meeting_id.upper())
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

    return jsonify(_session_status_payload())

@bp.route("/session/status", methods=["GET"])
def session_status():
    return jsonify(_session_status_payload())

@bp.route("/create-room", methods=["POST", "OPTIONS"])
def create_room():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    data = request.get_json(silent=True) or {}
    password = data.get("password", "").strip()
    expected_password = os.getenv("HOST_PASSWORD", "admin123")
    if password != expected_password:
        return jsonify({"error": "Invalid host credentials"}), 401
        
    host_name = (data.get("hostName") or "Host").strip()
    title = (data.get("title") or "Interview Session").strip()
    meeting_id = str(uuid.uuid4())[:8].upper()
    state.meeting_rooms[meeting_id] = {
        "id": meeting_id,
        "title": title,
        "host": host_name,
        "createdAt": time.time(),
        "participants": [],
        "status": "waiting",
    }
    
    # Authenticate host
    session['host_authenticated'] = True
    session['host_name'] = host_name
    
    # Generate signed link for candidate
    expires = int(time.time()) + 86400  # 24 hours from now
    sig_payload = f"{meeting_id}-{expires}"
    signature = hmac.new(current_app.secret_key.encode(), sig_payload.encode(), hashlib.sha256).hexdigest()
    signed_link = f"/meet/{meeting_id}?sig={signature}&expires={expires}"
    
    return jsonify({"meetingId": meeting_id, "link": signed_link}), 201

@bp.route("/api/room/<meeting_id>", methods=["GET"])
def get_room(meeting_id):
    room = state.meeting_rooms.get(meeting_id.upper())
    if not room:
        return jsonify({"error": "Room not found"}), 404
    return jsonify(room)

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
def gaze_frame():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    if not _is_candidate_request():
        return jsonify({"error": "Candidate session required for gaze analysis"}), 403

    payload = request.get_json(silent=True) or {}
    image_data = payload.get("image")

    if not image_data:
        return jsonify({"error": "Missing image"}), 400

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
            return jsonify({"error": "Could not decode image"}), 400

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
from liveness_detector import analyze_liveness

@bp.route("/liveness-frame", methods=["POST", "OPTIONS"])
def liveness_frame():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    if not _is_candidate_request():
        return jsonify({"error": "Candidate session required for liveness analysis"}), 403

    payload = request.get_json(silent=True) or {}
    image_data = payload.get("image")

    if not image_data:
        return jsonify({"error": "Missing image"}), 400

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
            return jsonify({"error": "Could not decode image"}), 400

        return jsonify(analyze_liveness(frame))

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@bp.route("/analyze-audio", methods=["POST", "OPTIONS"])
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

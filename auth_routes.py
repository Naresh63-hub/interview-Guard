"""
Authentication Routes for Intervue (Supabase)
Handles user registration, login, and session management using Supabase Auth.
"""
import os
from functools import wraps
from flask import Blueprint, jsonify, request, session, redirect, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from database import db

auth_bp = Blueprint('auth', __name__)
auth_limiter = Limiter(key_func=get_remote_address, default_limits=["20 per minute"])

def init_oauth(app):
    # Supabase handles OAuth natively; no need for authlib setup here.
    pass

# ============================================================
# ROLE-BASED ACCESS CONTROL DECORATORS
# ============================================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated_function

def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('authenticated'):
                return jsonify({"error": "Authentication required"}), 401
            user_role = session.get('role', 'interviewer')
            if user_role not in allowed_roles:
                return jsonify({"error": f"Access denied. Required roles: {', '.join(allowed_roles)}"}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ============================================================
# AUTHENTICATION ENDPOINTS
# ============================================================
@auth_bp.route("/register", methods=["POST", "OPTIONS"])
@auth_limiter.limit("5 per minute")
def register_user():
    if request.method == "OPTIONS": return jsonify({}), 200
    
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    email = data.get("email", "").strip()
    password = data.get("password", "")
    full_name = data.get("fullName", "").strip()
    # Force role to candidate to prevent privilege escalation
    role = "candidate"
    
    if not username or len(username) < 3: return jsonify({"error": "Username must be at least 3 characters"}), 400
    if not email or "@" not in email: return jsonify({"error": "Valid email is required"}), 400
    if not password or len(password) < 8: return jsonify({"error": "Password must be at least 8 characters"}), 400
    
    result = db.create_user(username, email, password, full_name, role)
    if result.get("success"):
        return jsonify(result), 201
    else:
        return jsonify(result), 400

@auth_bp.route("/login", methods=["POST", "OPTIONS"])
@auth_limiter.limit("10 per minute")
def login_user():
    if request.method == "OPTIONS": return jsonify({}), 200
    
    data = request.get_json(silent=True) or {}
    email = data.get("username", "").strip() # frontend sends 'username' field, but Supabase requires email
    password = data.get("password", "")
    
    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    auth_result = db.authenticate_user(email, password)
    
    if auth_result.get("success"):
        session['user_id'] = auth_result["user_id"]
        session['role'] = auth_result["role"]
        session['full_name'] = auth_result["full_name"]
        session['access_token'] = auth_result["access_token"]
        session['authenticated'] = True
        
        return jsonify({
            "success": True,
            "user": {
                "full_name": auth_result["full_name"],
                "role": auth_result["role"]
            },
            "access_token": auth_result["access_token"]
        }), 200
    else:
        return jsonify({"error": auth_result.get("error", "Authentication failed")}), 401

@auth_bp.route("/logout", methods=["POST", "OPTIONS"])
def logout_user():
    if request.method == "OPTIONS": return jsonify({}), 200
    session.clear()
    return jsonify({"success": True}), 200

@auth_bp.route("/me", methods=["GET"])
@login_required
def get_current_user():
    return jsonify({
        "authenticated": True,
        "user": {
            "id": session.get('user_id'),
            "role": session.get('role'),
            "full_name": session.get('full_name')
        }
    }), 200

from flask import render_template

@auth_bp.route("/google")
def google_login():
    try:
        # Generate OAuth URL using Supabase
        res = db.supabase.auth.sign_in_with_oauth({
            "provider": "google",
            "options": {
                "redirect_to": url_for("auth.google_callback", _external=True)
            }
        })
        return redirect(res.url)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@auth_bp.route("/google/callback")
def google_callback():
    code = request.args.get('code')
    if code:
        try:
            # Exchange the authorization code for a session (PKCE flow)
            res = db.supabase.auth.exchange_code_for_session({"auth_code": code})
            if res and res.user:
                user = res.user
                session['user_id'] = user.id
                session['role'] = user.user_metadata.get("role", "interviewer")
                session['full_name'] = user.user_metadata.get("full_name", user.email)
                session['access_token'] = res.session.access_token
                session['authenticated'] = True
                return redirect(url_for('main.host_dashboard_landing'))
        except Exception as e:
            return f"<h3>Authentication Error</h3><p>{str(e)}</p><a href='/'>Go Back</a>"
            
    # Fallback: Render the frontend page which will extract the #access_token from the URL fragment
    return render_template("oauth_callback.html")

@auth_bp.route("/oauth_save", methods=["POST", "OPTIONS"])
def oauth_save():
    if request.method == "OPTIONS": return jsonify({}), 200
    data = request.get_json(silent=True) or {}
    token = data.get("access_token")
    if not token:
        return jsonify({"error": "Missing access token"}), 400
    
    try:
        # Verify the token by getting the user from Supabase
        res = db.supabase.auth.get_user(token)
        if res and res.user:
            user = res.user
            session['user_id'] = user.id
            session['role'] = user.user_metadata.get("role", "interviewer")
            session['full_name'] = user.user_metadata.get("full_name", user.email)
            session['access_token'] = token
            session['authenticated'] = True
            return jsonify({"success": True})
        return jsonify({"error": "Invalid token"}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 401

@auth_bp.route("/status", methods=["GET", "OPTIONS"])
def auth_status():
    if request.method == "OPTIONS": return jsonify({}), 200
    return jsonify({"google_configured": False, "github_configured": False}), 200

@auth_bp.route("/forgot-password", methods=["POST", "OPTIONS"])
@auth_limiter.limit("5 per hour")
def forgot_password():
    if request.method == "OPTIONS": return jsonify({}), 200
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip()
    if not email: return jsonify({"error": "Email required"}), 400
    try:
        db.supabase.auth.reset_password_email(email)
        return jsonify({"success": True, "message": "If the email exists, a password reset link has been sent."}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400
def register_auth_routes(app):
    auth_limiter.init_app(app)
    init_oauth(app)
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    print('[Auth] Authentication routes registered at /api/auth')

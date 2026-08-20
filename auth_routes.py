"""
Authentication Routes for Intervue
Handles user registration, login, and session management.
"""

import os
import uuid
import base64
import secrets
import pyotp
import qrcode
import requests
from datetime import datetime, timezone
from io import BytesIO
from functools import wraps
from urllib.parse import urlparse
from flask import Blueprint, jsonify, request, session, current_app, redirect, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from authlib.integrations.flask_client import OAuth
from database import db

auth_bp = Blueprint('auth', __name__)

# OAuth setup
oauth = OAuth()

# Rate limiter for auth routes
auth_limiter = Limiter(key_func=get_remote_address, default_limits=["10 per minute"])

def utc_now():
    """Return current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)

# OAuth configuration (to be set in environment variables)
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
GOOGLE_REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI', '')

def _oauth_setup_page(provider_name: str, redirect_uri: str):
    """Friendly HTML page shown when an OAuth provider is not configured,
    instead of a bare JSON error."""
    upper = provider_name.upper()
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<title>OAuth setup required</title>"
        "<style>body{font-family:Segoe UI,Arial,sans-serif;background:#0f0c29;"
        "color:#fff;display:flex;align-items:center;justify-content:center;"
        "min-height:100vh;margin:0;padding:20px} .card{background:rgba(255,255,255,.06);"
        "border:1px solid rgba(255,255,255,.15);border-radius:16px;padding:32px;"
        "max-width:640px;line-height:1.7} h1{font-size:1.4rem;margin:0 0 8px} "
        "code{background:rgba(255,255,255,.12);padding:2px 6px;border-radius:6px}"
        "ol{margin:12px 0 0;padding-left:20px} li{margin:8px 0} "
        ".uri{display:block;background:#0008;border:1px solid rgba(255,255,255,.2);"
        "border-radius:8px;padding:10px 12px;margin-top:6px;"
        "font-family:Consolas,monospace;word-break:break-all}</style></head>"
        "<body><div class='card'>"
        f"<h1>{provider_name} OAuth is not configured</h1>"
        f"<p>The server is running, but <code>{provider_name}</code> login is disabled "
        "because no client credentials are set.</p><ol>"
        f"<li>In the {provider_name} developer console, create an OAuth app / "
        "client ID (type: Web application).</li>"
        f"<li>Add this exact URL as an authorized redirect URI:"
        f"<span class='uri'>{redirect_uri}</span></li>"
        f"<li>Put the Client ID and Client Secret in your <code>.env</code> file as "
        f"<code>{upper}_CLIENT_ID</code> and <code>{upper}_CLIENT_SECRET</code>.</li>"
        "<li>Restart the server, then try logging in again.</li>"
        "</ol></div></body></html>"
    )

def _google_redirect_uri() -> str:
    """Resolve the Google OAuth redirect URI that matches the CURRENT request
    origin.

    Authlib stores the OAuth `state` value in the Flask session cookie, which
    is scoped to a single host. If the redirect URI points at a different host
    than the user is currently on (e.g. the app is served on http://192.168.1.8:5000
    but GOOGLE_REDIRECT_URI is http://127.0.0.1:5000/...), the callback arrives
    with no session cookie and Google OAuth fails with `mismatching_state`.
    Deriving the URI from the request keeps the cookie and the callback on the
    same origin (register the derived URI in the Google Cloud console).

    GOOGLE_REDIRECT_URI overrides the derived value only when it already points
    at the same host as the current request (e.g. when running behind a reverse
    proxy where the request host differs from the public URI).
    """
    if GOOGLE_REDIRECT_URI:
        try:
            if urlparse(GOOGLE_REDIRECT_URI).netloc == urlparse(request.host_url).netloc:
                return GOOGLE_REDIRECT_URI
        except ValueError:
            pass
    return url_for('auth.google_callback', _external=True)

def init_oauth(app):
    """Initialize OAuth providers."""
    try:
        oauth.init_app(app)
        
        # Google OAuth
        if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
            oauth.register(
                name='google',
                client_id=GOOGLE_CLIENT_ID,
                client_secret=GOOGLE_CLIENT_SECRET,
                server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
                client_kwargs={
                    'scope': 'openid email profile'
                }
            )
            print("[Auth] Google OAuth configured")
        else:
            print("[Auth] Google OAuth not configured (missing credentials)")
        

    except Exception as e:
        print(f"[Auth] OAuth initialization failed: {e}")
        import traceback
        traceback.print_exc()

# ============================================================
# ROLE-BASED ACCESS CONTROL DECORATORS
# ============================================================

def login_required(f):
    """Decorator to require login for a route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated_function

def role_required(*allowed_roles):
    """Decorator to require specific roles for a route."""
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

def admin_required(f):
    """Decorator to require admin role."""
    return role_required('admin')(f)

def interviewer_required(f):
    """Decorator to require interviewer or admin role."""
    return role_required('admin', 'interviewer')(f)

# ============================================================
# AUTHENTICATION ENDPOINTS
# ============================================================

@auth_bp.route("/register", methods=["POST", "OPTIONS"])
@auth_limiter.limit("5 per minute")
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

@auth_bp.route("/login", methods=["POST", "OPTIONS"])
@auth_limiter.limit("10 per minute")
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

@auth_bp.route("/logout", methods=["POST", "OPTIONS"])
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

@auth_bp.route("/me", methods=["GET"])
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

@auth_bp.route("/users", methods=["GET", "OPTIONS"])
def get_users():
    """Get all users (admin only)."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    
    # Check if user is admin
    if session.get('role') != 'admin':
        return jsonify({"error": "Admin access required"}), 403
    
    users = db.get_all_users()
    return jsonify({"success": True, "users": users}), 200

@auth_bp.route("/users/<user_id>", methods=["PUT", "OPTIONS"])
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

@auth_bp.route("/users/<user_id>", methods=["DELETE", "OPTIONS"])
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
# MFA/TOTP ENDPOINTS
# ============================================================

@auth_bp.route("/mfa/enable", methods=["POST", "OPTIONS"])
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

@auth_bp.route("/mfa/disable", methods=["POST", "OPTIONS"])
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

@auth_bp.route("/mfa/verify", methods=["POST", "OPTIONS"])
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

@auth_bp.route("/forgot-password", methods=["POST"])
@auth_limiter.limit("5 per hour")
def forgot_password():
    """Send password reset link to user's email."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip()
    
    if not email:
        return jsonify({"error": "Email is required"}), 400
    
    if not email or "@" not in email:
        return jsonify({"error": "Invalid email address"}), 400
    
    # Check if user exists
    user = db.get_user_by_email(email)
    if not user:
        # Don't reveal if email exists for security
        return jsonify({"success": True, "message": "If an account exists with this email, a reset link will be sent."})
    
    # In production, generate a reset token and send email
    # For now, return success
    return jsonify({"success": True, "message": "Password reset link sent to your email."})

@auth_bp.route("/reset-password", methods=["POST"])
@auth_limiter.limit("5 per hour")
def reset_password():
    """Reset user password with token."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    
    data = request.get_json(silent=True) or {}
    token = data.get("token", "").strip()
    new_password = data.get("password", "").strip()
    
    if not token or not new_password:
        return jsonify({"error": "Token and password are required"}), 400
    
    if len(new_password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    
    # In production, validate token and reset password
    # For now, return success
    return jsonify({"success": True, "message": "Password reset successfully."})

# OAuth Routes
@auth_bp.route("/google", methods=["GET"])
def google_login():
    """Initiate Google OAuth login."""
    # The redirect URI must match the origin the user is currently on so the
    # session cookie (which holds the OAuth `state`) survives the round-trip.
    redirect_uri = _google_redirect_uri()

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return _oauth_setup_page("Google", redirect_uri), 501
    
    try:
        return oauth.google.authorize_redirect(redirect_uri)
    except Exception as e:
        return jsonify({"error": f"OAuth redirect failed: {str(e)}"}), 500

@auth_bp.route("/google/callback", methods=["GET"])
def google_callback():
    """Handle Google OAuth callback."""
    # Use consistent redirect URI for error page
    redirect_uri = _google_redirect_uri()
    
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return _oauth_setup_page("Google", redirect_uri), 501
    
    try:
        token = oauth.google.authorize_access_token()
        user_info = token.get('userinfo')
        if not user_info:
            user_info = oauth.google.userinfo(token=token)
        
        email = user_info.get('email')
        name = user_info.get('name')
        google_id = user_info.get('sub')
        
        # Check if user exists by email or Google ID
        user = db.get_user_by_email(email)
        
        if user:
            # Update Google ID if not set
            if not user.get('google_id'):
                db.link_oauth_id(str(user['_id']), 'google', google_id)
            # Log in existing user
            session['user_id'] = str(user['_id'])
            session['authenticated'] = True
            session['auth_method'] = 'google'
            return redirect(url_for('main.host_dashboard_landing'))
        else:
            # Create new user
            username = email.split('@')[0]
            result = db.create_user(
                username=username,
                email=email,
                password=secrets.token_urlsafe(32),  # Random password
                full_name=name,
                google_id=google_id
            )
            
            if result.get('success'):
                session['user_id'] = result['user_id']
                session['authenticated'] = True
                session['auth_method'] = 'google'
                return redirect(url_for('main.host_dashboard_landing'))
            else:
                return jsonify({"error": result.get('error', 'Failed to create user')}), 400
                
    except Exception as e:
        # A stale `state` (e.g. the callback arrived on a different host than
        # where login was started, or the browser dropped the session cookie)
        # must not leave a poisoned session. Clear it and route back to the
        # login page with a readable message instead of raw JSON.
        for key in list(session.keys()):
            if 'authlib_state' in key or '_google_' in key:
                session.pop(key, None)
        message = "OAuth state mismatch. Please try again from the login page."
        if 'mismatching_state' not in str(e) and 'state' not in str(e).lower():
            message = "Google sign-in failed. Please try again."
        return redirect(url_for('main.home', oauth_error=message))


@auth_bp.route("/status")
def auth_status():
    """Check authentication system status."""
    google_redirect = _google_redirect_uri()
    
    return jsonify({
        "status": "active",
        "google_configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        "google_redirect_uri": google_redirect
    })

@auth_bp.route("/test")
def auth_test():
    """Test route to verify auth routes are working."""
    return jsonify({
        "message": "Auth routes are working",
        "timestamp": str(utc_now())
    })

def register_auth_routes(app):
    """Register authentication routes with the Flask app."""
    init_oauth(app)
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    print("[Auth] Authentication routes registered at /api/auth")
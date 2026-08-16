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
from io import BytesIO
from functools import wraps
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

# OAuth configuration (to be set in environment variables)
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
GITHUB_CLIENT_ID = os.getenv('GITHUB_CLIENT_ID', '')
GITHUB_CLIENT_SECRET = os.getenv('GITHUB_CLIENT_SECRET', '')

def init_oauth(app):
    """Initialize OAuth providers."""
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
    
    # GitHub OAuth
    if GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET:
        oauth.register(
            name='github',
            client_id=GITHUB_CLIENT_ID,
            client_secret=GITHUB_CLIENT_SECRET,
            access_token_url='https://github.com/login/oauth/access_token',
            authorize_url='https://github.com/login/oauth/authorize',
            api_base_url='https://api.github.com/',
            client_kwargs={'scope': 'user:email'}
        )

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
@auth_bp.route("/google")
def google_login():
    """Initiate Google OAuth login."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return jsonify({"error": "Google OAuth not configured. Please set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables."}), 500
    
    redirect_uri = url_for('auth.google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)

@auth_bp.route("/google/callback")
def google_callback():
    """Handle Google OAuth callback."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return jsonify({"error": "Google OAuth not configured"}), 500
    
    try:
        token = oauth.google.authorize_access_token()
        user_info = oauth.google.parse_id_token(token)
        
        email = user_info.get('email')
        name = user_info.get('name')
        google_id = user_info.get('sub')
        
        # Check if user exists by email or Google ID
        user = db.get_user_by_email(email)
        
        if user:
            # Update Google ID if not set
            if not user.get('google_id'):
                db.db.users.update_one(
                    {"_id": user["_id"]},
                    {"$set": {"google_id": google_id}}
                )
            # Log in existing user
            session['user_id'] = str(user['_id'])
            session['authenticated'] = True
            session['auth_method'] = 'google'
            return redirect('/host_dashboard')
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
                return redirect('/host_dashboard')
            else:
                return jsonify({"error": result.get('error', 'Failed to create user')}), 400
                
    except Exception as e:
        return jsonify({"error": f"Google OAuth failed: {str(e)}"}), 500

@auth_bp.route("/github")
def github_login():
    """Initiate GitHub OAuth login."""
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        return jsonify({"error": "GitHub OAuth not configured. Please set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET environment variables."}), 500
    
    redirect_uri = url_for('auth.github_callback', _external=True)
    return oauth.github.authorize_redirect(redirect_uri)

@auth_bp.route("/github/callback")
def github_callback():
    """Handle GitHub OAuth callback."""
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        return jsonify({"error": "GitHub OAuth not configured"}), 500
    
    try:
        token = oauth.github.authorize_access_token()
        resp = oauth.github.get('user', token=token)
        user_info = resp.json()
        
        email = user_info.get('email')
        name = user_info.get('name') or user_info.get('login')
        github_id = str(user_info.get('id'))
        
        # Get primary email if not provided
        if not email:
            resp = oauth.github.get('user/emails', token=token)
            emails = resp.json()
            primary_email = next((e['email'] for e in emails if e['primary'] and e['verified']), None)
            email = primary_email
        
        if not email:
            return jsonify({"error": "GitHub email not verified"}), 400
        
        # Check if user exists by email or GitHub ID
        user = db.get_user_by_email(email)
        
        if user:
            # Update GitHub ID if not set
            if not user.get('github_id'):
                db.db.users.update_one(
                    {"_id": user["_id"]},
                    {"$set": {"github_id": github_id}}
                )
            # Log in existing user
            session['user_id'] = str(user['_id'])
            session['authenticated'] = True
            session['auth_method'] = 'github'
            return redirect('/host_dashboard')
        else:
            # Create new user
            username = user_info.get('login', email.split('@')[0])
            result = db.create_user(
                username=username,
                email=email,
                password=secrets.token_urlsafe(32),  # Random password
                full_name=name,
                github_id=github_id
            )
            
            if result.get('success'):
                session['user_id'] = result['user_id']
                session['authenticated'] = True
                session['auth_method'] = 'github'
                return redirect('/host_dashboard')
            else:
                return jsonify({"error": result.get('error', 'Failed to create user')}), 400
                
    except Exception as e:
        return jsonify({"error": f"GitHub OAuth failed: {str(e)}"}), 500

def register_auth_routes(app):
    """Register authentication routes with the Flask app."""
    init_oauth(app)
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
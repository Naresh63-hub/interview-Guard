"""
MongoDB Atlas Integration for Intervue
Handles database operations for the Intervue proctoring system.
"""

import os
import hashlib
import hmac
import secrets
import pyotp
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from functools import wraps

from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import (
    ConnectionFailure,
    ServerSelectionTimeoutError,
    DuplicateKeyError,
)
from werkzeug.security import generate_password_hash, check_password_hash

# Load .env
load_dotenv()


def utc_now() -> datetime:
    """Return current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


# Password hashing: PBKDF2 (via werkzeug). SHA-256+salt was too fast for
# credential storage; PBKDF2 with the default 600k iterations is the OWASP-
# recommended minimum for server-side hashing and needs no extra dependency.
_HASH_METHOD = "pbkdf2:sha256"


def hash_password(password: str) -> str:
    """Hash a password using PBKDF2 (werkzeug)."""
    return generate_password_hash(password, method=_HASH_METHOD)


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a stored hash.

    Supports both the current PBKDF2 format and legacy `salt$sha256hex` hashes
    created before the hashing upgrade, so existing accounts keep working and
    are upgraded lazily on the next successful login by the caller.
    """
    if not hashed:
        return False
    if not hashed.startswith(("pbkdf2:", "scrypt:", "bcrypt:")):
        # Legacy format: hex-salted SHA-256 ("salt$digest")
        try:
            salt, hash_value = hashed.split("$", 1)
            return hmac.compare_digest(
                hash_value,
                hashlib.sha256(f"{salt}{password}".encode()).hexdigest(),
            )
        except Exception:
            return False
    return check_password_hash(hashed, password)


def password_needs_rehash(hashed: str) -> bool:
    """True for legacy salted-SHA-256 hashes that should be re-hashed on login."""
    return bool(hashed) and not hashed.startswith(("pbkdf2:", "scrypt:", "bcrypt:"))


class MongoDBDatabase:
    """MongoDB Atlas database handler for Intervue."""

    def __init__(self):
        self.client: Optional[MongoClient] = None
        self.db = None
        self.connected = False

    # ============================================================
    # CONNECTION
    # ============================================================

    def connect(self) -> bool:
        """Connect to MongoDB Atlas using environment variables."""

        try:
            mongo_uri = os.getenv("MONGODB_URI")

            if not mongo_uri:
                print("[MongoDB] MONGODB_URI is not configured.")
                return False

            db_name = os.getenv(
                "MONGODB_DB_NAME",
                "intervue_proctoring"
            )

            self.client = MongoClient(
                mongo_uri,
                serverSelectionTimeoutMS=15000,
                connectTimeoutMS=15000,
                socketTimeoutMS=30000,
                retryWrites=True,
                w="majority",
            )

            # Test connection
            self.client.admin.command("ping")

            # Select database
            self.db = self.client[db_name]

            # Create indexes
            self._create_indexes()

            self.connected = True

            print(
                f"[MongoDB] Connected successfully to Atlas "
                f"database: {db_name}"
            )

            return True

        except ServerSelectionTimeoutError as e:
            print(f"[MongoDB] Server selection timeout: {e}")
            self.connected = False
            return False

        except ConnectionFailure as e:
            print(f"[MongoDB] Connection failure: {e}")
            self.connected = False
            return False

        except Exception as e:
            print(f"[MongoDB] Connection error: {e}")
            self.connected = False
            return False

    def close(self):
        """Close MongoDB connection."""

        if self.client:
            self.client.close()

        self.client = None
        self.db = None
        self.connected = False

        print("[MongoDB] Connection closed.")

    def is_connected(self) -> bool:
        """Return connection status."""

        return self.connected and self.db is not None

    # ============================================================
    # INDEXES
    # ============================================================

    def _create_indexes(self):
        """Create database indexes."""

        if self.db is None:
            return

        try:
            # Meeting rooms
            self.db.meeting_rooms.create_index(
                [("meeting_id", ASCENDING)],
                unique=True
            )

            self.db.meeting_rooms.create_index(
                [("host", ASCENDING)]
            )

            self.db.meeting_rooms.create_index(
                [("created_at", DESCENDING)]
            )

            # Audit logs
            self.db.audit_logs.create_index(
                [("meeting_id", ASCENDING)]
            )

            self.db.audit_logs.create_index(
                [("timestamp", DESCENDING)]
            )

            self.db.audit_logs.create_index(
                [("is_critical", ASCENDING)]
            )
            
            # Users (NEW - for authentication)
            self.db.users.create_index(
                [("username", ASCENDING)],
                unique=True
            )
            
            self.db.users.create_index(
                [("email", ASCENDING)],
                unique=True
            )
            
            self.db.users.create_index(
                [("role", ASCENDING)]
            )
            
            # Sessions (NEW - for session management)
            # NOTE: sparse unique index — the same collection also stores
            # meeting sessions (create_meeting_session) which have no
            # session_token; a plain unique index would fail to build because
            # every legacy/meeting doc with a missing field is indexed as null.
            self.db.sessions.create_index(
                [("session_token", ASCENDING)],
                unique=True,
                sparse=True,
            )
            
            self.db.sessions.create_index(
                [("user_id", ASCENDING)]
            )
            
            self.db.sessions.create_index(
                [("expires_at", ASCENDING)]
            )

            print("[MongoDB] Indexes created successfully.")

        except Exception as e:
            print(f"[MongoDB] Index creation error: {e}")

    # ============================================================
    # USER MANAGEMENT (NEW)
    # ============================================================
    
    def create_user(self, username: str, email: str, password: str, 
                   full_name: str = "", role: str = "interviewer",
                   google_id: str = None, github_id: str = None) -> Dict[str, Any]:
        """Create a new user account."""
        if not self.is_connected():
            return {"success": False, "error": "Database not connected"}
        
        try:
            # Check if username or email already exists
            existing = self.db.users.find_one({
                "$or": [
                    {"username": username.lower()},
                    {"email": email.lower()}
                ]
            })
            
            if existing:
                return {"success": False, "error": "Username or email already exists"}
            
            # Validate role
            valid_roles = ["admin", "interviewer", "moderator"]
            if role not in valid_roles:
                role = "interviewer"
            
            # Create user document
            user_doc = {
                "username": username.lower(),
                "email": email.lower(),
                "password_hash": hash_password(password),
                "full_name": full_name,
                "role": role,
                "is_active": True,
                "created_at": utc_now(),
                "last_login": None,
                "mfa_enabled": False,
                "mfa_secret": None
            }
            
            # Add OAuth IDs if provided
            if google_id:
                user_doc["google_id"] = google_id
            if github_id:
                user_doc["github_id"] = github_id
            
            result = self.db.users.insert_one(user_doc)
            
            if result.inserted_id:
                return {
                    "success": True,
                    "user_id": str(result.inserted_id),
                    "username": username,
                    "email": email,
                    "role": role
                }
            else:
                return {"success": False, "error": "Failed to create user"}
                
        except DuplicateKeyError:
            return {"success": False, "error": "Username or email already exists"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def authenticate_user(self, username: str, password: str) -> Dict[str, Any]:
        """Authenticate a user with username and password."""
        if not self.is_connected():
            return {"success": False, "error": "Database not connected"}
        
        try:
            user = self.db.users.find_one({
                "$or": [
                    {"username": username.lower()},
                    {"email": username.lower()}
                ],
                "is_active": True
            })
            
            if not user:
                return {"success": False, "error": "Invalid credentials"}
            
            if not verify_password(password, user["password_hash"]):
                return {"success": False, "error": "Invalid credentials"}

            # Lazy upgrade: re-hash legacy salted-SHA-256 accounts on login.
            if password_needs_rehash(user["password_hash"]):
                self.db.users.update_one(
                    {"_id": user["_id"]},
                    {"$set": {"password_hash": hash_password(password)}},
                )

            # Update last login
            self.db.users.update_one(
                {"_id": user["_id"]},
                {"$set": {"last_login": utc_now()}}
            )
            
            return {
                "success": True,
                "user_id": str(user["_id"]),
                "username": user["username"],
                "email": user["email"],
                "full_name": user.get("full_name", ""),
                "role": user["role"],
                "mfa_enabled": user.get("mfa_enabled", False)
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by ID."""
        if not self.is_connected():
            return None
        
        try:
            from bson.objectid import ObjectId
            user = self.db.users.find_one({"_id": ObjectId(user_id)})
            if user:
                user["_id"] = str(user["_id"])
            return user
        except:
            return None
    
    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get user by email."""
        if not self.is_connected():
            return None
        
        try:
            user = self.db.users.find_one({"email": email.lower()})
            if user:
                user["_id"] = str(user["_id"])
            return user
        except:
            return None
    
    def get_all_users(self) -> List[Dict[str, Any]]:
        """Get all users (admin only)."""
        if not self.is_connected():
            return []
        
        try:
            users = list(self.db.users.find({}))
            for user in users:
                user["_id"] = str(user["_id"])
                # Remove sensitive data
                user.pop("password_hash", None)
                user.pop("mfa_secret", None)
            return users
        except Exception as e:
            print(f"[MongoDB] Error getting users: {e}")
            return []
    
    def update_user_role(self, user_id: str, new_role: str) -> Dict[str, Any]:
        """Update user role (admin only)."""
        if not self.is_connected():
            return {"success": False, "error": "Database not connected"}
        
        try:
            from bson.objectid import ObjectId
            valid_roles = ["admin", "interviewer", "moderator"]
            if new_role not in valid_roles:
                return {"success": False, "error": "Invalid role"}
            
            result = self.db.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {"role": new_role}}
            )
            
            if result.modified_count > 0:
                return {"success": True}
            else:
                return {"success": False, "error": "User not found"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def delete_user(self, user_id: str) -> Dict[str, Any]:
        """Delete a user (admin only)."""
        if not self.is_connected():
            return {"success": False, "error": "Database not connected"}
        
        try:
            from bson.objectid import ObjectId
            result = self.db.users.delete_one({"_id": ObjectId(user_id)})
            
            if result.deleted_count > 0:
                return {"success": True}
            else:
                return {"success": False, "error": "User not found"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def link_oauth_id(self, user_id: str, provider: str, oauth_id: str) -> bool:
        """Attach a Google/GitHub OAuth ID to an existing user.

        Used by the OAuth callbacks instead of touching the users collection
        directly (which would break when MongoDB is unreachable).
        """
        if not self.is_connected():
            return False
        try:
            from bson.objectid import ObjectId
            result = self.db.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {f"{provider}_id": oauth_id}}
            )
            return result.modified_count > 0
        except Exception as e:
            print(f"[MongoDB] OAuth link error: {e}")
            return False
    
    # ============================================================
    # SESSION MANAGEMENT (NEW)
    # ============================================================
    
    def create_session(self, user_id: str, remember_me: bool = False) -> Dict[str, Any]:
        """Create a new session for a user."""
        if not self.is_connected():
            return {"success": False, "error": "Database not connected"}
        
        try:
            from bson.objectid import ObjectId
            
            # Generate session token
            session_token = secrets.token_urlsafe(32)
            
            # Set expiration (7 days if remember me, 1 day otherwise)
            expires_at = utc_now() + timedelta(days=7 if remember_me else 1)
            
            session_doc = {
                "session_token": session_token,
                "user_id": ObjectId(user_id),
                "created_at": utc_now(),
                "expires_at": expires_at,
                "ip_address": None,  # Will be set by caller
                "user_agent": None  # Will be set by caller
            }
            
            result = self.db.sessions.insert_one(session_doc)
            
            if result.inserted_id:
                return {
                    "success": True,
                    "session_token": session_token,
                    "expires_at": expires_at.isoformat()
                }
            else:
                return {"success": False, "error": "Failed to create session"}
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def validate_session(self, session_token: str) -> Optional[Dict[str, Any]]:
        """Validate a session token and return user info."""
        if not self.is_connected():
            return None
        
        try:
            session = self.db.sessions.find_one({
                "session_token": session_token,
                "expires_at": {"$gt": utc_now()}
            })
            
            if not session:
                return None
            
            # Get user info
            user = self.get_user_by_id(str(session["user_id"]))
            
            if user and user.get("is_active"):
                return {
                    "user_id": str(user["_id"]),
                    "username": user["username"],
                    "email": user["email"],
                    "full_name": user.get("full_name", ""),
                    "role": user["role"]
                }
            
            return None
            
        except Exception as e:
            print(f"[MongoDB] Session validation error: {e}")
            return None
    
    def delete_session(self, session_token: str) -> bool:
        """Delete a session (logout)."""
        if not self.is_connected():
            return False
        
        try:
            result = self.db.sessions.delete_one({"session_token": session_token})
            return result.deleted_count > 0
        except Exception as e:
            print(f"[MongoDB] Session deletion error: {e}")
            return False
    
    def cleanup_expired_sessions(self) -> int:
        """Clean up expired sessions."""
        if not self.is_connected():
            return 0
        
        try:
            result = self.db.sessions.delete_many({
                "expires_at": {"$lt": utc_now()}
            })
            return result.deleted_count
        except Exception as e:
            print(f"[MongoDB] Session cleanup error: {e}")
            return 0
    
    # ============================================================
    # MFA/TOTP MANAGEMENT (NEW)
    # ============================================================
    
    def enable_mfa(self, user_id: str) -> Dict[str, Any]:
        """Enable MFA for a user and generate TOTP secret."""
        if not self.is_connected():
            return {"success": False, "error": "Database not connected"}
        
        try:
            from bson.objectid import ObjectId
            
            # Generate TOTP secret
            secret = pyotp.random_base32()
            
            # Update user with MFA settings
            result = self.db.users.update_one(
                {"_id": ObjectId(user_id)},
                {
                    "$set": {
                        "mfa_enabled": True,
                        "mfa_secret": secret,
                        "mfa_enabled_at": utc_now()
                    }
                }
            )
            
            if result.modified_count > 0:
                return {
                    "success": True,
                    "secret": secret,
                    "qr_code_url": f"otpauth://totp/Intervue:{secret}?secret={secret}&issuer=Intervue"
                }
            else:
                return {"success": False, "error": "User not found"}
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def disable_mfa(self, user_id: str) -> Dict[str, Any]:
        """Disable MFA for a user."""
        if not self.is_connected():
            return {"success": False, "error": "Database not connected"}
        
        try:
            from bson.objectid import ObjectId
            
            result = self.db.users.update_one(
                {"_id": ObjectId(user_id)},
                {
                    "$set": {
                        "mfa_enabled": False,
                        "mfa_secret": None
                    }
                }
            )
            
            if result.modified_count > 0:
                return {"success": True}
            else:
                return {"success": False, "error": "User not found"}
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def verify_totp(self, user_id: str, totp_code: str) -> bool:
        """Verify TOTP code for a user."""
        if not self.is_connected():
            return False
        
        try:
            from bson.objectid import ObjectId
            
            user = self.db.users.find_one({"_id": ObjectId(user_id)})
            
            if not user or not user.get("mfa_enabled") or not user.get("mfa_secret"):
                return False
            
            totp = pyotp.TOTP(user["mfa_secret"])
            return totp.verify(totp_code, valid_window=1)
            
        except Exception as e:
            print(f"[MongoDB] TOTP verification error: {e}")
            return False

    # ============================================================
    # MEETING ROOM OPERATIONS
    # ============================================================

    def create_meeting_room(
        self,
        meeting_id: str,
        host: str,
        title: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Create a new meeting room."""

        if not self.is_connected():
            return False

        try:
            now = utc_now()

            room_data = {
                "meeting_id": meeting_id.upper(),
                "host": host,
                "title": title,
                "created_at": now,
                "updated_at": now,
                "status": "waiting",
                "participants": [],
                "proctoring_settings": {
                    "gazeSensitivity": "medium",
                    "allowedTabSwitches": 3,
                    "gazeCheck": True,
                    "audioCheck": True,
                    "vmCheck": True,
                    "dualMonitorCheck": True,
                    "devToolsCheck": True,
                    "clipboardCheck": True,
                },
                "metadata": metadata or {},
            }

            self.db.meeting_rooms.insert_one(room_data)

            print(
                f"[MongoDB] Created meeting room: "
                f"{meeting_id.upper()}"
            )

            return True

        except DuplicateKeyError:
            print(
                f"[MongoDB] Meeting already exists: "
                f"{meeting_id.upper()}"
            )
            return False

        except Exception as e:
            print(f"[MongoDB] Error creating meeting room: {e}")
            return False

    def get_meeting_room(
        self,
        meeting_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get meeting room by ID."""

        if not self.is_connected():
            return None

        try:
            return self.db.meeting_rooms.find_one(
                {"meeting_id": meeting_id.upper()}
            )

        except Exception as e:
            print(f"[MongoDB] Error getting meeting room: {e}")
            return None

    def get_latest_meeting_for_user(
        self,
        user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get the host's most recently created meeting room."""

        if not self.is_connected():
            return None

        try:
            return self.db.meeting_rooms.find_one(
                {"metadata.user_id": user_id},
                sort=[("created_at", -1)],
            )

        except Exception as e:
            print(f"[MongoDB] Error getting latest meeting for user: {e}")
            return None

    def update_meeting_room(
        self,
        meeting_id: str,
        updates: Dict[str, Any]
    ) -> bool:
        """Update meeting room data."""

        if not self.is_connected():
            return False

        try:
            updates = dict(updates)
            updates["updated_at"] = utc_now()

            result = self.db.meeting_rooms.update_one(
                {"meeting_id": meeting_id.upper()},
                {"$set": updates}
            )

            return result.matched_count > 0

        except Exception as e:
            print(f"[MongoDB] Error updating meeting room: {e}")
            return False

    def delete_meeting_room(
        self,
        meeting_id: str
    ) -> bool:
        """Delete a meeting room."""

        if not self.is_connected():
            return False

        try:
            result = self.db.meeting_rooms.delete_one(
                {"meeting_id": meeting_id.upper()}
            )

            return result.deleted_count > 0

        except Exception as e:
            print(f"[MongoDB] Error deleting meeting room: {e}")
            return False

    def get_active_meeting_rooms(
        self,
        hours: int = 24
    ) -> List[Dict[str, Any]]:
        """Get meeting rooms created within the last N hours."""

        if not self.is_connected():
            return []

        try:
            cutoff = utc_now() - timedelta(hours=hours)

            return list(
                self.db.meeting_rooms.find(
                    {
                        "created_at": {
                            "$gte": cutoff
                        }
                    }
                ).sort(
                    "created_at",
                    DESCENDING
                )
            )

        except Exception as e:
            print(f"[MongoDB] Error getting active rooms: {e}")
            return []

    # ============================================================
    # PARTICIPANT OPERATIONS
    # ============================================================

    def add_participant(
        self,
        meeting_id: str,
        user_id: str,
        user_name: str,
        role: str,
        socket_id: Optional[str] = None,
    ) -> bool:
        """Add participant to a meeting."""

        if not self.is_connected():
            return False

        try:
            now = utc_now()

            participant_data = {
                "user_id": user_id,
                "user_name": user_name,
                "role": role,
                "socket_id": socket_id,
                "joined_at": now,
                "last_seen": now,
                "is_active": True,
            }

            result = self.db.meeting_rooms.update_one(
                {
                    "meeting_id": meeting_id.upper(),
                    "participants.user_id": {
                        "$ne": user_id
                    },
                },
                {
                    "$push": {
                        "participants": participant_data
                    },
                    "$set": {
                        "updated_at": now
                    },
                },
            )

            # Store participant separately
            self.db.participants.update_one(
                {
                    "meeting_id": meeting_id.upper(),
                    "user_id": user_id,
                },
                {
                    "$set": {
                        **participant_data,
                        "meeting_id": meeting_id.upper(),
                    }
                },
                upsert=True,
            )

            return result.modified_count > 0

        except Exception as e:
            print(f"[MongoDB] Error adding participant: {e}")
            return False

    def update_participant_activity(
        self,
        meeting_id: str,
        user_id: str
    ) -> bool:
        """Update participant activity."""

        if not self.is_connected():
            return False

        try:
            now = utc_now()

            self.db.meeting_rooms.update_one(
                {
                    "meeting_id": meeting_id.upper(),
                    "participants.user_id": user_id,
                },
                {
                    "$set": {
                        "participants.$.last_seen": now
                    }
                },
            )

            self.db.participants.update_one(
                {
                    "meeting_id": meeting_id.upper(),
                    "user_id": user_id,
                },
                {
                    "$set": {
                        "last_seen": now,
                        "is_active": True,
                    }
                },
            )

            return True

        except Exception as e:
            print(
                f"[MongoDB] Error updating participant activity: {e}"
            )
            return False

    def remove_participant(
        self,
        meeting_id: str,
        user_id: str
    ) -> bool:
        """Remove participant from meeting."""

        if not self.is_connected():
            return False

        try:
            now = utc_now()

            self.db.meeting_rooms.update_one(
                {
                    "meeting_id": meeting_id.upper()
                },
                {
                    "$pull": {
                        "participants": {
                            "user_id": user_id
                        }
                    },
                    "$set": {
                        "updated_at": now
                    },
                },
            )

            self.db.participants.update_one(
                {
                    "meeting_id": meeting_id.upper(),
                    "user_id": user_id,
                },
                {
                    "$set": {
                        "is_active": False,
                        "left_at": now,
                    }
                },
            )

            return True

        except Exception as e:
            print(f"[MongoDB] Error removing participant: {e}")
            return False

    # ============================================================
    # AUDIT LOG OPERATIONS
    # ============================================================

    def add_audit_log(
        self,
        meeting_id: str,
        event_type: str,
        title: str,
        message: str,
        confidence: str = "",
        is_critical: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Add audit log entry."""

        if not self.is_connected():
            return False

        try:
            log_entry = {
                "meeting_id": meeting_id.upper(),
                "event_type": event_type,
                "title": title,
                "message": message,
                "confidence": confidence,
                "is_critical": is_critical,
                "metadata": metadata or {},
                "timestamp": utc_now(),
            }

            self.db.audit_logs.insert_one(log_entry)

            return True

        except Exception as e:
            print(f"[MongoDB] Error adding audit log: {e}")
            return False

    def get_audit_logs(
        self,
        meeting_id: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get audit logs for a meeting."""

        if not self.is_connected():
            return []

        try:
            return list(
                self.db.audit_logs.find(
                    {
                        "meeting_id": meeting_id.upper()
                    }
                )
                .sort("timestamp", DESCENDING)
                .limit(limit)
            )

        except Exception as e:
            print(f"[MongoDB] Error getting audit logs: {e}")
            return []

    def get_critical_alerts(
        self,
        meeting_id: str
    ) -> List[Dict[str, Any]]:
        """Get critical security alerts."""

        if not self.is_connected():
            return []

        try:
            return list(
                self.db.audit_logs.find(
                    {
                        "meeting_id": meeting_id.upper(),
                        "is_critical": True,
                    }
                ).sort(
                    "timestamp",
                    DESCENDING
                )
            )

        except Exception as e:
            print(
                f"[MongoDB] Error getting critical alerts: {e}"
            )
            return []

    # ============================================================
    # CALIBRATION OPERATIONS
    # ============================================================

    def save_calibration_data(
        self,
        user_id: str,
        calibration_samples: List[Dict[str, Any]]
    ) -> bool:
        """Save calibration data."""

        if not self.is_connected():
            return False

        try:
            calibration_entry = {
                "user_id": user_id,
                "samples": calibration_samples,
                "timestamp": utc_now(),
                "is_valid": True,
            }

            self.db.calibration_data.insert_one(
                calibration_entry
            )

            return True

        except Exception as e:
            print(
                f"[MongoDB] Error saving calibration data: {e}"
            )
            return False

    def get_calibration_data(
        self,
        user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get latest valid calibration data."""

        if not self.is_connected():
            return None

        try:
            return self.db.calibration_data.find_one(
                {
                    "user_id": user_id,
                    "is_valid": True,
                },
                sort=[
                    ("timestamp", DESCENDING)
                ],
            )

        except Exception as e:
            print(
                f"[MongoDB] Error getting calibration data: {e}"
            )
            return None

    # ============================================================
    # SESSION OPERATIONS
    # ============================================================

    def create_meeting_session(
        self,
        meeting_id: str,
        started_at: Optional[datetime] = None
    ) -> bool:
        """Create a new session."""

        if not self.is_connected():
            return False

        try:
            session_data = {
                "meeting_id": meeting_id.upper(),
                "started_at": started_at or utc_now(),
                "ended_at": None,
                "duration_seconds": 0,
                "status": "active",
                "metadata": {},
            }

            self.db.sessions.insert_one(session_data)

            return True

        except Exception as e:
            print(f"[MongoDB] Error creating session: {e}")
            return False

    def end_session(
        self,
        meeting_id: str
    ) -> bool:
        """End active session and calculate duration."""

        if not self.is_connected():
            return False

        try:
            session = self.db.sessions.find_one(
                {
                    "meeting_id": meeting_id.upper(),
                    "status": "active",
                },
                sort=[
                    ("started_at", DESCENDING)
                ],
            )

            if not session:
                return False

            ended_at = utc_now()
            started_at = session.get(
                "started_at",
                ended_at
            )
            # pymongo returns naive UTC datetimes by default; make it aware so
            # the duration subtraction cannot raise an offset mismatch.
            if started_at is not None and started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)

            duration = max(
                0,
                int(
                    (
                        ended_at - started_at
                    ).total_seconds()
                )
            )

            self.db.sessions.update_one(
                {
                    "_id": session["_id"]
                },
                {
                    "$set": {
                        "ended_at": ended_at,
                        "duration_seconds": duration,
                        "status": "completed",
                    }
                },
            )

            return True

        except Exception as e:
            print(f"[MongoDB] Error ending session: {e}")
            return False

    def get_session(
        self,
        meeting_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get latest session for meeting."""

        if not self.is_connected():
            return None

        try:
            return self.db.sessions.find_one(
                {
                    "meeting_id": meeting_id.upper()
                },
                sort=[
                    ("started_at", DESCENDING)
                ],
            )

        except Exception as e:
            print(f"[MongoDB] Error getting session: {e}")
            return None

    # ============================================================
    # ANALYTICS
    # ============================================================

    def get_meeting_analytics(
        self,
        meeting_id: str
    ) -> Dict[str, Any]:
        """Get comprehensive meeting analytics."""

        if not self.is_connected():
            return {}

        try:
            session = self.get_session(meeting_id)

            audit_logs = self.get_audit_logs(
                meeting_id,
                limit=1000
            )

            critical_alerts = self.get_critical_alerts(
                meeting_id
            )

            total_events = len(audit_logs)
            critical_count = len(critical_alerts)

            event_types: Dict[str, int] = {}

            for log in audit_logs:
                event_type = log.get(
                    "event_type",
                    "unknown"
                )

                event_types[event_type] = (
                    event_types.get(event_type, 0) + 1
                )

            duration = 0

            if session:
                duration = session.get(
                    "duration_seconds",
                    0
                )

            risk_score = min(
                100,
                (critical_count * 10)
                + (total_events * 2)
            )

            return {
                "meeting_id": meeting_id.upper(),
                "duration_seconds": duration,
                "total_events": total_events,
                "critical_alerts": critical_count,
                "event_breakdown": event_types,
                "risk_score": risk_score,
                "has_high_risk": critical_count > 3,
            }

        except Exception as e:
            print(
                f"[MongoDB] Error getting analytics: {e}"
            )
            return {}

    def get_partner_analytics(
        self,
        partner_id: str,
        days: int = 30
    ) -> Dict[str, Any]:
        """Get partner analytics for the last N days."""

        if not self.is_connected():
            return {}

        try:
            cutoff = utc_now() - timedelta(
                days=days
            )

            meetings = list(
                self.db.meeting_rooms.find(
                    {
                        "metadata.partner_id": partner_id,
                        "created_at": {
                            "$gte": cutoff
                        },
                    }
                )
            )

            total_meetings = len(meetings)

            meeting_ids = [
                meeting["meeting_id"]
                for meeting in meetings
            ]

            if not meeting_ids:
                return {
                    "partner_id": partner_id,
                    "period_days": days,
                    "total_meetings": 0,
                    "total_duration_seconds": 0,
                    "total_events": 0,
                    "critical_alerts": 0,
                    "avg_duration_seconds": 0,
                    "meetings_per_day": 0,
                }

            audit_logs = list(
                self.db.audit_logs.find(
                    {
                        "meeting_id": {
                            "$in": meeting_ids
                        }
                    }
                )
            )

            sessions = list(
                self.db.sessions.find(
                    {
                        "meeting_id": {
                            "$in": meeting_ids
                        }
                    }
                )
            )

            total_duration = sum(
                session.get(
                    "duration_seconds",
                    0
                )
                for session in sessions
            )

            total_events = len(audit_logs)

            critical_alerts = sum(
                1
                for log in audit_logs
                if log.get("is_critical", False)
            )

            return {
                "partner_id": partner_id,
                "period_days": days,
                "total_meetings": total_meetings,
                "total_duration_seconds": total_duration,
                "total_events": total_events,
                "critical_alerts": critical_alerts,
                "avg_duration_seconds": (
                    total_duration / total_meetings
                    if total_meetings > 0
                    else 0
                ),
                "meetings_per_day": (
                    total_meetings / days
                    if days > 0
                    else 0
                ),
            }

        except Exception as e:
            print(
                f"[MongoDB] Error getting partner analytics: {e}"
            )
            return {}


class InMemoryBackend:
    """In-memory fallback for the user/auth subset of the database.

    Activated automatically when MongoDB is unreachable so registration,
    login (email or OAuth) and sessions keep working offline. This mirrors
    the existing in-memory meeting-room fallback in state.py. Data is lost
    when the process restarts.
    """

    def __init__(self):
        self.users = {}
        self.sessions = {}
        self._next_id = 1

    def _new_id(self) -> str:
        user_id = str(self._next_id)
        self._next_id += 1
        return user_id

    def create_user(self, username: str, email: str, password: str,
                    full_name: str = "", role: str = "interviewer",
                    google_id: str = None, github_id: str = None) -> Dict[str, Any]:
        username = (username or "").strip().lower()
        email = (email or "").strip().lower()
        if not username or not email or not password:
            return {"success": False, "error": "Username, email and password are required"}
        if any(u["username"] == username or u["email"] == email
               for u in self.users.values()):
            return {"success": False, "error": "Username or email already exists"}
        valid_roles = ["admin", "interviewer", "moderator"]
        if role not in valid_roles:
            role = "interviewer"
        user = {
            "_id": self._new_id(),
            "username": username,
            "email": email,
            "password_hash": hash_password(password),
            "full_name": full_name,
            "role": role,
            "is_active": True,
            "created_at": utc_now(),
            "last_login": None,
            "mfa_enabled": False,
            "mfa_secret": None,
            "google_id": google_id,
            "github_id": github_id,
        }
        self.users[user["_id"]] = user
        return {
            "success": True,
            "user_id": user["_id"],
            "username": username,
            "email": email,
            "role": role,
        }

    def authenticate_user(self, username: str, password: str) -> Dict[str, Any]:
        username = (username or "").strip().lower()
        user = next(
            (u for u in self.users.values()
             if u["username"] == username and u.get("is_active", True)),
            None,
        )
        if not user or not verify_password(password, user["password_hash"]):
            return {"success": False, "error": "Invalid credentials"}
        # Lazy upgrade: re-hash legacy salted-SHA-256 accounts on login.
        if password_needs_rehash(user["password_hash"]):
            user["password_hash"] = hash_password(password)
        user["last_login"] = utc_now()
        return {
            "success": True,
            "user_id": user["_id"],
            "username": user["username"],
            "email": user["email"],
            "full_name": user.get("full_name", ""),
            "role": user["role"],
            "mfa_enabled": user.get("mfa_enabled", False),
        }

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        user = self.users.get(str(user_id))
        return dict(user) if user else None

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        email = (email or "").strip().lower()
        user = next((u for u in self.users.values() if u["email"] == email), None)
        return dict(user) if user else None

    def get_all_users(self) -> List[Dict[str, Any]]:
        users = []
        for user in self.users.values():
            safe = dict(user)
            safe.pop("password_hash", None)
            safe.pop("mfa_secret", None)
            users.append(safe)
        return users

    def update_user_role(self, user_id: str, new_role: str) -> Dict[str, Any]:
        valid_roles = ["admin", "interviewer", "moderator"]
        if new_role not in valid_roles:
            return {"success": False, "error": "Invalid role"}
        user = self.users.get(str(user_id))
        if not user:
            return {"success": False, "error": "User not found"}
        user["role"] = new_role
        return {"success": True}

    def delete_user(self, user_id: str) -> Dict[str, Any]:
        user = self.users.pop(str(user_id), None)
        if not user:
            return {"success": False, "error": "User not found"}
        return {"success": True}

    def create_session(self, user_id: str, remember_me: bool = False) -> Dict[str, Any]:
        if str(user_id) not in self.users:
            return {"success": False, "error": "User not found"}
        session_token = secrets.token_urlsafe(32)
        expires_at = utc_now() + timedelta(days=7 if remember_me else 1)
        self.sessions[session_token] = {
            "user_id": str(user_id),
            "created_at": utc_now(),
            "expires_at": expires_at,
        }
        return {
            "success": True,
            "session_token": session_token,
            "expires_at": expires_at.isoformat(),
        }

    def delete_session(self, session_token: str) -> bool:
        return self.sessions.pop(session_token, None) is not None

    def validate_session(self, session_token: str) -> Optional[Dict[str, Any]]:
        session = self.sessions.get(session_token)
        if not session:
            return None
        if session["expires_at"] < utc_now():
            self.sessions.pop(session_token, None)
            return None
        user = self.get_user_by_id(session["user_id"])
        if not user or not user.get("is_active", True):
            return None
        return {
            "user_id": user["_id"],
            "username": user["username"],
            "email": user["email"],
            "full_name": user.get("full_name", ""),
            "role": user["role"],
        }

    def enable_mfa(self, user_id: str) -> Dict[str, Any]:
        user = self.users.get(str(user_id))
        if not user:
            return {"success": False, "error": "User not found"}
        secret = pyotp.random_base32()
        user["mfa_enabled"] = True
        user["mfa_secret"] = secret
        user["mfa_enabled_at"] = utc_now()
        return {
            "success": True,
            "secret": secret,
            "qr_code_url": f"otpauth://totp/Intervue:{secret}?secret={secret}&issuer=Intervue",
        }

    def disable_mfa(self, user_id: str) -> Dict[str, Any]:
        user = self.users.get(str(user_id))
        if not user:
            return {"success": False, "error": "User not found"}
        user["mfa_enabled"] = False
        user["mfa_secret"] = None
        return {"success": True}

    def verify_totp(self, user_id: str, totp_code: str) -> bool:
        user = self.users.get(str(user_id))
        if not user or not user.get("mfa_enabled") or not user.get("mfa_secret"):
            return False
        return pyotp.TOTP(user["mfa_secret"]).verify(totp_code, valid_window=1)

    def link_oauth_id(self, user_id: str, provider: str, oauth_id: str) -> bool:
        user = self.users.get(str(user_id))
        if not user:
            return False
        user[f"{provider}_id"] = oauth_id
        return True


class DatabaseRouter:
    """Routes database operations to MongoDB when available and to an
    in-memory store when it is not, so user registration/login (email or
    OAuth) keeps working without a live Atlas connection."""

    def __init__(self):
        self.mongo = MongoDBDatabase()
        self.memory = InMemoryBackend()

    @property
    def connected(self) -> bool:
        return self.mongo.connected

    @property
    def db(self):
        return self.mongo.db

    def connect(self) -> bool:
        return self.mongo.connect()

    def is_connected(self) -> bool:
        return self.mongo.is_connected()

    def _backend(self):
        return self.mongo if self.mongo.is_connected() else self.memory

    def create_user(self, *args, **kwargs):
        return self._backend().create_user(*args, **kwargs)

    def authenticate_user(self, *args, **kwargs):
        return self._backend().authenticate_user(*args, **kwargs)

    def get_user_by_email(self, *args, **kwargs):
        return self._backend().get_user_by_email(*args, **kwargs)

    def get_user_by_id(self, *args, **kwargs):
        return self._backend().get_user_by_id(*args, **kwargs)

    def get_all_users(self, *args, **kwargs):
        return self._backend().get_all_users(*args, **kwargs)

    def update_user_role(self, *args, **kwargs):
        return self._backend().update_user_role(*args, **kwargs)

    def delete_user(self, *args, **kwargs):
        return self._backend().delete_user(*args, **kwargs)

    def create_session(self, *args, **kwargs):
        return self._backend().create_session(*args, **kwargs)

    def delete_session(self, *args, **kwargs):
        return self._backend().delete_session(*args, **kwargs)

    def validate_session(self, *args, **kwargs):
        return self._backend().validate_session(*args, **kwargs)

    def enable_mfa(self, *args, **kwargs):
        return self._backend().enable_mfa(*args, **kwargs)

    def disable_mfa(self, *args, **kwargs):
        return self._backend().disable_mfa(*args, **kwargs)

    def verify_totp(self, *args, **kwargs):
        return self._backend().verify_totp(*args, **kwargs)

    def link_oauth_id(self, *args, **kwargs):
        return self._backend().link_oauth_id(*args, **kwargs)

    def get_latest_meeting_for_user(self, user_id):
        """Most recent meeting room for a host. Mongo-only: in-memory rooms
        are not indexed by user_id, so fall back to None when Mongo is down."""
        if not self.mongo.is_connected():
            return None
        return self.mongo.get_latest_meeting_for_user(user_id)

    # Meeting rooms / audit / analytics stay Mongo-backed; state.py already
    # falls back to in-memory meeting rooms when MongoDB is unreachable.
    def get_meeting_room(self, *args, **kwargs):
        return self.mongo.get_meeting_room(*args, **kwargs)

    def create_meeting_room(self, *args, **kwargs):
        return self.mongo.create_meeting_room(*args, **kwargs)

    def create_meeting_session(self, *args, **kwargs):
        return self.mongo.create_meeting_session(*args, **kwargs)

    def add_participant(self, *args, **kwargs):
        return self.mongo.add_participant(*args, **kwargs)

    def update_participant_activity(self, *args, **kwargs):
        return self.mongo.update_participant_activity(*args, **kwargs)

    def remove_participant(self, *args, **kwargs):
        return self.mongo.remove_participant(*args, **kwargs)

    def end_session(self, *args, **kwargs):
        return self.mongo.end_session(*args, **kwargs)

    def get_audit_logs(self, *args, **kwargs):
        return self.mongo.get_audit_logs(*args, **kwargs)

    def add_audit_log(self, *args, **kwargs):
        return self.mongo.add_audit_log(*args, **kwargs)

    def get_meeting_analytics(self, *args, **kwargs):
        return self.mongo.get_meeting_analytics(*args, **kwargs)

    def get_partner_analytics(self, *args, **kwargs):
        return self.mongo.get_partner_analytics(*args, **kwargs)


# ================================================================
# GLOBAL DATABASE INSTANCE
# ================================================================

db = DatabaseRouter()
import os
import time
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from dotenv import load_dotenv

# We only need the supabase client
from supabase import create_client, Client

load_dotenv()

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class SupabaseDatabase:
    def __init__(self):
        self.supabase: Optional[Client] = None
        self.connected = False

    def connect(self) -> bool:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if not url or not key:
            print("[Supabase] Missing SUPABASE_URL or SUPABASE_KEY in .env")
            return False
        try:
            self.supabase = create_client(url, key)
            self.connected = True
            print("[Supabase] Connected to Supabase")
            return True
        except Exception as e:
            print(f"[Supabase] Connection error: {e}")
            return False
            
    # ====================
    # USER AUTH MAPPINGS
    # ====================
    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        # Supabase auth doesn't expose get_user_by_email to anon key securely,
        # but since auth is done natively, we just return dummy dict if they try to check existence
        return None

    def create_user(self, username: str, email: str, password: str, full_name: str = "", role: str = "interviewer", google_id: str = None) -> Dict[str, Any]:
        try:
            res = self.supabase.auth.sign_up({
                "email": email,
                "password": password,
                "options": {
                    "data": {
                        "username": username,
                        "full_name": full_name,
                        "role": role
                    }
                }
            })
            if res.user:
                return {"success": True, "user_id": res.user.id}
            return {"success": False, "error": "Sign up failed"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def authenticate_user(self, email: str, password: str) -> Dict[str, Any]:
        try:
            # We assume email was passed even if var is named username because we require email for Supabase auth
            res = self.supabase.auth.sign_in_with_password({
                "email": email,
                "password": password
            })
            if res.session:
                user = res.user
                return {
                    "success": True, 
                    "user_id": user.id, 
                    "role": user.user_metadata.get("role", "interviewer"),
                    "full_name": user.user_metadata.get("full_name", ""),
                    "access_token": res.session.access_token
                }
            return {"success": False, "error": "Invalid credentials"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ====================
    # MEETINGS
    # ====================
    def create_meeting_room(self, meeting_id: str, host: str, title: str, metadata: Dict[str, Any] = None) -> bool:
        if not self.connected: return False
        try:
            # Requires host_id in metadata if it's referenced in DB. 
            # We will use a dummy UUID if user isn't logged in, but Supabase requires UUID.
            host_id = metadata.get("user_id") if metadata and metadata.get("user_id") else None
            
            # Since the SQL requires host_id as UUID and references auth.users(id), 
            # if we are not authenticated, we can't create it. 
            # For simplicity, let's omit the strict foreign key requirement in the python payload if missing,
            # wait, if table requires it, we must pass it.
            payload = {
                "meeting_id": meeting_id,
                "title": title,
                "settings": metadata or {},
                "status": "waiting"
            }
            if host_id:
                payload["host_id"] = host_id

            self.supabase.table("meeting_rooms").insert(payload).execute()
            return True
        except Exception as e:
            print(f"[Supabase] create_meeting_room error: {e}")
            return False

    def get_meeting_room(self, meeting_id: str) -> Optional[Dict[str, Any]]:
        if not self.connected: return None
        try:
            res = self.supabase.table("meeting_rooms").select("*").eq("meeting_id", meeting_id).execute()
            if res.data:
                # Format to match expectations
                room = res.data[0]
                room["host"] = "Host" # Could join auth.users to get actual host name
                room["metadata"] = room.get("settings", {})
                return room
            return None
        except Exception as e:
            print(f"[Supabase] get_meeting_room error: {e}")
            return None

    def get_latest_meeting_for_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        if not self.connected: return None
        try:
            res = self.supabase.table("meeting_rooms").select("*").eq("host_id", user_id).order("created_at", desc=True).limit(1).execute()
            if res.data:
                return res.data[0]
            return None
        except Exception as e:
            print(f"[DB Error] {e}")
            return None

    # ====================
    # PARTICIPANTS
    # ====================
    def add_participant(self, meeting_id: str, user_id: str, name: str, role: str = "candidate", socket_id: str = None) -> bool:
        if not self.connected: return False
        try:
            self.supabase.table("participants").insert({
                "meeting_id": meeting_id,
                "user_id": user_id,
                "name": name,
                "role": role,
                "socket_id": socket_id,
                "status": "joined"
            }).execute()
            return True
        except Exception as e:
            return False

    def update_participant_activity(self, meeting_id: str, user_id: str) -> bool:
        if not self.connected: return False
        try:
            self.supabase.table("participants").update({"last_active": utc_now().isoformat()}).eq("meeting_id", meeting_id).eq("user_id", user_id).execute()
            return True
        except Exception as e:
            print(f"[DB Error] {e}")
            return False

    def remove_participant(self, meeting_id: str, user_id: str) -> bool:
        if not self.connected: return False
        try:
            self.supabase.table("participants").update({
                "status": "left", 
                "leave_time": utc_now().isoformat()
            }).eq("meeting_id", meeting_id).eq("user_id", user_id).execute()
            return True
        except Exception as e:
            print(f"[DB Error] {e}")
            return False

    # ====================
    # AUDIT LOGS
    # ====================
    def add_audit_log(self, meeting_id: str, event_type: str, title: str, message: str, confidence: float = None, is_critical: bool = False, metadata: Dict[str, Any] = None) -> bool:
        if not self.connected: return False
        try:
            self.supabase.table("audit_logs").insert({
                "meeting_id": meeting_id,
                "event_type": event_type,
                "title": title,
                "message": message,
                "confidence": confidence,
                "is_critical": is_critical,
                "metadata": metadata or {}
            }).execute()
            return True
        except Exception as e:
            print(f"[DB Error] {e}")
            return False

    def get_audit_logs(self, meeting_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.connected: return []
        try:
            res = self.supabase.table("audit_logs").select("*").eq("meeting_id", meeting_id).order("timestamp", desc=True).limit(limit).execute()
            return res.data
        except Exception as e:
            print(f"[DB Error] {e}")
            return []

    # ====================
    # SESSIONS
    # ====================
    def create_meeting_session(self, meeting_id: str) -> bool:
        if not self.connected: return False
        try:
            self.supabase.table("meeting_sessions").insert({
                "meeting_id": meeting_id,
                "status": "active"
            }).execute()
            return True
        except Exception as e:
            print(f"[DB Error] {e}")
            return False

    def end_session(self, meeting_id: str) -> bool:
        if not self.connected: return False
        try:
            self.supabase.table("meeting_sessions").update({
                "status": "ended",
                "ended_at": utc_now().isoformat()
            }).eq("meeting_id", meeting_id).eq("status", "active").execute()
            return True
        except Exception as e:
            print(f"[DB Error] {e}")
            return False

    def get_meeting_analytics(self, meeting_id: str) -> Dict[str, Any]:
        return {"total_incidents": 0, "critical_incidents": 0, "duration_minutes": 0}

    def get_partner_analytics(self, partner_id: str, days: int = 30) -> Dict[str, Any]:
        return {"total_meetings": 0, "total_candidates": 0, "incidents": 0}

# Global instance
db = SupabaseDatabase()
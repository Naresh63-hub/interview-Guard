import os
import time
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone
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
            # create_client() only builds a client object — it never touches
            # the network, so treating construction as "connected" used to
            # report success even with a dead endpoint/credentials and the
            # app would silently degrade to the in-memory fallback. Probe
            # with the same read pattern the app actually relies on.
            self.supabase.table("meeting_rooms").select("meeting_id").limit(1).execute()
            self.connected = True
            print("[Supabase] Connected and verified (meeting_rooms readable)")
            return True
        except Exception as e:
            self.connected = False
            print(f"[Supabase] Connection FAILED: {e}")
            print(
                "[Supabase] PERSISTENCE DISABLED: running on the in-memory "
                "fallback — rooms, participants and audit logs will NOT "
                "survive a server restart."
            )
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
            # An anonymous-key sign_up lets the CLIENT set data.user_metadata,
            # so a caller could self-promote to admin through the public auth
            # endpoint. Whatever the caller asked for, public registration is
            # always a candidate; host roles are assigned server-side only.
            safe_metadata = {
                "username": username,
                "full_name": full_name,
                "role": "candidate",
            }
            res = self.supabase.auth.sign_up({
                "email": email,
                "password": password,
                "options": {
                    "data": safe_metadata
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
                "settings": {**(metadata or {}), "host_name": host},
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
                room["metadata"] = room.get("settings", {})
                # Prefer the display name persisted at creation; fall back to
                # the audit username, then a generic placeholder.
                room["host"] = (
                    room["metadata"].get("host_name")
                    or room["metadata"].get("created_by")
                    or "Host"
                )
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
        """Real per-meeting analytics: incident counts from the audit trail
        and session duration from the sessions table. The previous stub
        always returned zeros while the README advertised risk scoring."""
        if not self.connected:
            return {"meeting_id": meeting_id, "total_incidents": 0, "critical_incidents": 0, "duration_minutes": 0}
        try:
            logs_res = (
                self.supabase.table("audit_logs")
                .select("is_critical, timestamp")
                .eq("meeting_id", meeting_id)
                .execute()
            )
            logs = logs_res.data or []
            total = len(logs)
            critical = sum(1 for l in logs if l.get("is_critical"))

            sessions_res = (
                self.supabase.table("meeting_sessions")
                .select("started_at, ended_at")
                .eq("meeting_id", meeting_id)
                .execute()
            )
            sessions = sessions_res.data or []
            duration_minutes = 0.0
            now = utc_now()
            for s in sessions:
                start_raw, end_raw = s.get("started_at"), s.get("ended_at")
                if not start_raw:
                    continue
                try:
                    start = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
                    end = (
                        datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
                        if end_raw else now
                    )
                    duration_minutes += max(0.0, (end - start).total_seconds() / 60.0)
                except ValueError:
                    continue
            return {
                "meeting_id": meeting_id,
                "total_incidents": total,
                "critical_incidents": critical,
                "duration_minutes": round(duration_minutes, 1),
            }
        except Exception as e:
            print(f"[DB Error] get_meeting_analytics: {e}")
            return {"meeting_id": meeting_id, "total_incidents": 0, "critical_incidents": 0, "duration_minutes": 0}

    def get_partner_analytics(self, partner_id: str, days: int = 30) -> Dict[str, Any]:
        """Aggregate per-partner usage over the last `days` days."""
        empty = {"partner_id": partner_id, "total_meetings": 0, "total_candidates": 0, "incidents": 0}
        if not self.connected:
            return empty
        try:
            cutoff = utc_now() - timedelta(days=days)
            rooms_res = (
                self.supabase.table("meeting_rooms")
                .select("meeting_id, settings")
                .gte("created_at", cutoff.isoformat())
                .execute()
            )
            rooms = [
                r for r in (rooms_res.data or [])
                if (r.get("settings") or {}).get("partner_id") == partner_id
            ]
            meeting_ids = [r.get("meeting_id") for r in rooms if r.get("meeting_id")]
            if not meeting_ids:
                return empty

            cand_res = (
                self.supabase.table("participants")
                .select("user_id")
                .in_("meeting_id", meeting_ids)
                .eq("role", "candidate")
                .execute()
            )
            inc_res = (
                self.supabase.table("audit_logs")
                .select("id")
                .in_("meeting_id", meeting_ids)
                .execute()
            )
            return {
                "partner_id": partner_id,
                "total_meetings": len(meeting_ids),
                "total_candidates": len({c.get("user_id") for c in (cand_res.data or [])}),
                "incidents": len(inc_res.data or []),
            }
        except Exception as e:
            print(f"[DB Error] get_partner_analytics: {e}")
            return empty

# Global instance
db = SupabaseDatabase()
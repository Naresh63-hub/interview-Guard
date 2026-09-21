"""Database connectivity diagnostic.

Run:  .venv/Scripts/python.exe test_db.py

Exit codes: 0 = connected and verified, 1 = degraded (in-memory fallback).
Prints a human-readable summary of what still works and what does not.
"""
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from database import SupabaseDatabase, db  # noqa: E402


def main() -> int:
    print("=== Intervue database diagnostic ===")

    # Probe with a FRESH instance so we test connect() from scratch
    # (the global `db` may already be connected from import time).
    probe = SupabaseDatabase()
    ok = probe.connect()

    if ok:
        # Round-trip a real write + read to prove the full path works.
        # audit_logs.meeting_id has a FK to meeting_rooms, so create a temp
        # room first — exactly the order the app itself uses.
        diag_id = f"DIAG{int(time.time()) % 100000:05d}"
        try:
            probe.create_meeting_room(diag_id, "Diagnostic", "Connectivity Test")
            probe.add_audit_log(
                diag_id,
                "diagnostic",
                "Database Diagnostic",
                "Connectivity write/read test from test_db.py",
            )
            logs = probe.get_audit_logs(diag_id, limit=1)
            roundtrip = bool(logs)
            print(f"[OK] Write/read round-trip: {'PASS' if roundtrip else 'FAIL'}")
            # Best-effort cleanup so diagnostics don't pollute the tables.
            try:
                probe.supabase.table("audit_logs").delete().eq("meeting_id", diag_id).execute()
                probe.supabase.table("meeting_rooms").delete().eq("meeting_id", diag_id).execute()
                print("[OK] Cleanup: diagnostic rows removed")
            except Exception as ce:
                print(f"[WARN] Cleanup skipped: {ce}")
        except Exception as e:
            roundtrip = False
            print(f"[FAIL] Round-trip error: {e}")
    else:
        roundtrip = False

    print()
    print("Persistence backend : Supabase (Postgres)")
    print(f"Connection          : {'UP' if ok else 'DOWN'}")
    print(f"Write/read verified : {'YES' if roundtrip else 'NO'}")
    if not ok:
        print()
        print("The server would now run on the IN-MEMORY fallback:")
        print("  - rooms, participants, audit logs do NOT survive restarts")
        print("  - host login (Supabase Auth) will fail")
        print("Fix SUPABASE_URL / SUPABASE_KEY in .env, then restart the server.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

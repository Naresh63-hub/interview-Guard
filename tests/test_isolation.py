"""Multi-tenancy isolation tests.

The refactor moved every temporal AI-detection state (gaze/pose, liveness
blink history + challenge slot, audio anomaly streaks) from shared
module/instance globals into per-meeting buckets. These tests pin that
contract: mutating one meeting's state must NEVER be visible in, or wiped by,
another meeting.
"""
import time

import numpy as np

from tests.conftest import make_landmarks

import liveness_detector as ld
from audio_analyzer import (
    get_audio_session,
    process_audio_chunk,
    reset_audio_session,
)


# ─── gaze detector SessionState ──────────────────────────────────────────────

def test_gaze_reset_a_leaves_b(detector):
    # A starts (and re-joins) BEFORE B is dirtied: reset_meeting installs a
    # brand-new SessionState object, so any reference captured before a
    # reset points at the discarded instance.
    detector.reset_meeting("ISOAAA")

    sb = detector.reset_meeting("ISOBBB")
    sb.pose_smooth = (1.0, 2.0, 3.0)
    sb.dir_streak = 5
    sb.yolo_hits["phone (coco)"] = 2
    sb.last_yolo_objects = ["phone (coco)"]

    sa = detector.get_session("ISOAAA")
    assert sa is not sb

    fresh_a = detector.reset_meeting("ISOAAA")
    assert fresh_a is not sa
    assert fresh_a.session_gen == sa.session_gen + 1
    assert fresh_a.pose_smooth is None and fresh_a.dir_streak == 0

    # B was never touched by A's reset.
    assert detector.get_session("ISOBBB") is sb
    assert sb.pose_smooth == (1.0, 2.0, 3.0)
    assert sb.dir_streak == 5
    assert sb.yolo_hits == {"phone (coco)": 2}


def test_gaze_reset_does_not_create_default_bucket(detector):
    detector.reset_meeting("ISODFLT")
    assert detector.get_session("ISODFLT") is not detector.get_session(None)
    assert "ISODFLT" in detector.active_session_ids()


def test_gaze_drop_meeting_scoped(detector):
    detector.reset_meeting("ISODROP")
    detector.reset_meeting("ISOKEEP1")
    detector.reset_meeting("ISOKEEP2")

    detector.drop_meeting("ISODROP")
    ids = detector.active_session_ids()
    assert "ISODROP" not in ids
    assert {"ISOKEEP1", "ISOKEEP2"} <= set(ids)


def test_gaze_key_normalization(detector):
    """Lowercase / padded ids map to the same bucket (routes upper() them)."""
    s1 = detector.get_session("isonorm")
    assert detector.get_session("  ISONORM ") is s1


def test_gaze_janitor_evicts_only_idle_sessions(detector):
    """The leak-safety net reclaims abandoned interviews without touching
    live ones. last_used is driven manually so the test needs no sleep."""
    detector.reset_meeting("JNACTIVE")
    detector.reset_meeting("JNIDLE01")
    detector.reset_meeting("JNIDLE02")

    now = time.time()
    # Backdate the two idle sessions well past the TTL; keep one fresh.
    detector.get_session("JNIDLE01").last_used = now - detector._SESSION_TTL_S - 10
    detector.get_session("JNIDLE02").last_used = now - detector._SESSION_TTL_S - 10
    detector.get_session("JNACTIVE").last_used = now

    evicted = detector._sweep_sessions(now)
    assert evicted == 2
    ids = detector.active_session_ids()
    assert "JNACTIVE" in ids
    assert "JNIDLE01" not in ids and "JNIDLE02" not in ids

    # A resumed meeting re-initialises cleanly (no stale object resurrected).
    fresh = detector.get_session("JNIDLE01")
    assert fresh.pose_smooth is None


# ─── liveness blink history + challenge slot ─────────────────────────────────

def test_liveness_blink_history_isolated():
    ld._reset_liveness_state("LIVEAAA")
    ld._reset_liveness_state("LIVEBBB")
    sa = ld.get_liveness_state("LIVEAAA")
    sb = ld.get_liveness_state("LIVEBBB")
    assert sa is not sb and sa["closure_events"] is not sb["closure_events"]

    sa["closure_events"].append(time.time())
    assert len(sb["closure_events"]) == 0

    ld._reset_liveness_state("LIVEAAA")
    assert ld.get_liveness_state("LIVEAAA") is not sa
    assert ld.get_liveness_state("LIVEBBB") is sb


def test_liveness_challenge_isolated():
    ld._reset_liveness_state("CHAAA")
    ld._reset_liveness_state("CHBBB")
    res = ld.issue_challenge("blink", meeting_id="CHAAA")
    assert "error" not in res

    assert ld.get_challenge_state("CHAAA")["active"] is True
    assert ld.get_challenge_state("CHBBB")["active"] is False
    assert ld.get_challenge_state("CHAAA")["type"] == "blink"

    # A join reset for another room must not clear a pending challenge...
    ld._reset_liveness_state("CHCCC")
    assert ld.get_challenge_state("CHAAA")["active"] is True
    # ...while resetting its own room clears both buckets.
    ld._reset_liveness_state("CHAAA")
    assert ld.get_challenge_state("CHAAA")["active"] is False


def test_liveness_analyze_liveness_routes_state_per_meeting():
    ld._reset_liveness_state("ANAAA")
    ld._reset_liveness_state("ANBBB")
    frame = np.full((240, 320, 3), 128, dtype=np.uint8)

    open_lms = make_landmarks(0.35)
    closed_lms = make_landmarks(0.10)

    # closed -> open = one closure event, recorded only in meeting AAA.
    ld.analyze_liveness(frame, closed_lms, meeting_id="ANAAA")
    verdict = ld.analyze_liveness(frame, open_lms, meeting_id="ANAAA")

    assert len(ld.get_liveness_state("ANAAA")["closure_events"]) == 1
    assert len(ld.get_liveness_state("ANBBB")["closure_events"]) == 0
    assert verdict["blinkDetected"] is True
    assert ld.analyze_liveness(frame, open_lms, meeting_id="ANBBB")["blinkDetected"] is False


# ─── audio anomaly streaks ───────────────────────────────────────────────────

def test_audio_sessions_isolated():
    reset_audio_session("AUDAAA")
    reset_audio_session("AUDBBB")
    sa = get_audio_session("AUDAAA")
    sb = get_audio_session("AUDBBB")
    assert sa is not sb

    pcm = np.full(8000, 20000, dtype=np.int16).tobytes()
    now = time.time()
    sa["streaks"]["lip_mismatch"] = 2
    sa["anomaly_since"]["lip_mismatch"] = now - 2.0
    # Resetting B (join) must leave A's half-built streak intact.
    reset_audio_session("AUDBBB")
    assert get_audio_session("AUDAAA")["streaks"]["lip_mismatch"] == 2
    assert get_audio_session("AUDBBB")["streaks"]["lip_mismatch"] == 0

    # A loud chunk with a closed mouth updates only A's state.
    process_audio_chunk(pcm, 16000, mouth_open=False, meeting_id="AUDAAA")
    assert get_audio_session("AUDAAA")["state"]["lipSyncMismatch"] is True
    assert get_audio_session("AUDBBB")["state"]["lipSyncMismatch"] is False


def test_audio_sustained_streak_never_crosses_meetings(monkeypatch):
    reset_audio_session("SSTAAA")
    reset_audio_session("SSTBBB")
    pcm = np.full(8000, 20000, dtype=np.int16).tobytes()

    fake = {"t": time.time()}
    monkeypatch.setattr(
        "audio_analyzer.time.time", lambda: fake["t"], raising=False
    )

    # Three lip-mismatch chunks 0.7s apart for A: sustained (>=3 chunks,
    # >=1.0s span) only after the third — and never for B.
    for _ in range(2):
        fake["t"] += 0.7
        assert process_audio_chunk(pcm, 16000, mouth_open=False, meeting_id="SSTAAA")["suspiciousAudio"] is False
    fake["t"] += 0.7
    assert process_audio_chunk(pcm, 16000, mouth_open=False, meeting_id="SSTAAA")["suspiciousAudio"] is True
    assert process_audio_chunk(pcm, 16000, mouth_open=False, meeting_id="SSTBBB")["suspiciousAudio"] is False


# ─── route-level scoping ─────────────────────────────────────────────────────

def test_session_join_candidate_resets_only_that_room(client, detector):
    """A candidate joining room X must not wipe room Y's detector state."""
    detector.reset_meeting("JOINYYY")
    sy = detector.get_session("JOINYYY")
    sy.pose_smooth = (9.0, 9.0, 9.0)

    with client.session_transaction() as sess:
        sess["candidate_verified_meeting"] = "JOINXXX"
    resp = client.post(
        "/session/join", json={"role": "candidate", "meetingId": "JOINXXX"}
    )
    assert resp.status_code in (200, 201)

    assert detector.get_session("JOINYYY") is sy
    assert sy.pose_smooth == (9.0, 9.0, 9.0)
    assert detector.get_session("JOINXXX").pose_smooth is None


def test_liveness_challenge_route_scoped(client):
    with client.session_transaction() as sess:
        sess["candidate_verified_meeting"] = "CHRTXXX"
    resp = client.post("/liveness-challenge", json={"type": "head"})
    assert resp.status_code == 200

    assert ld.get_challenge_state("CHRTXXX")["active"] is True
    assert ld.get_challenge_state("CHRTYYY")["active"] is False

"""
InterviewOS — Audio Analysis Module
Detects:
  1. Voice Activity (VAD)        — is someone speaking?
  2. Speaker Diarization         — how many distinct voices?
  3. Lip Sync Mismatch           — voice detected but lips not moving
"""

import time
import numpy as np
from collections import deque

# ─── Sustained-anomaly persistence ────────────────────────────────────────────
# A single-chunk spike (a cough, a door slam, a moment of overlapping speech, a
# one-off lip-sync mismatch) is TRANSIENT and must not flag the candidate. A
# signal only becomes suspicious after it persists across several consecutive
# chunks spanning at least _SUSTAINED_MIN_DURATION_S.
_SUSTAINED_CHUNKS = 3            # consecutive chunks required
_SUSTAINED_MIN_DURATION_S = 1.0  # …and spanning at least this long

_signal_streaks = {"multi_speaker": 0, "lip_mismatch": 0}
_anomaly_since = {}


def _update_sustained(component: str, active: bool, now: float) -> bool:
    """Return True only when `active` has persisted across consecutive chunks.

    Any inactive chunk resets the component's streak, so isolated spikes are
    always ignored.
    """
    if active:
        _signal_streaks[component] += 1
        if _signal_streaks[component] == 1:
            _anomaly_since[component] = now
        return (
            _signal_streaks[component] >= _SUSTAINED_CHUNKS
            and (now - _anomaly_since[component]) >= _SUSTAINED_MIN_DURATION_S
        )
    _signal_streaks[component] = 0
    return False

# ─── State ───────────────────────────────────────────────────────────────────
audio_state = {
    "isVoiceActive":      False,
    "speakerCount":       0,
    "lipSyncMismatch":    False,
    "voiceScore":         100,
    "suspiciousAudio":    False,
    "confidence":         95,
    "noiseLevel":         "Low",
    "timestamp":          time.time(),
}

# ─── Numpy VAD Heuristic ─────────────────────────────────────────────────────
def analyze_vad(pcm_bytes: bytes, sample_rate: int = 16000) -> bool:
    """
    Run energy-based VAD on a PCM audio chunk.
    pcm_bytes must be 16-bit mono PCM.
    """
    try:
        audio_np = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if len(audio_np) == 0:
            return False
            
        rms = np.sqrt(np.mean(audio_np ** 2))
        return rms > 0.05 # Threshold for speech
    except Exception as e:
        print(f"VAD error: {e}")
        return False

# ─── Simple Speaker Count Heuristic ──────────────────────────────────────────
def estimate_speaker_count(audio_np: np.ndarray, sample_rate: int = 16000) -> int:
    """
    Simple energy-based speaker estimation.
    This heuristic detects overlapping speech energy patterns.
    """
    if len(audio_np) == 0:
        return 0

    # Split into 500ms windows
    window_size  = sample_rate // 2
    num_windows  = len(audio_np) // window_size
    if num_windows == 0:
        return 1

    energies = []
    for i in range(num_windows):
        window = audio_np[i * window_size: (i + 1) * window_size]
        energy = np.sqrt(np.mean(window ** 2))
        energies.append(energy)

    energies = np.array(energies)
    if np.max(energies) < 0.02:
        return 0    # Silence

    # Normalize
    energies = energies / np.max(energies)

    # Count significant energy transitions (speaker changes)
    threshold    = 0.3
    active       = energies > threshold
    transitions  = np.sum(np.diff(active.astype(int)) != 0)

    # Heuristic: many transitions suggest multiple speakers
    if transitions > 8:
        return 3
    elif transitions > 4:
        return 2
    elif np.any(active):
        return 1
    return 0

# ─── Noise Level Classifier ───────────────────────────────────────────────────
def classify_noise(audio_np: np.ndarray) -> str:
    rms = np.sqrt(np.mean(audio_np ** 2)) if len(audio_np) > 0 else 0
    if rms < 0.02:   return "Low"
    elif rms < 0.10: return "Medium"
    else:            return "High"

# ─── Lip Sync Check ───────────────────────────────────────────────────────────
def check_lip_sync(is_voice_active: bool, mouth_open: bool) -> bool:
    """
    Returns True (mismatch) if voice detected but mouth is closed.
    mouth_open comes from MediaPipe lip landmarks passed from frontend.
    """
    if is_voice_active and not mouth_open:
        return True    # Voice without lip movement = earpiece/second person
    return False

# ─── Main Audio Processing Entry Point ───────────────────────────────────────
def process_audio_chunk(
    pcm_bytes:   bytes,
    sample_rate: int  = 16000,
    mouth_open:  bool = True
) -> dict:
    global audio_state

    # Convert to numpy for analysis
    try:
        audio_np = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    except:
        return audio_state.copy()

    # 1. VAD
    is_voice      = analyze_vad(pcm_bytes, sample_rate)
    now           = time.time()

    # 2. Speaker count
    speaker_count = estimate_speaker_count(audio_np, sample_rate)

    # 3. Lip sync
    lip_mismatch  = check_lip_sync(is_voice, mouth_open)

    # 4. Noise level
    noise_level   = classify_noise(audio_np)

    # Suspicious ONLY if the anomaly is SUSTAINED: multiple speakers or a lip
    # mismatch persisting across consecutive chunks (>= 1s). A single-chunk
    # spike — a cough, a second voice for a moment, a one-off lip mismatch —
    # is transient and must NOT flag the candidate. Constant voice is normal
    # during an interview and never counts on its own.
    sustained_multi = _update_sustained("multi_speaker", speaker_count > 1, now)
    sustained_lip = _update_sustained("lip_mismatch", lip_mismatch, now)
    suspicious = sustained_multi or sustained_lip

    # Voice score: decreases only with SUSTAINED suspicious signals
    voice_score = max(0, min(100,
        100
        - (30 if sustained_multi else 0)
        - (20 if sustained_lip else 0)
    ))

    # Confidence in the verdict: a SUSTAINED anomaly (multi-speaker or lip
    # mismatch persisting across chunks) is high-confidence; a clean chunk is
    # trusted as normal. The fusion layer uses this to weight the audio signal.
    confidence = 70 if suspicious else (95 if not is_voice else 85)

    # Coerce numpy scalars to native Python types: analyze_vad returns
    # np.bool_ which Flask's jsonify cannot serialize (500 on /analyze-audio).
    is_voice = bool(is_voice)
    lip_mismatch = bool(lip_mismatch)
    suspicious = bool(suspicious)
    speaker_count = int(speaker_count)

    audio_state.update({
        "isVoiceActive":   is_voice,
        "speakerCount":    speaker_count,
        "lipSyncMismatch": lip_mismatch,
        "voiceScore":      voice_score,
        "suspiciousAudio": suspicious,
        "confidence":      confidence,
        "noiseLevel":      noise_level,
        "timestamp":       now,
    })

    return audio_state.copy()

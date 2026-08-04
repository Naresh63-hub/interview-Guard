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

# ─── State ───────────────────────────────────────────────────────────────────
audio_state = {
    "isVoiceActive":      False,
    "speakerCount":       0,
    "lipSyncMismatch":    False,
    "voiceScore":         100,
    "suspiciousAudio":    False,
    "noiseLevel":         "Low",
    "timestamp":          time.time(),
}

# Rolling buffer
voice_events = deque(maxlen=50)    # timestamps of voice activity

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

    if is_voice:
        voice_events.append(now)

    # Voice activity in last 10 seconds
    recent_voice  = sum(1 for t in voice_events if now - t <= 10)

    # 2. Speaker count
    speaker_count = estimate_speaker_count(audio_np, sample_rate)

    # 3. Lip sync
    lip_mismatch  = check_lip_sync(is_voice, mouth_open)

    # 4. Noise level
    noise_level   = classify_noise(audio_np)

    # Suspicious if: multiple speakers OR lip mismatch OR voice while muted
    suspicious = (
        speaker_count > 1 or
        lip_mismatch      or
        (is_voice and recent_voice > 20)   # constant background voice
    )

    # Voice score: decreases with suspicious events
    voice_score = max(0, min(100,
        100
        - (30 if speaker_count > 1 else 0)
        - (20 if lip_mismatch else 0)
        - (min(50, recent_voice * 2))
    ))

    audio_state.update({
        "isVoiceActive":   is_voice,
        "speakerCount":    speaker_count,
        "lipSyncMismatch": lip_mismatch,
        "voiceScore":      voice_score,
        "suspiciousAudio": suspicious,
        "noiseLevel":      noise_level,
        "timestamp":       now,
    })

    return audio_state.copy()

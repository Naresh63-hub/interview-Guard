import unittest
import numpy as np
from collections import namedtuple

from audio_analyzer import (
    analyze_vad,
    estimate_speaker_count,
    classify_noise,
    check_lip_sync,
    process_audio_chunk
)
from advanced_gaze_detector import AdvancedGazeDetector
from liveness_detector import analyze_liveness

# Define a mock Landmark named tuple for gaze testing
MockLandmark = namedtuple('MockLandmark', ['x', 'y'])

class TestAudioAnalysis(unittest.TestCase):
    def test_analyze_vad_silence(self):
        # 16000 samples/sec, 16-bit mono = 2 bytes per sample.
        # Let's create 1 second of silence (all zeros)
        pcm_bytes = bytes(32000)
        self.assertFalse(analyze_vad(pcm_bytes))

    def test_analyze_vad_speech(self):
        # Create a high amplitude 440Hz sine wave to simulate sound
        sample_rate = 16000
        duration = 0.5
        t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
        # Scale to max amplitude for int16 (32767)
        signal = (np.sin(2 * np.pi * 440 * t) * 30000).astype(np.int16)
        pcm_bytes = signal.tobytes()
        self.assertTrue(analyze_vad(pcm_bytes, sample_rate))

    def test_classify_noise(self):
        # Low noise
        audio_low = np.zeros(8000, dtype=np.float32)
        self.assertEqual(classify_noise(audio_low), "Low")

        # Medium noise: RMS around 0.05
        # Since RMS = sqrt(mean(x^2)), we can set a constant signal of 0.05
        audio_med = np.full(8000, 0.05, dtype=np.float32)
        self.assertEqual(classify_noise(audio_med), "Medium")

        # High noise: RMS around 0.15
        audio_high = np.full(8000, 0.15, dtype=np.float32)
        self.assertEqual(classify_noise(audio_high), "High")

    def test_check_lip_sync(self):
        # Voice active, mouth closed => mismatch
        self.assertTrue(check_lip_sync(is_voice_active=True, mouth_open=False))
        # Voice active, mouth open => no mismatch
        self.assertFalse(check_lip_sync(is_voice_active=True, mouth_open=True))
        # Voice inactive, mouth closed => no mismatch
        self.assertFalse(check_lip_sync(is_voice_active=False, mouth_open=False))
        # Voice inactive, mouth open => no mismatch
        self.assertFalse(check_lip_sync(is_voice_active=False, mouth_open=True))

    def test_estimate_speaker_count_silence(self):
        audio_np = np.zeros(8000, dtype=np.float32)
        self.assertEqual(estimate_speaker_count(audio_np), 0)

    def test_estimate_speaker_count_single_speaker(self):
        # A single continuous active signal
        audio_np = np.full(8000, 0.5, dtype=np.float32)
        self.assertEqual(estimate_speaker_count(audio_np), 1)

class TestGazeMath(unittest.TestCase):
    def setUp(self):
        self.detector = AdvancedGazeDetector()

    def test_gaze_direction_center(self):
        # Setup landmarks such that the iris is in the center
        # LEFT_EYE indices = [33, 133], RIGHT_EYE = [362, 263], LEFT_IRIS = [468]
        # We'll use LEFT_EYE (33, 133) and LEFT_IRIS (468)
        landmarks = [None] * 500
        landmarks[33] = MockLandmark(0.1, 0.5)
        landmarks[133] = MockLandmark(0.2, 0.5)
        landmarks[468] = MockLandmark(0.15, 0.5) # Exactly half-way (ratio 0.50)

        direction = self.detector._get_gaze_direction(
            landmarks=landmarks,
            eye_points=[33, 133],
            iris_point=[468],
            frame_w=100
        )
        self.assertEqual(direction, "CENTER")

    def test_gaze_direction_right(self):
        # Setup landmarks such that the iris is towards the lower ratio (image right)
        landmarks = [None] * 500
        landmarks[33] = MockLandmark(0.1, 0.5)
        landmarks[133] = MockLandmark(0.2, 0.5)
        landmarks[468] = MockLandmark(0.13, 0.5) # ratio = (13 - 10)/(20 - 10) = 0.30 < 0.40

        direction = self.detector._get_gaze_direction(
            landmarks=landmarks,
            eye_points=[33, 133],
            iris_point=[468],
            frame_w=100
        )
        self.assertEqual(direction, "RIGHT")

    def test_gaze_direction_left(self):
        # Setup landmarks such that the iris is towards the higher ratio (image left)
        landmarks = [None] * 500
        landmarks[33] = MockLandmark(0.1, 0.5)
        landmarks[133] = MockLandmark(0.2, 0.5)
        landmarks[468] = MockLandmark(0.18, 0.5) # ratio = (18 - 10)/(20 - 10) = 0.80 > 0.60

        direction = self.detector._get_gaze_direction(
            landmarks=landmarks,
            eye_points=[33, 133],
            iris_point=[468],
            frame_w=100
        )
        self.assertEqual(direction, "LEFT")

    def test_get_landmarks_no_mesh(self):
        self.detector.face_mesh = None
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        self.assertEqual(self.detector.get_landmarks(frame), [])

    def test_analyze_frame_returns_annotated_frame(self):
        self.detector.face_mesh = None
        frame = np.zeros((100, 100, 3), dtype=np.uint8)

        result = self.detector.analyze_frame(frame)

        self.assertIsNotNone(result["annotatedFrame"])
        self.assertTrue(result["annotatedFrame"].startswith("data:image/jpeg;base64,"))

    def test_suspicious_object_labels_include_v2_classes(self):
        labels = self.detector.SUSPICIOUS_OBJECT_LABELS

        self.assertIn("mobile phone", labels)
        self.assertIn("earphone", labels)
        self.assertIn("headphone", labels)


class TestLivenessAnalysis(unittest.TestCase):
    def test_analyze_liveness_returns_placeholder_result(self):
        frame = np.full((80, 80, 3), 120, dtype=np.uint8)

        result = analyze_liveness(frame)

        self.assertTrue(result["is_live"])
        self.assertEqual(result["risk"], "low")
        self.assertEqual(result["reason"], "placeholder_liveness_model")

    def test_analyze_liveness_flags_empty_frame(self):
        result = analyze_liveness(None)

        self.assertFalse(result["is_live"])
        self.assertEqual(result["risk"], "high")

if __name__ == '__main__':
    unittest.main()

import base64
import os
import time
from typing import Any

import cv2
import numpy as np

# Try to import new MediaPipe Tasks API
try:
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    mp_available = True
except ImportError:
    mp_available = False

try:
    from ultralytics import YOLO
    yolo_available = True
except ImportError:
    yolo_available = False

class YOLOEnsemble:
    """Wrapper that combines multiple YOLO models into a single interface."""
    def __init__(self, model_candidates: list[tuple[str, str]]) -> None:
        self.models = []
        self.names = {}
        loaded_models = []
        
        for i, (model_type, model_path) in enumerate(model_candidates):
            if os.path.exists(model_path):
                try:
                    model = YOLO(model_path)
                    self.models.append((model_type, model))
                    loaded_models.append(model_type)
                    # Map the classes to the ensemble names dict
                    for cls_id, cls_name in model.names.items():
                        ensemble_cls_id = i * 1000 + cls_id
                        self.names[ensemble_cls_id] = f"{cls_name} ({model_type})"
                    print(f"✅ Ensemble loaded {model_type}: {model_path}")
                except Exception as exc:
                    print(f"❌ Ensemble failed to load {model_path}: {exc}")

        if self.models:
            self.status = f"ensemble: {', '.join(loaded_models)}"
            self.is_custom_yolo = any(m_type.startswith("custom") for m_type, _ in self.models)
        else:
            self.status = "disabled: no YOLO models loaded"
            self.is_custom_yolo = False

    def __call__(self, frame, verbose=False):
        class EnsembleBox:
            def __init__(self, cls_id):
                self.cls = [cls_id]

        class EnsembleResult:
            def __init__(self, boxes):
                self.boxes = boxes

        ensemble_boxes = []
        for i, (model_type, model) in enumerate(self.models):
            try:
                results = model(frame, verbose=verbose)
                for r in results:
                    for box in r.boxes:
                        cls_id = int(box.cls[0])
                        ensemble_cls_id = i * 1000 + cls_id
                        ensemble_boxes.append(EnsembleBox(ensemble_cls_id))
            except Exception:
                pass
        
        return [EnsembleResult(ensemble_boxes)]

import torch
import torch.nn as nn

class HeadPoseAutoencoder(nn.Module):
    def __init__(self, input_dim):
        super().__init__()
        hidden = max(8, input_dim // 2)
        bottleneck = max(4, input_dim // 8)
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, bottleneck),
            nn.ReLU(),
        )
        self.decoder = nn.Sequential(
            nn.Linear(bottleneck, hidden),
            nn.ReLU(),
            nn.Linear(hidden, input_dim),
        )

    def forward(self, x):
        return self.decoder(self.encoder(x))

class AdvancedGazeDetector:
    # Outer and inner corner indices for face landmarker
    LEFT_EYE = [33, 133]
    RIGHT_EYE = [362, 263]
    LEFT_IRIS = [468]
    RIGHT_IRIS = [473]
    _disabled_yolo_models = set()
    SUSPICIOUS_OBJECT_LABELS = {
        "cell phone",
        "mobile phone",
        "phone",
        "earphone",
        "earphones",
        "earpiece",
        "headphone",
        "headphones",
        "smartwatch",
        "tablet",
        "notebook",
        "book",
        "laptop",
        "pen camera",
        "mini device",
    }

    def __init__(self) -> None:
        self.face_mesh = None
        self._init_mediapipe()
        
        self.face_detector = self._load_face_detector()
        
        self.yolo_model = None
        self.last_yolo_time = 0
        self.last_yolo_objects = []
        self.is_custom_yolo = False
        self.object_detector_status = "unavailable"
        if yolo_available:
            self._init_yolo()

        self.pose_autoencoder = None
        self.pose_mean = None
        self.pose_std = None
        self.pose_threshold = None
        self._init_pose_autoencoder()

        self.metadata = {
            "module": "advanced_gaze_detector.py",
            "engine": "MediaPipe FaceLandmarker" if self.face_mesh else "Haar Cascade",
            "estimatedAccuracy": 95 if self.face_mesh else 50,
            "nextSteps": [
                "Train a custom ML model on interview-specific data.",
            ],
            "objectDetector": self.object_detector_status,
        }

    def _init_yolo(self) -> None:
        model_candidates = [
            ("custom-v2", "cheating_detector_v2.pt"),
            ("custom", "cheating_detector.pt"),
        ]

        ensemble = YOLOEnsemble(model_candidates)
        if ensemble.models:
            self.yolo_model = ensemble
            self.object_detector_status = ensemble.status
            self.is_custom_yolo = ensemble.is_custom_yolo
        else:
            self.yolo_model = None
            self.object_detector_status = ensemble.status
            self.is_custom_yolo = False

    def _init_pose_autoencoder(self) -> None:
        autoencoder_path = os.path.join("trained_models", "head_pose_autoencoder.pt")
        if os.path.exists(autoencoder_path):
            try:
                checkpoint = torch.load(autoencoder_path, map_location="cpu", weights_only=False)
                input_dim = checkpoint["input_dim"]
                self.pose_mean = checkpoint["mean"]
                self.pose_std = checkpoint["std"]
                self.pose_threshold = checkpoint["threshold"]
                
                self.pose_autoencoder = HeadPoseAutoencoder(input_dim)
                self.pose_autoencoder.load_state_dict(checkpoint["model_state"])
                self.pose_autoencoder.eval()
                print(f"✅ Loaded head pose autoencoder with threshold: {self.pose_threshold:.4f}")
            except Exception as e:
                self.pose_autoencoder = None
                print(f"❌ Failed to load head pose autoencoder: {e}")

    def _init_mediapipe(self):
        if not mp_available: return
        model_path = 'face_landmarker.task'
        if not os.path.exists(model_path):
            print(f"[AdvancedGazeDetector] Could not find {model_path}")
            return
            
        try:
            base_options = python.BaseOptions(model_asset_path=model_path)
            options = vision.FaceLandmarkerOptions(
                base_options=base_options,
                output_face_blendshapes=False,
                output_facial_transformation_matrixes=False,
                num_faces=4
            )
            self.face_mesh = vision.FaceLandmarker.create_from_options(options)
        except Exception as e:
            print(f"[AdvancedGazeDetector] Failed to initialize FaceLandmarker: {e}")
            self.face_mesh = None

    @property
    def mediapipe_available(self) -> bool:
        return self.face_mesh is not None

    def _load_face_detector(self) -> cv2.CascadeClassifier:
        data_dir = getattr(getattr(cv2, "data", None), "haarcascades", None)
        cascade_path = (
            os.path.join(data_dir, "haarcascade_frontalface_default.xml")
            if data_dir
            else "haarcascade_frontalface_default.xml"
        )
        return cv2.CascadeClassifier(cascade_path)

    def _get_gaze_direction(
        self,
        landmarks: list[Any],
        eye_points: list[int],
        iris_point: list[int],
        frame_w: int,
    ) -> str:
        left = landmarks[eye_points[0]]
        right = landmarks[eye_points[1]]
        iris = landmarks[iris_point[0]]

        left_x = int(left.x * frame_w)
        right_x = int(right.x * frame_w)
        iris_x = int(iris.x * frame_w)

        min_x = min(left_x, right_x)
        max_x = max(left_x, right_x)
        span = max_x - min_x
        
        if span <= 0:
            return "CENTER"

        ratio = (iris_x - min_x) / span
        
        # When a user looks to their left, the pupil moves to the right of the image (x=1.0)
        # So a high ratio (>0.60) means they are looking left!
        if ratio > 0.60:
            return "LEFT"
        if ratio < 0.40:
            return "RIGHT"
        return "CENTER"

    def _get_head_pose(self, landmarks, frame_w, frame_h):
        # 3D model points
        face3Dmodel = np.array([
            (0.0, 0.0, 0.0),            # Nose tip
            (0.0, -330.0, -65.0),       # Chin
            (-225.0, 170.0, -135.0),    # Left eye left corner
            (225.0, 170.0, -135.0),     # Right eye right corner
            (-150.0, -150.0, -125.0),   # Left mouth corner
            (150.0, -150.0, -125.0)     # Right mouth corner
        ], dtype=np.float64)

        # 2D image points from MediaPipe
        image_points = np.array([
            (landmarks[1].x * frame_w, landmarks[1].y * frame_h),
            (landmarks[152].x * frame_w, landmarks[152].y * frame_h),
            (landmarks[33].x * frame_w, landmarks[33].y * frame_h),
            (landmarks[263].x * frame_w, landmarks[263].y * frame_h),
            (landmarks[61].x * frame_w, landmarks[61].y * frame_h),
            (landmarks[291].x * frame_w, landmarks[291].y * frame_h)
        ], dtype=np.float64)

        focal_length = frame_w
        center = (frame_w / 2, frame_h / 2)
        camera_matrix = np.array([
            [focal_length, 0, center[0]],
            [0, focal_length, center[1]],
            [0, 0, 1]
        ], dtype=np.float64)

        dist_coeffs = np.zeros((4, 1))

        success, rotation_vector, translation_vector = cv2.solvePnP(
            face3Dmodel, image_points, camera_matrix, dist_coeffs, flags=cv2.SOLVEPNP_ITERATIVE
        )

        if not success:
            return 0, 0, 0

        rmat, _ = cv2.Rodrigues(rotation_vector)
        angles, _, _, _, _, _ = cv2.RQDecomp3x3(rmat)

        pitch = angles[0] * 360
        yaw = angles[1] * 360
        roll = angles[2] * 360

        return pitch, yaw, roll

    def _analyze_corneal_reflection(self, frame, landmarks, width, height):
        left_iris_indices = [474, 475, 476, 477]
        right_iris_indices = [469, 470, 471, 472]
        
        reflection_detected = False
        
        for indices in [left_iris_indices, right_iris_indices]:
            # Bounding box of the iris
            x_coords = [int(landmarks[i].x * width) for i in indices]
            y_coords = [int(landmarks[i].y * height) for i in indices]
            
            x_min, x_max = max(0, min(x_coords) - 5), min(width, max(x_coords) + 5)
            y_min, y_max = max(0, min(y_coords) - 5), min(height, max(y_coords) + 5)
            
            if x_max <= x_min or y_max <= y_min:
                continue
                
            # Extract iris patch
            iris_patch = frame[y_min:y_max, x_min:x_max]
            
            # OpenCV Algorithmic Model (Phase 1)
            try:
                gray = cv2.cvtColor(iris_patch, cv2.COLOR_BGR2GRAY)
                # Isolate extreme brightness (screen reflection)
                _, thresh = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY)
                
                contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                
                for contour in contours:
                    area = cv2.contourArea(contour)
                    if area > 4: # Size threshold for a screen reflection in a high-res iris patch
                        # Shape approximation
                        epsilon = 0.15 * cv2.arcLength(contour, True)
                        approx = cv2.approxPolyDP(contour, epsilon, True)
                        
                        # 4 corners = rectangular screen
                        if len(approx) == 4:
                            reflection_detected = True
                            cv2.drawContours(iris_patch, [approx], 0, (0, 0, 255), 1)
                            
                frame[y_min:y_max, x_min:x_max] = iris_patch
            except Exception as e:
                pass
                
        return reflection_detected

    def get_landmarks(self, frame: np.ndarray) -> list[dict[str, float]]:
        if not self.face_mesh:
            return []
        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = self.face_mesh.detect(mp_image)
            if result and result.face_landmarks:
                landmarks = result.face_landmarks[0]
                return [{"x": l.x, "y": l.y, "z": l.z} for l in landmarks]
        except Exception:
            pass
        return []

    def analyze_frame(self, frame: np.ndarray) -> dict[str, Any]:
        height, width, _ = frame.shape
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        direction = "NO_FACE"
        face_detected = False
        multiple_faces = False
        reflection_detected = False
        engine = "none"
        
        pitch, yaw, roll = 0, 0, 0
        pose_abnormal = False

        # Throttled YOLO Object Detection (every 3 seconds)
        if getattr(self, "yolo_model", None) is not None and getattr(self.yolo_model, "models", None):
            now = time.time()
            if now - self.last_yolo_time > 3.0:
                self.last_yolo_time = now
                self.last_yolo_objects = []
                try:
                    results = self.yolo_model(frame, verbose=False)
                    for r in results:
                        for box in r.boxes:
                            cls_id = int(box.cls[0])
                            cls_name = self.yolo_model.names[cls_id]
                            
                            base_name = cls_name.split(" (")[0]
                            if base_name.lower() in self.SUSPICIOUS_OBJECT_LABELS:
                                self.last_yolo_objects.append(cls_name)
                    self.last_yolo_objects = list(set(self.last_yolo_objects))
                except Exception:
                    pass
        objects_detected = getattr(self, "last_yolo_objects", [])

        if self.face_mesh:
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = self.face_mesh.detect(mp_image)
            
            if result and result.face_landmarks:
                face_detected = True
                multiple_faces = len(result.face_landmarks) > 1
                
                landmarks = result.face_landmarks[0]
                left_gaze = self._get_gaze_direction(
                    landmarks,
                    self.LEFT_EYE,
                    self.LEFT_IRIS,
                    width,
                )
                right_gaze = self._get_gaze_direction(
                    landmarks,
                    self.RIGHT_EYE,
                    self.RIGHT_IRIS,
                    width,
                )
                
                # Head Pose Estimation override
                pitch, yaw, roll = self._get_head_pose(landmarks, width, height)
                
                # Run anomaly detection using the Autoencoder
                if self.pose_autoencoder is not None:
                    try:
                        pose_vec = np.array([[pitch, yaw, roll]], dtype=np.float32)
                        pose_normalized = (pose_vec - self.pose_mean) / self.pose_std
                        pose_tensor = torch.tensor(pose_normalized, dtype=torch.float32)
                        with torch.no_grad():
                            recon = self.pose_autoencoder(pose_tensor)
                            mse_loss = ((recon - pose_tensor) ** 2).mean(dim=1).item()
                        
                        if self.pose_threshold is not None:
                            pose_abnormal = bool(mse_loss > self.pose_threshold)
                    except Exception as e:
                        print(f"[AdvancedGazeDetector] Pose autoencoder inference error: {e}")
                
                # Phase 1: Corneal Reflection Analysis
                reflection_detected = self._analyze_corneal_reflection(frame, landmarks, width, height)
                
                if pitch < -10:
                    direction = "LOOKING_DOWN"
                elif pitch > 15:
                    direction = "LOOKING_UP"
                elif yaw < -15:
                    direction = "RIGHT"
                elif yaw > 15:
                    direction = "LEFT"
                elif left_gaze == right_gaze:
                    direction = left_gaze
                elif "LEFT" in (left_gaze, right_gaze):
                    direction = "LEFT"
                elif "RIGHT" in (left_gaze, right_gaze):
                    direction = "RIGHT"
                else:
                    direction = "CENTER"
                engine = "mediapipe-tasks"

        # If MediaPipe fails or is not available, just use basic face detection.
        # We DO NOT try to guess left/right with Haar because it flickers wildly!
        if not face_detected and not self.face_detector.empty():
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self.face_detector.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(80, 80),
            )
            if len(faces) > 0:
                face_detected = True
                direction = "CENTER"
                engine = "haar-fallback"

        # Encode the frame to base64 for the Live Eye Analysis PiP
        annotated_b64 = None
        try:
            # Draw a simple box/text for debugging
            if face_detected:
                cv2.putText(frame, direction, (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            ret, buffer = cv2.imencode('.jpg', frame)
            if ret:
                annotated_b64 = "data:image/jpeg;base64," + base64.b64encode(buffer).decode('utf-8')
        except:
            pass

        return {
            "direction": direction,
            "lookingAway": direction in {"LEFT", "RIGHT", "NO_FACE", "LOOKING_DOWN", "LOOKING_UP"},
            "faceDetected": face_detected,
            "multipleFaces": multiple_faces,
            "reflectionDetected": reflection_detected,
            "pose": {"pitch": pitch, "yaw": yaw, "roll": roll, "abnormal": pose_abnormal},
            "objectsDetected": objects_detected,
            "timestamp": time.time(),
            "engine": engine,
            "estimatedAccuracy": self.metadata["estimatedAccuracy"],
            "annotatedFrame": annotated_b64
        }

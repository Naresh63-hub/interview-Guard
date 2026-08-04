import os
import cv2
import numpy as np
import time

try:
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    mp_available = True
except ImportError:
    mp_available = False

# 3D generic facial landmark model points
FACE_3D_MODEL = np.array([
    (0.0, 0.0, 0.0),            # Nose tip
    (0.0, -330.0, -65.0),       # Chin
    (-225.0, 170.0, -135.0),    # Left eye left corner
    (225.0, 170.0, -135.0),     # Right eye right corner
    (-150.0, -150.0, -125.0),   # Left mouth corner
    (150.0, -150.0, -125.0)     # Right mouth corner
], dtype=np.float64)

def calculate_head_pose(landmarks, w, h):
    # 2D landmarks corresponding to the 3D model points
    # Landmarks map to MediaPipe indices:
    # 1: Nose tip
    # 152: Chin
    # 33: Left eye left corner (user's right corner from camera view)
    # 263: Right eye right corner
    # 61: Left mouth corner
    # 291: Right mouth corner
    image_points = np.array([
        (landmarks[1].x * w, landmarks[1].y * h),
        (landmarks[152].x * w, landmarks[152].y * h),
        (landmarks[33].x * w, landmarks[33].y * h),
        (landmarks[263].x * w, landmarks[263].y * h),
        (landmarks[61].x * w, landmarks[61].y * h),
        (landmarks[291].x * w, landmarks[291].y * h)
    ], dtype=np.float64)

    focal_length = w
    center = (w / 2, h / 2)
    camera_matrix = np.array([
        [focal_length, 0, center[0]],
        [0, focal_length, center[1]],
        [0, 0, 1]
    ], dtype=np.float64)

    dist_coeffs = np.zeros((4, 1))
    success, rotation_vector, translation_vector = cv2.solvePnP(
        FACE_3D_MODEL, image_points, camera_matrix, dist_coeffs, flags=cv2.SOLVEPNP_ITERATIVE
    )

    if not success:
        return None

    rmat, _ = cv2.Rodrigues(rotation_vector)
    angles, _, _, _, _, _ = cv2.RQDecomp3x3(rmat)

    pitch = angles[0] * 360
    yaw = angles[1] * 360
    roll = angles[2] * 360

    return pitch, yaw, roll

def main():
    if not mp_available:
        print("❌ MediaPipe is not installed. Please install requirements-training.txt.")
        return

    model_path = 'face_landmarker.task'
    if not os.path.exists(model_path):
        print(f"❌ Could not find {model_path} in current directory. Please run from project root.")
        return

    # Setup MediaPipe
    base_options = python.BaseOptions(model_asset_path=model_path)
    options = vision.FaceLandmarkerOptions(
        base_options=base_options,
        output_face_blendshapes=False,
        output_facial_transformation_matrixes=False,
        num_faces=1
    )
    detector = vision.FaceLandmarker.create_from_options(options)

    # Output directory setup
    out_dir = os.path.join("datasets", "head_pose")
    os.makedirs(out_dir, exist_ok=True)

    normal_poses = []
    anomaly_poses = []
    recording_mode = None # 'normal', 'anomaly', or None

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ Cannot open camera.")
        return

    print("\n" + "="*50)
    print("      HEAD POSE DATA COLLECTION SCRIPT")
    print("="*50)
    print("Instructions:")
    print("  - Press 'N' to start/pause recording NORMAL behaviors")
    print("    (Look at the screen, read, type normally)")
    print("  - Press 'A' to start/pause recording ANOMALOUS behaviors")
    print("    (Look far left, right, up, down, read notes)")
    print("  - Press 'Q' to Quit and Save")
    print("="*50 + "\n")

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        h, w, _ = frame.shape
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = detector.detect(mp_image)

        pose = None
        if result and result.face_landmarks:
            landmarks = result.face_landmarks[0]
            pose = calculate_head_pose(landmarks, w, h)
            
            if pose:
                pitch, yaw, roll = pose
                # Save data based on mode
                if recording_mode == 'normal':
                    normal_poses.append([pitch, yaw, roll])
                elif recording_mode == 'anomaly':
                    anomaly_poses.append([pitch, yaw, roll])

                # Draw status
                color = (0, 255, 0) if recording_mode == 'normal' else ((0, 0, 255) if recording_mode == 'anomaly' else (255, 255, 255))
                status_text = f"Recording: {recording_mode.upper()}" if recording_mode else "Status: PAUSED"
                cv2.putText(frame, status_text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                cv2.putText(frame, f"Pitch: {pitch:.1f}  Yaw: {yaw:.1f}  Roll: {roll:.1f}", (20, 75), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
                
                # Draw facial landmark points
                # Draw lines between key nose, chin, eye landmarks
                for index in [1, 152, 33, 263, 61, 291]:
                    lm = landmarks[index]
                    cv2.circle(frame, (int(lm.x * w), int(lm.y * h)), 4, color, -1)

        # Show frame
        cv2.imshow("Head Pose Data Collector", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('n'):
            if recording_mode == 'normal':
                recording_mode = None
                print("⏸ Paused recording normal poses.")
            else:
                recording_mode = 'normal'
                print("⏺ Recording normal poses...")
        elif key == ord('a'):
            if recording_mode == 'anomaly':
                recording_mode = None
                print("⏸ Paused recording anomaly poses.")
            else:
                recording_mode = 'anomaly'
                print("⏺ Recording anomaly poses...")
        elif key == ord('q'):
            print("\nExiting data collector...")
            break

    cap.release()
    cv2.destroyAllWindows()

    # Save normal data
    if normal_poses:
        train_path = os.path.join(out_dir, "normal_train.csv")
        np.savetxt(train_path, normal_poses, delimiter=",", fmt="%.4f")
        print(f"✅ Saved {len(normal_poses)} normal training samples to: {train_path}")
    else:
        print("⚠ No normal poses recorded.")

    # Save anomaly data
    if anomaly_poses:
        val_path = os.path.join(out_dir, "val.csv")
        np.savetxt(val_path, anomaly_poses, delimiter=",", fmt="%.4f")
        print(f"✅ Saved {len(anomaly_poses)} anomaly samples to: {val_path}")
    elif normal_poses:
        # If normal was collected but no anomalies, write a subset of normal to val.csv for compatibility
        val_path = os.path.join(out_dir, "val.csv")
        np.savetxt(val_path, normal_poses[:min(200, len(normal_poses))], delimiter=",", fmt="%.4f")
        print(f"✅ Copied subset of normal poses to: {val_path} (for verification thresholding)")

if __name__ == "__main__":
    main()

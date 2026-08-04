# Model Training Guide

This folder is for training the AI models used by the proctoring project.

Keep training separate from the Flask app:

- App code uses `requirements.txt`
- Training code uses `requirements-training.txt`
- Final trained files go into `trained_models/`

## Step 1: Install Training Packages

From the project root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-training.txt
```

## Step 2: Create Dataset Folders

Use this layout:

```text
datasets/
  face_liveness/
    train/
      live/
      spoof/
    val/
      live/
      spoof/

  audio_whisper/
    train/
      ambient_noise/
      normal_speech/
      whisper/
    val/
      ambient_noise/
      normal_speech/
      whisper/

  audio_spoof/
    train/
      live/
      spoof/
    val/
      live/
      spoof/

  object_detection/
    data.yaml
    train/
    val/

  head_pose/
    normal_train.csv
    val.csv
```

## Step 3: Train Models

Face liveness:

```powershell
.\.venv\Scripts\python.exe training\train_face_liveness.py --data datasets\face_liveness --out trained_models\face_liveness.pt
```

Whisper detector:

```powershell
.\.venv\Scripts\python.exe training\train_audio_classifier.py --data datasets\audio_whisper --out trained_models\whisper_detector.joblib
```

Audio spoof detector:

```powershell
.\.venv\Scripts\python.exe training\train_audio_classifier.py --data datasets\audio_spoof --out trained_models\audio_spoof_detector.joblib
```

Object detection with YOLOv8:

```powershell
.\.venv\Scripts\python.exe training\train_yolo_objects.py --data datasets\object_detection\data.yaml --out trained_models
```

Head pose anomaly model:

```powershell
.\.venv\Scripts\python.exe training\train_head_pose_autoencoder.py --train datasets\head_pose\normal_train.csv --val datasets\head_pose\val.csv --out trained_models\head_pose_autoencoder.pt
```

## Recommended Training Order

1. Face liveness
2. Whisper detector
3. Object detection improvement
4. Audio spoof detector
5. Head pose anomaly detector

Train one model at a time. After each model works, connect it to the app.

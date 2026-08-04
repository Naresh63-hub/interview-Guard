import argparse
from pathlib import Path

import joblib
import librosa
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}


def extract_features(path):
    audio, sample_rate = librosa.load(path, sr=16000, mono=True)
    mfcc = librosa.feature.mfcc(y=audio, sr=sample_rate, n_mfcc=20)
    centroid = librosa.feature.spectral_centroid(y=audio, sr=sample_rate)
    bandwidth = librosa.feature.spectral_bandwidth(y=audio, sr=sample_rate)
    rolloff = librosa.feature.spectral_rolloff(y=audio, sr=sample_rate)
    zcr = librosa.feature.zero_crossing_rate(audio)
    rms = librosa.feature.rms(y=audio)

    features = [
        mfcc.mean(axis=1),
        mfcc.std(axis=1),
        centroid.mean(axis=1),
        bandwidth.mean(axis=1),
        rolloff.mean(axis=1),
        zcr.mean(axis=1),
        rms.mean(axis=1),
        rms.std(axis=1),
    ]
    return np.concatenate(features)


def load_split(split_dir):
    x_values = []
    y_values = []
    split_dir = Path(split_dir)

    for class_dir in sorted(p for p in split_dir.iterdir() if p.is_dir()):
        for audio_path in class_dir.rglob("*"):
            if audio_path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            x_values.append(extract_features(audio_path))
            y_values.append(class_dir.name)

    return np.asarray(x_values), np.asarray(y_values)


def train(args):
    data_dir = Path(args.data)
    x_train, y_train = load_split(data_dir / "train")
    x_val, y_val = load_split(data_dir / "val")

    if len(x_train) == 0:
        raise ValueError("No training audio files found.")

    model = make_pipeline(
        StandardScaler(),
        RandomForestClassifier(n_estimators=300, random_state=42, class_weight="balanced"),
    )
    model.fit(x_train, y_train)

    if len(x_val):
        predictions = model.predict(x_val)
        print(classification_report(y_val, predictions))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "model": model,
        "classes": sorted(set(y_train)),
        "feature_type": "mfcc_spectral_random_forest",
    }, out_path)
    print(f"saved {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    train(parser.parse_args())

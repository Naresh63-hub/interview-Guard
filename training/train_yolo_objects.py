import argparse
from pathlib import Path

from ultralytics import YOLO


def train(args):
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    model = YOLO(args.base_model)
    results = model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.image_size,
        project=str(out_dir),
        name="object_detector",
    )
    print(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--base-model", default="yolov8n.pt")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--image-size", type=int, default=640)
    train(parser.parse_args())

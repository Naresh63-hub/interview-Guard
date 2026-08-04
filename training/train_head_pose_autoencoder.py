import argparse
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


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


def load_csv(path):
    data = np.loadtxt(path, delimiter=",", dtype=np.float32)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    return data


def train(args):
    train_data = load_csv(args.train)
    mean = train_data.mean(axis=0)
    std = train_data.std(axis=0) + 1e-6
    train_data = (train_data - mean) / std

    x_train = torch.tensor(train_data, dtype=torch.float32)
    loader = DataLoader(TensorDataset(x_train), batch_size=args.batch_size, shuffle=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = HeadPoseAutoencoder(x_train.shape[1]).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    criterion = nn.MSELoss()

    for epoch in range(args.epochs):
        model.train()
        losses = []
        for (batch,) in loader:
            batch = batch.to(device)
            optimizer.zero_grad()
            recon = model(batch)
            loss = criterion(recon, batch)
            loss.backward()
            optimizer.step()
            losses.append(loss.item())
        print(f"epoch={epoch + 1} loss={float(np.mean(losses)):.6f}")

    threshold = None
    if args.val:
        val_data = (load_csv(args.val) - mean) / std
        x_val = torch.tensor(val_data, dtype=torch.float32).to(device)
        with torch.no_grad():
            recon = model(x_val)
            errors = ((recon - x_val) ** 2).mean(dim=1).cpu().numpy()
        threshold = float(np.percentile(errors, 95))
        print(f"validation anomaly threshold={threshold:.6f}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "model_state": model.state_dict(),
        "input_dim": x_train.shape[1],
        "mean": mean,
        "std": std,
        "threshold": threshold,
    }, out_path)
    print(f"saved {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", required=True)
    parser.add_argument("--val")
    parser.add_argument("--out", required=True)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    train(parser.parse_args())

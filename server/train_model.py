"""
Train a binary "crack vs no-crack" classifier and export it to ONNX.

The detector then loads the ONNX file via cv2.dnn for fast CPU inference,
with no new runtime dependency.

== Setup (one time) ==

1. Install training-only dependencies into the venv:
       .venv\\Scripts\\pip.exe install -r requirements-train.txt

2. Download the Surface Crack Detection dataset:
       https://www.kaggle.com/datasets/arunrk7/surface-crack-detection
   (Free Kaggle account needed. ~230 MB zip.)

3. Unzip so the folder structure under server/dataset/ looks like:
       server/dataset/Positive/   (~20000 images of cracks)
       server/dataset/Negative/   (~20000 images of clean concrete)

== Run ==

   .venv\\Scripts\\python.exe train_model.py

Trains for 3 epochs and exports to  crack_classifier.onnx  (~6 MB).
Roughly 10-20 minutes on a laptop CPU; faster with a CUDA GPU.

Re-run anytime you want to tune --epochs / --batch / --lr.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, random_split
from torchvision import datasets, models, transforms


HERE = Path(__file__).parent
DATA_DIR = HERE / "dataset"
ONNX_PATH = HERE / "crack_classifier.onnx"
LABELS_PATH = HERE / "crack_classifier_labels.txt"

INPUT_SIZE = 224
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]


def make_loaders(batch_size: int, num_workers: int):
    train_tf = transforms.Compose([
        transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
        transforms.RandomHorizontalFlip(),
        transforms.RandomVerticalFlip(),
        transforms.RandomRotation(15),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])
    val_tf = transforms.Compose([
        transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])

    full = datasets.ImageFolder(str(DATA_DIR), transform=train_tf)
    print(f"Found {len(full)} images, classes: {full.classes}")

    n_train = int(0.85 * len(full))
    train_ds, val_ds = random_split(full, [n_train, len(full) - n_train])
    # Use the eval-only transforms for the val split
    val_ds.dataset = datasets.ImageFolder(str(DATA_DIR), transform=val_tf)

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True,
                              num_workers=num_workers, pin_memory=False)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False,
                            num_workers=num_workers, pin_memory=False)
    return train_loader, val_loader, full.classes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--workers", type=int, default=0,
                        help="DataLoader workers (keep at 0 on Windows for stability)")
    args = parser.parse_args()

    if not DATA_DIR.exists() or not any(DATA_DIR.iterdir()):
        print(f"Dataset not found at {DATA_DIR}", file=sys.stderr)
        print("Download from https://www.kaggle.com/datasets/arunrk7/surface-crack-detection",
              file=sys.stderr)
        print("and unzip so you have server/dataset/Positive/ and server/dataset/Negative/",
              file=sys.stderr)
        return 1

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    train_loader, val_loader, classes = make_loaders(args.batch, args.workers)

    model = models.mobilenet_v3_small(weights=models.MobileNet_V3_Small_Weights.IMAGENET1K_V1)
    # Replace the final classification head with a 2-class output
    in_feat = model.classifier[-1].in_features
    model.classifier[-1] = nn.Linear(in_feat, len(classes))
    model = model.to(device)

    optimizer = optim.AdamW(model.parameters(), lr=args.lr)
    criterion = nn.CrossEntropyLoss()

    for epoch in range(1, args.epochs + 1):
        model.train()
        train_loss = 0.0
        correct = total = 0
        for i, (x, y) in enumerate(train_loader):
            x, y = x.to(device), y.to(device)
            optimizer.zero_grad()
            out = model(x)
            loss = criterion(out, y)
            loss.backward()
            optimizer.step()

            train_loss += loss.item()
            correct += (out.argmax(1) == y).sum().item()
            total += y.size(0)
            if i % 50 == 0:
                print(f"  epoch {epoch}  batch {i}/{len(train_loader)}  "
                      f"loss {loss.item():.4f}  acc {100*correct/total:.1f}%")

        # Validation
        model.eval()
        v_correct = v_total = 0
        with torch.no_grad():
            for x, y in val_loader:
                x, y = x.to(device), y.to(device)
                out = model(x)
                v_correct += (out.argmax(1) == y).sum().item()
                v_total += y.size(0)
        print(f"== epoch {epoch}  train acc {100*correct/total:.2f}%  "
              f"val acc {100*v_correct/v_total:.2f}% ==")

    # Export to ONNX
    model.eval()
    dummy = torch.randn(1, 3, INPUT_SIZE, INPUT_SIZE, device=device)
    torch.onnx.export(
        model, dummy, str(ONNX_PATH),
        input_names=["input"], output_names=["logits"],
        opset_version=11,
        dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
    )
    LABELS_PATH.write_text("\n".join(classes), encoding="utf-8")

    print(f"\nSaved ONNX model: {ONNX_PATH}  ({ONNX_PATH.stat().st_size/1024/1024:.1f} MB)")
    print(f"Saved labels:    {LABELS_PATH}  ({classes})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

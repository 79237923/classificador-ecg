"""Treina o classificador deep learning nas 5 superclasses do PTB-XL.

Pré-requisitos:
    pip install torch tqdm
    Baixar o dataset PTB-XL (~1,7 GB):
      https://physionet.org/content/ptb-xl/1.0.3/
    e extrair em: data/ptbxl/  (deve conter ptbxl_database.csv e records100/)

Uso:
    .venv\\Scripts\\python scripts\\train_ptbxl.py --epochs 20

Gera models/ptbxl_resnet.pt — detectado automaticamente pela API.
Avaliação: AUC macro no split oficial (strat_fold 10 = teste).
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.classification.deep_model import CLASSES, build_model  # noqa: E402
from scripts._ptbxl_path import find_ptbxl  # noqa: E402

MODEL_OUT = ROOT / "models" / "ptbxl_resnet.pt"
CLASS_CODES = [c for c, _ in CLASSES]


CACHE = ROOT / "data" / "ptbxl_cache.npz"


def load_dataset():
    """Carrega sinais + rótulos, com cache em .npz (o parse WFDB é lento)."""
    if CACHE.exists():
        print(f"usando cache {CACHE}")
        z = np.load(CACHE)
        return ((z["Xtr"], z["ytr"]), (z["Xva"], z["yva"]), (z["Xte"], z["yte"]))

    import pandas as pd
    import wfdb
    from tqdm import tqdm

    data_dir = find_ptbxl()
    db = pd.read_csv(data_dir / "ptbxl_database.csv", index_col="ecg_id")
    db.scp_codes = db.scp_codes.apply(ast.literal_eval)
    agg = pd.read_csv(data_dir / "scp_statements.csv", index_col=0)
    agg = agg[agg.diagnostic == 1]

    def to_superclass(codes: dict) -> list[str]:
        out = set()
        for code in codes:
            if code in agg.index:
                out.add(agg.loc[code].diagnostic_class)
        return sorted(out)

    db["superclasses"] = db.scp_codes.apply(to_superclass)
    db = db[db.superclasses.map(len) > 0]

    X = np.zeros((len(db), 1000, 12), dtype=np.float32)
    y = np.zeros((len(db), len(CLASS_CODES)), dtype=np.float32)
    for i, (_, row) in enumerate(tqdm(db.iterrows(), total=len(db), desc="Lendo sinais")):
        sig, _ = wfdb.rdsamp(str(data_dir / row.filename_lr))
        X[i] = sig
        for cls in row.superclasses:
            if cls in CLASS_CODES:
                y[i, CLASS_CODES.index(cls)] = 1.0

    folds = db.strat_fold.to_numpy()
    train, val, test = folds <= 8, folds == 9, folds == 10
    splits = ((X[train], y[train]), (X[val], y[val]), (X[test], y[test]))
    np.savez_compressed(CACHE,
                        Xtr=splits[0][0], ytr=splits[0][1],
                        Xva=splits[1][0], yva=splits[1][1],
                        Xte=splits[2][0], yte=splits[2][1])
    print(f"cache salvo em {CACHE}")
    return splits


def main():
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    args = ap.parse_args()

    (Xtr, ytr), (Xva, yva), (Xte, yte) = load_dataset()
    print(f"treino={len(Xtr)}  val={len(Xva)}  teste={len(Xte)}", flush=True)

    def norm(X):
        mu = X.mean(axis=1, keepdims=True)
        sd = X.std(axis=1, keepdims=True) + 1e-6
        return ((X - mu) / sd).transpose(0, 2, 1)  # (N, 12, 1000)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = build_model().to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    lossfn = torch.nn.BCEWithLogitsLoss()

    dl_tr = DataLoader(TensorDataset(torch.from_numpy(norm(Xtr)), torch.from_numpy(ytr)),
                       batch_size=args.batch, shuffle=True)

    def predict(X):
        model.eval()
        preds = []
        with torch.no_grad():
            for i in range(0, len(X), 256):
                xb = torch.from_numpy(norm(X[i:i + 256])).to(device)
                preds.append(torch.sigmoid(model(xb)).float().cpu().numpy())
        model.train()
        return np.concatenate(preds)

    def evaluate(X, y):
        from sklearn.metrics import roc_auc_score
        return roc_auc_score(y, predict(X), average="macro")

    best = 0.0
    for epoch in range(args.epochs):
        total = 0.0
        for xb, yb in dl_tr:
            xb, yb = xb.to(device), yb.to(device)
            opt.zero_grad()
            loss = lossfn(model(xb), yb)
            loss.backward()
            opt.step()
            total += loss.item() * len(xb)
        sched.step()
        auc = evaluate(Xva, yva)
        print(f"época {epoch + 1}/{args.epochs}  loss={total / len(Xtr):.4f}  AUC_val={auc:.4f}",
              flush=True)
        if auc > best:
            best = auc
            MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
            torch.save(model.state_dict(), MODEL_OUT)
            print(f"  → modelo salvo em {MODEL_OUT}", flush=True)

    from sklearn.metrics import roc_auc_score
    model.load_state_dict(torch.load(MODEL_OUT, weights_only=True))
    probs = predict(Xte)
    print(f"\nTREINO_CONCLUIDO AUC macro no teste (fold 10): "
          f"{roc_auc_score(yte, probs, average='macro'):.4f}", flush=True)
    for i, (code, label) in enumerate(CLASSES):
        print(f"  {code:5s} AUC={roc_auc_score(yte[:, i], probs[:, i]):.4f}  "
              f"(prevalência {yte[:, i].mean() * 100:.1f}%) — {label}", flush=True)


if __name__ == "__main__":
    main()

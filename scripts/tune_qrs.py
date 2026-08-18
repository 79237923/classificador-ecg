"""Calibra o limiar da delimitação de QRS por velocidade espacial.

Varre valores de `fraction` e mede, em ECGs reais do PTB-XL:
  - NORM : quanto do QRS cai em 60–110 ms (deve ser alto) e o PR em 120–200 ms
  - CLBBB/CRBBB (bloqueios de ramo): quanto do QRS fica > 120 ms (deve ser alto)

O limiar bom maximiza os dois ao mesmo tempo — ou seja, mede o normal como
normal E ainda detecta o alargamento patológico (poder discriminante).

Uso: .venv\\Scripts\\python scripts\\tune_qrs.py --n 80
"""
from __future__ import annotations

import argparse
import ast
import sys
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.processing.qrs_bounds import qrs_bounds  # noqa: E402
from scripts._ptbxl_path import find_ptbxl  # noqa: E402

DATA_DIR = find_ptbxl()
FRACTIONS = [0.05, 0.08, 0.10, 0.13, 0.16, 0.20, 0.25]


def load(row):
    import wfdb
    sig, meta = wfdb.rdsamp(str(DATA_DIR / row.filename_hr))
    return np.asarray(sig, float), float(meta["fs"]), [str(s) for s in meta["sig_name"]]


def beats(sig, names, fs):
    import neurokit2 as nk
    lead = sig[:, names.index("II")] if "II" in names else sig[:, 1]
    cleaned = nk.ecg_clean(lead, sampling_rate=fs)
    _, info = nk.ecg_peaks(cleaned, sampling_rate=fs)
    rp = np.asarray(info.get("ECG_R_Peaks", []), dtype=int)
    return cleaned, rp[(rp > 0) & (rp < len(cleaned))]


def med_width(sig, rp, fs, frac):
    on, off = qrs_bounds(sig, rp, fs, fraction=frac)
    w = (off - on) / fs * 1000.0
    w = w[np.isfinite(w)]
    return float(np.median(w)) if len(w) else None


def main():
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=80)
    args = ap.parse_args()

    db = pd.read_csv(DATA_DIR / "ptbxl_database.csv", index_col="ecg_id")
    db.scp_codes = db.scp_codes.apply(ast.literal_eval)
    rng = np.random.default_rng(0)

    norm = db[db.scp_codes.apply(lambda c: c.get("NORM", 0) >= 100)]
    norm = norm.iloc[rng.permutation(len(norm))[: args.n]]
    bbb = db[db.scp_codes.apply(lambda c: "CLBBB" in c or "CRBBB" in c)]
    bbb = bbb.iloc[rng.permutation(len(bbb))[: args.n]]

    print(f"NORM n={len(norm)}   BRE/BRD n={len(bbb)}\n")

    cache = {}
    for tag, subset in (("norm", norm), ("bbb", bbb)):
        for idx, row in subset.iterrows():
            try:
                sig, fs, names = load(row)
                cleaned, rp = beats(sig, names, fs)
                if len(rp) >= 3:
                    cache[(tag, idx)] = (sig, rp, fs)
            except Exception:
                continue

    print(f"{'frac':>6} {'QRS_norm':>9} {'norm 60-110':>12} "
          f"{'QRS_bbb':>9} {'bbb >120':>10} {'separação':>11}")
    for frac in FRACTIONS:
        wn = [w for k, (s, r, f) in cache.items() if k[0] == "norm"
              and (w := med_width(s, r, f, frac)) is not None]
        wb = [w for k, (s, r, f) in cache.items() if k[0] == "bbb"
              and (w := med_width(s, r, f, frac)) is not None]
        if not wn or not wb:
            continue
        wn, wb = np.array(wn), np.array(wb)
        ok_n = np.mean((wn >= 60) & (wn <= 110)) * 100
        ok_b = np.mean(wb > 120) * 100
        print(f"{frac:6.2f} {np.median(wn):9.1f} {ok_n:11.1f}% "
              f"{np.median(wb):9.1f} {ok_b:9.1f}% {ok_n + ok_b:10.1f}")


if __name__ == "__main__":
    main()

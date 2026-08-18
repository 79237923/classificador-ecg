"""Diagnostica a superestimação de QRS e escolhe o método de delineação.

Compara os métodos de delineação do NeuroKit2 (dwt, peak, cwt) em ECGs reais
rotulados NORM do PTB-XL, medindo quanto de cada intervalo cai na faixa
fisiológica. Também audita os nomes de derivação do dataset (para detectar
falha de correspondência de maiúsculas/minúsculas no cálculo do eixo).

Uso: .venv\\Scripts\\python scripts\\calibrate_delineation.py --n 60
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

from scripts._ptbxl_path import find_ptbxl  # noqa: E402

DATA_DIR = find_ptbxl()
METHODS = ["dwt", "peak", "cwt"]


def pct_in(vals: list[float], lo: float, hi: float) -> tuple[float, float, int]:
    v = np.asarray([x for x in vals if x is not None and np.isfinite(x)], dtype=float)
    if not len(v):
        return float("nan"), float("nan"), 0
    return float(np.median(v)), float(np.mean((v >= lo) & (v <= hi)) * 100), len(v)


def median_interval(onsets, offsets, fs, lo, hi):
    vals = []
    for a, b in zip(onsets, offsets):
        if a is None or b is None or np.isnan(a) or np.isnan(b):
            continue
        ms = (b - a) / fs * 1000.0
        if lo <= ms <= hi:
            vals.append(ms)
    return float(np.median(vals)) if vals else None


def main():
    import neurokit2 as nk
    import pandas as pd
    import wfdb

    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    args = ap.parse_args()

    db = pd.read_csv(DATA_DIR / "ptbxl_database.csv", index_col="ecg_id")
    db.scp_codes = db.scp_codes.apply(ast.literal_eval)
    norm = db[db.scp_codes.apply(lambda c: c.get("NORM", 0) >= 100)]
    rng = np.random.default_rng(0)
    norm = norm.iloc[rng.permutation(len(norm))[: args.n]]

    # ---- 1. Nomes de derivação reais do dataset ----
    sig, meta = wfdb.rdsamp(str(DATA_DIR / norm.iloc[0].filename_hr))
    print(f"== Derivações no PTB-XL ==\n  {meta['sig_name']}")
    print(f"  'aVF' presente: {'aVF' in meta['sig_name']}   "
          f"'AVF' presente: {'AVF' in meta['sig_name']}\n")

    # ---- 2. Comparação de métodos de delineação ----
    acc = {m: {"pr": [], "qrs": [], "qt": []} for m in METHODS}
    falhas = {m: 0 for m in METHODS}

    for _, row in norm.iterrows():
        sig, meta = wfdb.rdsamp(str(DATA_DIR / row.filename_hr))
        fs = float(meta["fs"])
        names = [str(s) for s in meta["sig_name"]]
        lead = sig[:, names.index("II")] if "II" in names else sig[:, 1]

        cleaned = nk.ecg_clean(lead, sampling_rate=fs)
        _, info = nk.ecg_peaks(cleaned, sampling_rate=fs)
        rp = np.asarray(info.get("ECG_R_Peaks", []), dtype=int)
        if len(rp) < 3:
            continue

        for m in METHODS:
            try:
                _, w = nk.ecg_delineate(cleaned, rp, sampling_rate=fs, method=m)
            except Exception:
                falhas[m] += 1
                continue
            p_on = np.asarray(w.get("ECG_P_Onsets", []), dtype=float)
            q_on = np.asarray(w.get("ECG_R_Onsets", []), dtype=float)
            q_off = np.asarray(w.get("ECG_R_Offsets", []), dtype=float)
            t_off = np.asarray(w.get("ECG_T_Offsets", []), dtype=float)
            acc[m]["pr"].append(median_interval(p_on, q_on, fs, 60, 400))
            acc[m]["qrs"].append(median_interval(q_on, q_off, fs, 40, 250))
            acc[m]["qt"].append(median_interval(q_on, t_off, fs, 200, 700))

    print(f"== Métodos de delineação em {len(norm)} ECGs NORM reais ==")
    print("  (esperado: PR 120–200 ms | QRS 60–110 ms | QT 320–450 ms)\n")
    for m in METHODS:
        pr_med, pr_ok, n1 = pct_in(acc[m]["pr"], 120, 200)
        qrs_med, qrs_ok, n2 = pct_in(acc[m]["qrs"], 60, 110)
        qt_med, qt_ok, n3 = pct_in(acc[m]["qt"], 320, 450)
        print(f"  {m:5s} falhas={falhas[m]:3d}")
        print(f"        PR  mediana={pr_med:6.1f} ms  dentro={pr_ok:5.1f}%  (n={n1})")
        print(f"        QRS mediana={qrs_med:6.1f} ms  dentro={qrs_ok:5.1f}%  (n={n2})")
        print(f"        QT  mediana={qt_med:6.1f} ms  dentro={qt_ok:5.1f}%  (n={n3})")


if __name__ == "__main__":
    main()

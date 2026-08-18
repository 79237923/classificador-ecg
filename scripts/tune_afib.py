"""Calibra a regra de fibrilação atrial em ECGs reais do PTB-XL.

A regra original exigia irregularidade dos RR **e** ausência de onda P, e
alcançou apenas 2% de sensibilidade: a delineação "encontra" ondas P mesmo na
FA, bloqueando a regra.

Este script mede as distribuições de rr_cv, rmssd_ratio e p_wave_ratio em
ECGs rotulados AFIB vs NORM, e avalia regras candidatas por
sensibilidade/especificidade.

Uso: .venv\\Scripts\\python scripts\\tune_afib.py --n 150
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

from backend.app.ingestion.loaders import ECGRecord  # noqa: E402
from backend.app.processing.analysis import analyze  # noqa: E402
from scripts._ptbxl_path import find_ptbxl  # noqa: E402

DATA_DIR = find_ptbxl()


def load_record(row) -> ECGRecord:
    import wfdb
    sig, meta = wfdb.rdsamp(str(DATA_DIR / row.filename_hr))
    return ECGRecord(signal=np.asarray(sig, float), sampling_rate=float(meta["fs"]),
                     lead_names=[str(s) for s in meta["sig_name"]], source_format="wfdb")


def describe(name: str, vals: list[float]):
    v = np.asarray([x for x in vals if x is not None and np.isfinite(x)], float)
    if not len(v):
        print(f"    {name:14s} sem dados")
        return
    print(f"    {name:14s} p10={np.percentile(v, 10):5.3f}  mediana={np.median(v):5.3f}  "
          f"p90={np.percentile(v, 90):5.3f}  (n={len(v)})")


def collect(subset, tag):
    out = []
    for _, row in subset.iterrows():
        try:
            a = analyze(load_record(row))
        except Exception:
            continue
        if a.rr_cv is None:
            continue
        out.append({"rr_cv": a.rr_cv, "rmssd": a.rmssd_ratio,
                    "p_ratio": a.p_wave_ratio, "tag": tag})
    return out


def main():
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    args = ap.parse_args()

    db = pd.read_csv(DATA_DIR / "ptbxl_database.csv", index_col="ecg_id")
    db.scp_codes = db.scp_codes.apply(ast.literal_eval)
    rng = np.random.default_rng(0)

    afib = db[db.scp_codes.apply(lambda c: "AFIB" in c)]
    afib = afib.iloc[rng.permutation(len(afib))[: args.n]]
    norm = db[db.scp_codes.apply(lambda c: c.get("NORM", 0) >= 100)]
    norm = norm.iloc[rng.permutation(len(norm))[: args.n]]

    A = collect(afib, "afib")
    N = collect(norm, "norm")
    print(f"\n== Distribuições (AFIB n={len(A)} | NORM n={len(N)}) ==")
    for tag, data in (("AFIB", A), ("NORM", N)):
        print(f"  {tag}:")
        describe("rr_cv", [d["rr_cv"] for d in data])
        describe("rmssd_ratio", [d["rmssd"] for d in data])
        describe("p_wave_ratio", [d["p_ratio"] for d in data])

    def rule_cv(d, t):
        return d["rr_cv"] is not None and d["rr_cv"] > t

    def rule_rmssd(d, t):
        return d["rmssd"] is not None and d["rmssd"] > t

    def rule_both(d, tcv, trm):
        return rule_cv(d, tcv) and rule_rmssd(d, trm)

    print("\n== Regras candidatas (sens = detecta FA | espec = não alarma em normal) ==")
    print(f"  {'regra':<34} {'sens':>7} {'espec':>8}")

    cands = []
    for t in (0.10, 0.12, 0.15, 0.18, 0.20):
        cands.append((f"rr_cv > {t}", lambda d, t=t: rule_cv(d, t)))
    for t in (0.10, 0.15, 0.20, 0.25, 0.30):
        cands.append((f"rmssd_ratio > {t}", lambda d, t=t: rule_rmssd(d, t)))
    for tcv, trm in ((0.12, 0.15), (0.15, 0.20), (0.15, 0.15), (0.12, 0.20), (0.18, 0.25)):
        cands.append((f"rr_cv>{tcv} e rmssd>{trm}",
                      lambda d, a=tcv, b=trm: rule_both(d, a, b)))

    for label, fn in cands:
        sens = np.mean([fn(d) for d in A]) * 100 if A else 0
        spec = (1 - np.mean([fn(d) for d in N])) * 100 if N else 0
        print(f"  {label:<34} {sens:6.1f}% {spec:7.1f}%")


if __name__ == "__main__":
    main()

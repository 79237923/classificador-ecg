"""Valida o pipeline de medidas com ECGs reais do PTB-XL (500 Hz).

Compara as medidas automatizadas em registros rotulados como NORM (ECG normal)
com as faixas fisiológicas esperadas, e testa a sensibilidade da regra de
possível FA nos registros rotulados AFIB.

Uso: .venv\\Scripts\\python scripts\\validate_measurements.py [--n 100]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.classification.rules import classify  # noqa: E402
from backend.app.ingestion.loaders import ECGRecord  # noqa: E402
from backend.app.processing.analysis import analyze  # noqa: E402
from scripts._ptbxl_path import find_ptbxl  # noqa: E402

DATA_DIR = find_ptbxl()


def load_record(row) -> ECGRecord:
    import wfdb
    sig, meta = wfdb.rdsamp(str(DATA_DIR / row.filename_hr))
    return ECGRecord(signal=np.asarray(sig, dtype=float),
                     sampling_rate=float(meta["fs"]),
                     lead_names=[str(s) for s in meta["sig_name"]],
                     source_format="wfdb")


def stats(name: str, vals: list[float], lo: float, hi: float):
    v = np.asarray([x for x in vals if x is not None], dtype=float)
    if not len(v):
        print(f"  {name:14s} sem medidas")
        return
    inside = np.mean((v >= lo) & (v <= hi)) * 100
    print(f"  {name:14s} mediana={np.median(v):6.1f}  p5–p95=[{np.percentile(v, 5):6.1f}, "
          f"{np.percentile(v, 95):6.1f}]  dentro de [{lo:g},{hi:g}]: {inside:.0f}%  (n={len(v)})")


def main():
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100, help="registros por grupo")
    args = ap.parse_args()

    db = pd.read_csv(DATA_DIR / "ptbxl_database.csv", index_col="ecg_id")
    db.scp_codes = db.scp_codes.apply(ast.literal_eval)

    rng = np.random.default_rng(0)

    # ---- Grupo 1: NORM — medidas devem cair nas faixas fisiológicas ----
    norm = db[db.scp_codes.apply(lambda c: c.get("NORM", 0) >= 100)]
    norm = norm.iloc[rng.permutation(len(norm))[: args.n]]
    res = {"hr": [], "pr": [], "qrs": [], "qt": [], "qtcf": [], "axis": []}
    fp = n_norm = 0
    for _, row in norm.iterrows():
        try:
            a = analyze(load_record(row))
        except Exception:
            continue
        res["hr"].append(a.heart_rate_bpm)
        res["pr"].append(a.pr_ms)
        res["qrs"].append(a.qrs_ms)
        res["qt"].append(a.qt_ms)
        res["qtcf"].append(a.qtc_fridericia_ms)
        res["axis"].append(a.axis_degrees)
        # especificidade: falso "possível FA" em ECG rotulado como normal
        n_norm += 1
        if "fa_possivel" in {f.code for f in classify(a)}:
            fp += 1

    print(f"\n== NORM (n={len(norm)}): faixas fisiológicas esperadas ==")
    stats("FC (bpm)", res["hr"], 50, 100)
    stats("PR (ms)", res["pr"], 120, 200)
    stats("QRS (ms)", res["qrs"], 60, 110)
    stats("QT (ms)", res["qt"], 320, 450)
    stats("QTcF (ms)", res["qtcf"], 340, 460)
    stats("Eixo (°)", res["axis"], -30, 90)

    # ---- Grupo 2: AFIB — sensibilidade da regra de possível FA ----
    afib = db[db.scp_codes.apply(lambda c: "AFIB" in c)]
    afib = afib.iloc[rng.permutation(len(afib))[: args.n]]
    hits = irregular_only = total = 0
    for _, row in afib.iterrows():
        try:
            a = analyze(load_record(row))
        except Exception:
            continue
        total += 1
        codes = {f.code for f in classify(a)}
        if "fa_possivel" in codes:
            hits += 1
        elif "ritmo_irregular" in codes:
            irregular_only += 1

    print(f"\n== AFIB (n={total}): detecção de fibrilação atrial ==")
    print(f"  'possível FA'        : {hits}/{total} ({hits / max(total, 1) * 100:.0f}%)")
    print(f"  ao menos 'irregular' : {(hits + irregular_only)}/{total} "
          f"({(hits + irregular_only) / max(total, 1) * 100:.0f}%)")
    print(f"\n== NORM: falsos positivos de 'possível FA': {fp}/{n_norm} "
          f"({fp / max(n_norm, 1) * 100:.1f}%) ==")


if __name__ == "__main__":
    main()

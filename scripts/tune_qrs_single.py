"""Calibra o limiar do QRS para sinal de DERIVAÇÃO ÚNICA.

O limiar de 5% foi calibrado sobre a velocidade espacial das 12 derivações, onde
o QRS domina a soma. Com uma derivação só — o caso de todo sinal vindo de
imagem — a onda T também ultrapassa esse limiar e o complexo se estende até o
limite da busca.

Mede, usando apenas a derivação II de registros reais do PTB-XL:
  NORM     — quanto do QRS cai em 60–110 ms
  BRE/BRD  — quanto é detectado como > 120 ms

Uso: .venv\\Scripts\\python scripts\\tune_qrs_single.py --n 80
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
FRACOES = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]


def preparar(row):
    import neurokit2 as nk
    import wfdb
    sig, meta = wfdb.rdsamp(str(DATA_DIR / row.filename_hr))
    fs = float(meta["fs"])
    nomes = [str(s) for s in meta["sig_name"]]
    lead = np.asarray(sig, float)[:, nomes.index("II")] if "II" in nomes else None
    if lead is None:
        return None
    limpo = nk.ecg_clean(lead, sampling_rate=fs)
    _, info = nk.ecg_peaks(limpo, sampling_rate=fs)
    rp = np.asarray(info.get("ECG_R_Peaks", []), dtype=int)
    # Só a derivação II, como um sinal de coluna: é o formato vindo de imagem.
    return (lead.reshape(-1, 1), rp, fs) if len(rp) >= 3 else None


def largura(sinal, rp, fs, frac):
    on, off = qrs_bounds(sinal, rp, fs, fraction=frac)
    w = (off - on) / fs * 1000.0
    w = w[np.isfinite(w)]
    return float(np.median(w)) if len(w) else None


def main() -> int:
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

    cache = {"norm": [], "bbb": []}
    for tag, sub in (("norm", norm), ("bbb", bbb)):
        for _, row in sub.iterrows():
            try:
                p = preparar(row)
            except Exception:
                continue
            if p:
                cache[tag].append(p)

    print(f"Derivação II isolada — NORM n={len(cache['norm'])}  "
          f"BRE/BRD n={len(cache['bbb'])}")
    print("\n  fração  QRS_norm  em 60-110   medidos   QRS_bbb   >120ms   separação")
    for frac in FRACOES:
        wn = [w for s, r, f in cache["norm"] if (w := largura(s, r, f, frac)) is not None]
        wb = [w for s, r, f in cache["bbb"] if (w := largura(s, r, f, frac)) is not None]
        if not wn or not wb:
            print(f"  {frac:5.2f}  (medidas insuficientes)")
            continue
        wn, wb = np.array(wn), np.array(wb)
        ok_n = np.mean((wn >= 60) & (wn <= 110)) * 100
        ok_b = np.mean(wb > 120) * 100
        cobertura = len(wn) / max(len(cache["norm"]), 1) * 100
        print(f"  {frac:5.2f} {np.median(wn):9.1f} {ok_n:10.1f}% {cobertura:8.0f}% "
              f"{np.median(wb):9.1f} {ok_b:7.1f}% {ok_n + ok_b:10.1f}")
    return 0


if __name__ == "__main__":
    main()

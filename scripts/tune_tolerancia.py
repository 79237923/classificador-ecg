"""Calibra a tolerância de vale da delimitação do QRS.

A tolerância existe para atravessar o vale de velocidade no ápice do R. Curta
demais, a medida falha em derivação única (o caso das imagens); longa demais,
o complexo se encadeia com a onda T e a largura infla.

Avalia cada valor em três frentes:
  NORM (12 der.) — quanto do QRS cai em 60–110 ms
  BRE/BRD        — quanto é detectado como > 120 ms (sensibilidade à patologia)
  imagem real    — largura medida numa derivação única digitalizada

Uso: .venv\\Scripts\\python scripts\\tune_tolerancia.py --n 80 --imagem <arquivo>
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

from backend.app.processing import qrs_bounds as qb  # noqa: E402
from scripts._ptbxl_path import find_ptbxl  # noqa: E402

DATA_DIR = find_ptbxl()
TOLERANCIAS_MS = [8, 12, 16, 20, 25, 30]


def largura(sinal, rp, fs) -> float | None:
    on, off = qb.qrs_bounds(sinal, rp, fs)
    w = (off - on) / fs * 1000.0
    w = w[np.isfinite(w)]
    return float(np.median(w)) if len(w) else None


def preparar(row):
    import neurokit2 as nk
    import wfdb
    sig, meta = wfdb.rdsamp(str(DATA_DIR / row.filename_hr))
    fs = float(meta["fs"])
    nomes = [str(s) for s in meta["sig_name"]]
    lead = sig[:, nomes.index("II")] if "II" in nomes else sig[:, 1]
    limpo = nk.ecg_clean(lead, sampling_rate=fs)
    _, info = nk.ecg_peaks(limpo, sampling_rate=fs)
    rp = np.asarray(info.get("ECG_R_Peaks", []), dtype=int)
    return (np.asarray(sig, float), rp, fs) if len(rp) >= 3 else None


def main() -> int:
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=80)
    ap.add_argument("--imagem")
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

    img = None
    if args.imagem:
        import neurokit2 as nk

        from backend.app.ingestion.image_digitizer import digitize
        cam = Path(args.imagem)
        rec = digitize(cam.name, cam.read_bytes())
        limpo = nk.ecg_clean(rec.signal[:, 0], sampling_rate=rec.sampling_rate)
        _, info = nk.ecg_peaks(limpo, sampling_rate=rec.sampling_rate)
        img = (rec.signal, np.asarray(info["ECG_R_Peaks"], int), rec.sampling_rate)

    print(f"NORM n={len(cache['norm'])}  BRE/BRD n={len(cache['bbb'])}"
          + (f"  imagem: {Path(args.imagem).name}" if img else ""))
    print("\n  tol   QRS_norm  em 60-110   QRS_bbb   >120ms   p95_norm"
          + ("   imagem" if img else ""))

    original = qb.TOLERANCIA_VALE_S
    for tol in TOLERANCIAS_MS:
        qb.TOLERANCIA_VALE_S = tol / 1000.0
        wn = [w for s, r, f in cache["norm"] if (w := largura(s, r, f)) is not None]
        wb = [w for s, r, f in cache["bbb"] if (w := largura(s, r, f)) is not None]
        if not wn or not wb:
            continue
        wn, wb = np.array(wn), np.array(wb)
        ok_n = np.mean((wn >= 60) & (wn <= 110)) * 100
        ok_b = np.mean(wb > 120) * 100
        linha = (f"  {tol:3d}ms {np.median(wn):8.1f} {ok_n:10.1f}% "
                 f"{np.median(wb):9.1f} {ok_b:7.1f}% {np.percentile(wn, 95):9.1f}")
        if img:
            wi = largura(*img)
            linha += f"   {wi:.0f}ms" if wi else "   —"
        print(linha)
    qb.TOLERANCIA_VALE_S = original
    return 0


if __name__ == "__main__":
    main()

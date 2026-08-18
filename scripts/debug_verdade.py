"""Mede o QRS do registro de referência sem passar por imagem.

Responde se a diferença observada vem da digitalização ou é característica do
próprio registro: compara 12 derivações, derivação II isolada e o sinal que
voltou da imagem.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.processing.qrs_bounds import qrs_bounds  # noqa: E402
from scripts._ptbxl_path import find_ptbxl  # noqa: E402


def largura(sig, rp, fs):
    on, off = qrs_bounds(sig, rp, fs)
    w = (off - on) / fs * 1000.0
    w = w[np.isfinite(w)]
    return float(np.median(w)) if len(w) else None


def main() -> int:
    import neurokit2 as nk
    import pandas as pd
    import wfdb

    data = find_ptbxl()
    db = pd.read_csv(data / "ptbxl_database.csv", index_col="ecg_id")
    db.scp_codes = db.scp_codes.apply(ast.literal_eval)
    norm = db[db.scp_codes.apply(lambda c: c.get("NORM", 0) >= 100)]
    row = norm.iloc[0]
    sig, meta = wfdb.rdsamp(str(data / row.filename_hr))
    sig = np.asarray(sig, float)
    fs = float(meta["fs"])
    nomes = [str(s) for s in meta["sig_name"]]
    ii = sig[:, nomes.index("II")]

    limpo = nk.ecg_clean(ii, sampling_rate=fs)
    _, info = nk.ecg_peaks(limpo, sampling_rate=fs)
    rp = np.asarray(info["ECG_R_Peaks"], int)

    print(f"registro PTB-XL {row.name} (rotulado NORM), {len(rp)} batimentos\n")
    print(f"  12 derivações      : {largura(sig, rp, fs):.0f} ms")
    print(f"  derivação II só    : {largura(ii.reshape(-1, 1), rp, fs):.0f} ms")
    print(f"  amplitude da II    : {np.ptp(ii):.2f} mV")
    print(f"  amplitude 12 der.  : {np.ptp(sig):.2f} mV")

    # Referência independente: delineação por wavelet do NeuroKit2.
    try:
        _, w = nk.ecg_delineate(limpo, rp, sampling_rate=fs, method="dwt")
        on = np.asarray(w.get("ECG_R_Onsets", []), float)
        off = np.asarray(w.get("ECG_R_Offsets", []), float)
        vals = [(b - a) / fs * 1000 for a, b in zip(on, off)
                if np.isfinite(a) and np.isfinite(b)]
        if vals:
            print(f"  NeuroKit2 (dwt)    : {np.median(vals):.0f} ms")
    except Exception as exc:
        print(f"  NeuroKit2 (dwt)    : falhou ({exc})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

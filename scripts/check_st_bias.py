"""Verifica viés sistemático da medição de ST em registros NORM digitais.

Se a mediana do ST precordial em ECGs normais estiver perto de zero, a medição
está calibrada e uma elevação isolada é repolarização precoce real. Se houver
viés positivo sistemático, é bug de posicionamento do ponto J.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.processing.analysis import _medir_derivacao  # noqa: E402
from scripts._ptbxl_path import find_ptbxl  # noqa: E402


def main(n: int = 100) -> int:
    import pandas as pd
    import wfdb

    data = find_ptbxl()
    db = pd.read_csv(data / "ptbxl_database.csv", index_col="ecg_id")
    db.scp_codes = db.scp_codes.apply(ast.literal_eval)
    norm = db[db.scp_codes.apply(lambda c: c.get("NORM", 0) >= 100)]
    rng = np.random.default_rng(0)
    norm = norm.iloc[rng.permutation(len(norm))[:n]]

    leads_alvo = ["I", "II", "aVF", "V1", "V2", "V3", "V4", "V5", "V6"]
    acc = {l: [] for l in leads_alvo}
    for _, row in norm.iterrows():
        sig, meta = wfdb.rdsamp(str(data / row.filename_hr))
        sig = np.asarray(sig, float)
        fs = float(meta["fs"])
        nomes = [str(s).strip().lower() for s in meta["sig_name"]]
        for l in leads_alvo:
            if l.lower() in nomes:
                m = _medir_derivacao(sig[:, nomes.index(l.lower())], fs)
                if m and m["st"] is not None:
                    acc[l].append(m["st"] * 1000)

    print(f"ST em {len(norm)} registros NORM digitais (µV):")
    print(f"  {'deriv':<6} {'mediana':>8} {'p25':>7} {'p75':>7} {'≥100µV':>8}")
    for l in leads_alvo:
        v = np.array(acc[l])
        if not len(v):
            continue
        pct = np.mean(v >= 100) * 100
        print(f"  {l:<6} {np.median(v):8.0f} {np.percentile(v,25):7.0f} "
              f"{np.percentile(v,75):7.0f} {pct:7.0f}%")
    return 0


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100)

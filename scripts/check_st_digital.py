"""Confere a especificidade/sensibilidade do ST territorial no SINAL DIGITAL.

No sinal digital (não imagem) a medição de ST é precisa. Verifica:
  - NORM      : quantos disparam supra territorial (deve ser baixo — especificidade)
  - MI (IAM)  : quantos com padrão de IAM disparam algo de ST (sensibilidade)
"""
from __future__ import annotations

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


def carregar(row, data):
    import wfdb
    sig, meta = wfdb.rdsamp(str(data / row.filename_hr))
    return ECGRecord(signal=np.asarray(sig, float), sampling_rate=float(meta["fs"]),
                     lead_names=[str(s) for s in meta["sig_name"]],
                     source_format="wfdb")


def main(n: int = 80) -> int:
    import pandas as pd

    data = find_ptbxl()
    db = pd.read_csv(data / "ptbxl_database.csv", index_col="ecg_id")
    db.scp_codes = db.scp_codes.apply(ast.literal_eval)
    rng = np.random.default_rng(1)

    grupos = {
        "NORM": db[db.scp_codes.apply(lambda c: c.get("NORM", 0) >= 100)],
        "IAM/MI": db[db.scp_codes.apply(lambda c: any(
            k in c for k in ("IMI", "AMI", "ASMI", "ILMI", "LMI", "STEMI")))],
    }
    for nome, sub in grupos.items():
        sub = sub.iloc[rng.permutation(len(sub))[:n]]
        supra = depre = total = 0
        for _, row in sub.iterrows():
            try:
                a = analyze(carregar(row, data))
            except Exception:
                continue
            total += 1
            codes = [f.code for f in classify(a)]
            if any(c.startswith("st_elev_") for c in codes):
                supra += 1
            if any(c.startswith("st_depr_") for c in codes):
                depre += 1
        print(f"{nome:<8} n={total:3d}  supra territorial: {supra:3d} "
              f"({supra/max(total,1)*100:.0f}%)  infra: {depre:3d} "
              f"({depre/max(total,1)*100:.0f}%)")
    return 0


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 80)

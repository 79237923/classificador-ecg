"""Compara o ST recuperado da imagem com o ST verdadeiro do sinal digital.

Renderiza um registro do PTB-XL, digitaliza de volta e, para cada derivação,
confronta o desvio de ST medido na imagem com o medido diretamente no sinal
digital original (a verdade). Só assim dá para saber se o sinal do ST está
correto, em vez de julgar a olho.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.ingestion.image_digitizer import digitize  # noqa: E402
from backend.app.processing.analysis import _medir_derivacao  # noqa: E402
from scripts._ptbxl_path import find_ptbxl  # noqa: E402

SAMPLES = ROOT / "data" / "samples"
ORDEM = ["I", "aVR", "V1", "V4", "II", "aVL", "V2", "V5", "III", "aVF", "V3", "V6"]


def main() -> int:
    import pandas as pd
    import wfdb

    data = find_ptbxl()
    db = pd.read_csv(data / "ptbxl_database.csv", index_col="ecg_id")
    db.scp_codes = db.scp_codes.apply(ast.literal_eval)
    norm = db[db.scp_codes.apply(lambda c: c.get("NORM", 0) >= 100)]

    escolhido = None
    for _, row in norm.head(40).iterrows():
        sig, meta = wfdb.rdsamp(str(data / row.filename_hr))
        sig = np.asarray(sig, float)
        nomes = [str(s) for s in meta["sig_name"]]
        if "II" in nomes and float(np.ptp(sig[:, nomes.index("II")])) >= 1.2:
            escolhido = (sig, meta, nomes)
            break
    sig, meta, nomes = escolhido
    fs = float(meta["fs"])
    idx = {str(n).strip().lower(): i for i, n in enumerate(nomes)}

    # Verdade: ST de cada derivação medido no sinal digital completo.
    verdade = {}
    for nome in ORDEM:
        canal = sig[:, idx[nome.lower()]]
        m = _medir_derivacao(canal, fs)
        verdade[nome] = m["st"] if m else None

    from backend.app.processing.analysis import analyze
    for arquivo in ("ecg12_laudo_alta.png", "ecg12_laudo_baixa.png"):
        rec = digitize(arquivo, (SAMPLES / arquivo).read_bytes())
        # Passa pelo pipeline completo, que ancora as células nos picos da tira.
        a = analyze(rec)
        ext = dict(a.st_deviation_mv)

        print(f"\n=== {arquivo} ===")
        print(f"{'deriv':<6} {'verdade':>10} {'imagem':>10} {'erro':>9}  sinal")
        ok = inv = falta = 0
        for nome in ORDEM:
            v = verdade.get(nome)
            e = ext.get(nome)
            vs = f"{v * 1000:+.0f}µV" if v is not None else "—"
            es = f"{e * 1000:+.0f}µV" if e is not None else "—"
            if v is not None and e is not None:
                erro = f"{abs(e - v) * 1000:.0f}µV"
                if (v > 0) == (e > 0) or abs(v - e) < 0.05:
                    sinal, ok = "OK", ok + 1
                else:
                    sinal, inv = "INVERTIDO", inv + 1
            else:
                erro = "—"
                sinal = "não medido" if e is None else ""
                if e is None:
                    falta += 1
            print(f"{nome:<6} {vs:>10} {es:>10} {erro:>9}  {sinal}")
        print(f"  → {ok} OK, {inv} invertido(s), {falta} não medido(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

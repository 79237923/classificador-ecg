"""Mostra as larguras de QRS medidas em cada imagem, com e sem filtro."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.ingestion.image_digitizer import digitize  # noqa: E402
from backend.app.processing import qrs_bounds as qb  # noqa: E402


def cruas(signal, rp, fs):
    sv = qb._spatial_velocity(signal, fs)
    tol = max(2, int(qb.TOLERANCIA_VALE_S * fs))
    perto = max(2, int(qb.PICO_PROXIMO_S * fs))
    busca = int(qb.SEARCH_MS / 1000 * fs)
    out = []
    for r in rp:
        a, b = max(0, r - busca), min(len(sv), r + busca)
        thr = sv[a:b].max() * qb.ONSET_FRACTION
        ja, jb = max(a, r - perto), min(b, r + perto + 1)
        c = int(np.argmax(sv[ja:jb])) + ja
        j, ua, v = c, c, 0
        while j > a and v < tol:
            j -= 1
            if sv[j] > thr:
                ua, v = j, 0
            else:
                v += 1
        k, ub, v = c, c, 0
        while k < b - 1 and v < tol:
            k += 1
            if sv[k] > thr:
                ub, v = k, 0
            else:
                v += 1
        out.append((ub - ua) / fs * 1000)
    return out


def main(caminhos: list[str]) -> int:
    import neurokit2 as nk

    for nome in caminhos:
        p = Path(nome)
        rec = digitize(p.name, p.read_bytes())
        fs = rec.sampling_rate
        limpo = nk.ecg_clean(rec.signal[:, 0], sampling_rate=fs)
        _, info = nk.ecg_peaks(limpo, sampling_rate=fs)
        rp = np.asarray(info["ECG_R_Peaks"], int)
        on, off = qb.qrs_bounds(rec.signal, rp, fs)
        w = (off - on) / fs * 1000
        val = w[np.isfinite(w)]
        c = cruas(rec.signal, rp, fs)
        print(f"\n{p.name}")
        print(f"  picos R          : {len(rp)}")
        print(f"  larguras cruas   : mediana={np.median(c):.0f} ms  "
              f"min={min(c):.0f}  max={max(c):.0f}")
        print(f"  após plausib.    : {len(val)}/{len(rp)} aceitos"
              + (f", mediana={np.median(val):.0f} ms" if len(val) else
                 f" (faixa aceita: {qb.MIN_QRS_MS:.0f}–{qb.MAX_QRS_MS:.0f} ms)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

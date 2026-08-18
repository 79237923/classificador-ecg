"""Compara o envelope de velocidade entre imagens, em torno de um batimento."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.ingestion.image_digitizer import digitize  # noqa: E402
from backend.app.processing import qrs_bounds as qb  # noqa: E402


def main(caminhos: list[str]) -> int:
    import neurokit2 as nk

    for nome in caminhos:
        p = Path(nome)
        rec = digitize(p.name, p.read_bytes())
        fs = rec.sampling_rate
        sinal = rec.signal[:, 0]
        limpo = nk.ecg_clean(sinal, sampling_rate=fs)
        _, info = nk.ecg_peaks(limpo, sampling_rate=fs)
        rp = np.asarray(info["ECG_R_Peaks"], int)
        sv = qb._spatial_velocity(rec.signal, fs)

        r = int(rp[len(rp) // 2])          # batimento do meio
        jan = int(0.2 * fs)
        a, b = max(0, r - jan), min(len(sv), r + jan)
        trecho = sv[a:b]
        pico = trecho.max()

        # Perfil em passos de 20 ms, normalizado pelo pico local
        passo = int(0.02 * fs)
        perfil = [trecho[i:i + passo].max() / pico
                  for i in range(0, len(trecho) - passo, passo)]
        marcas = "".join("#" if v > 0.05 else ("-" if v > 0.02 else ".")
                         for v in perfil)

        print(f"\n{p.name}")
        print(f"  amplitude do sinal : {np.ptp(sinal):.2f} mV")
        print(f"  sv no pico do QRS  : {pico:.5f}")
        print(f"  sv mínima na janela: {trecho.min():.5f} "
              f"({trecho.min() / pico:.1%} do pico)")
        print(f"  perfil ±200ms (# = acima de 5% do pico, cada caractere = 20 ms):")
        print(f"    {marcas}")
        acima = np.mean(trecho > pico * 0.05) * 100
        print(f"  fração da janela acima do limiar de 5%: {acima:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

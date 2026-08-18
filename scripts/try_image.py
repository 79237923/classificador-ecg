"""Digitaliza e analisa uma imagem de ECG, mostrando o resultado no terminal.

Uso: .venv\\Scripts\\python scripts\\try_image.py <imagem> [--salvar-png saida.png]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.classification.rules import classify, summarize  # noqa: E402
from backend.app.ingestion.image_digitizer import digitize  # noqa: E402
from backend.app.processing.analysis import analyze  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("imagem")
    ap.add_argument("--salvar-png", help="grava o traçado extraído como imagem")
    args = ap.parse_args()

    caminho = Path(args.imagem)
    registro = digitize(caminho.name, caminho.read_bytes())

    print(f"Sinal extraído: {registro.signal.shape[0]} amostras a "
          f"{registro.sampling_rate:.0f} Hz = {registro.duration_s:.1f} s")
    print(f"Derivações: {', '.join(registro.lead_names)}")
    amp = float(np.nanmax(registro.signal) - np.nanmin(registro.signal))
    print(f"Amplitude: {amp:.2f} mV pico a pico\n")
    for n in registro.notes:
        print(f"  · {n}")

    a = analyze(registro)
    achados = classify(a)

    print(f"\nMEDIDAS")
    def fmt(v, s=""):
        return f"{v:.0f}{s}" if isinstance(v, (int, float)) else "—"
    print(f"  FC        : {fmt(a.heart_rate_bpm, ' bpm')}   "
          f"batimentos: {a.n_beats}")
    print(f"  RR médio  : {fmt(a.rr_mean_ms, ' ms')}   CV: "
          f"{a.rr_cv:.2f}" if a.rr_cv else "  RR médio  : —")
    print(f"  PR        : {fmt(a.pr_ms, ' ms')}")
    print(f"  QRS       : {fmt(a.qrs_ms, ' ms')}")
    print(f"  QT / QTcF : {fmt(a.qt_ms, ' ms')} / {fmt(a.qtc_fridericia_ms, ' ms')}")
    for lead, dev in a.st_deviation_mv.items():
        print(f"  ST ({lead}) : {dev * 1000:+.0f} µV")

    print(f"\nACHADOS")
    for f in achados:
        print(f"  [{f.severity.upper():9s}] {f.label}")
    print(f"\nCONCLUSÃO\n  {summarize(achados, a)}")

    if a.quality_warnings:
        print("\nAVISOS")
        for w in a.quality_warnings:
            print(f"  · {w}")

    if args.salvar_png:
        import cv2
        sinal = a.cleaned_rhythm if a.cleaned_rhythm is not None else registro.signal[:, 0]
        larg, alt = 1600, 300
        img = np.full((alt, larg, 3), 255, np.uint8)
        xs = np.linspace(0, larg - 1, len(sinal)).astype(int)
        amp = max(np.ptp(sinal), 1e-6)
        ys = (alt / 2 - (sinal - np.median(sinal)) / amp * (alt * 0.4)).astype(int)
        cv2.polylines(img, [np.column_stack([xs, ys])], False, (20, 20, 20), 1, cv2.LINE_AA)
        if a.r_peaks is not None:
            for r in a.r_peaks:
                x = int(r / len(sinal) * (larg - 1))
                cv2.line(img, (x, 0), (x, 12), (0, 0, 220), 2)
        cv2.imwrite(args.salvar_png, img)
        print(f"\nTraçado extraído salvo em {args.salvar_png} "
              "(marcas vermelhas = picos R detectados)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

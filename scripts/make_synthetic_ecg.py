"""Gera ECGs sintéticos (NeuroKit2) para testar o pipeline.

Uso:
    .venv\\Scripts\\python scripts\\make_synthetic_ecg.py

Gera em data/samples/:
    ecg_normal_12d.csv    — 12 derivações simuladas, 75 bpm
    ecg_taquicardia.csv   — derivação única, 140 bpm
    ecg_irregular.csv     — derivação única, RR irregular (simula FA)
    ecg_normal_imagem.png — traçado renderizado sobre grade (teste do digitalizador)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "samples"
FS = 500


def save_csv(path: Path, signal: np.ndarray, names: list[str]):
    header = ",".join(names)
    np.savetxt(path, signal, delimiter=",", header=header, comments="", fmt="%.5f")
    print(f"gerado: {path}")


def main():
    import neurokit2 as nk

    OUT.mkdir(parents=True, exist_ok=True)

    # 12 derivações: multileads via método simulado + derivadas escaladas
    base = nk.ecg_simulate(duration=10, sampling_rate=FS, heart_rate=75,
                           method="ecgsyn", random_state=42)
    rng = np.random.default_rng(42)
    leads = ["I", "II", "III", "aVR", "aVL", "aVF",
             "V1", "V2", "V3", "V4", "V5", "V6"]
    scales = [0.6, 1.0, 0.4, -0.7, 0.25, 0.7, -0.3, 0.5, 0.9, 1.3, 1.6, 1.4]
    sig12 = np.column_stack([
        s * base + rng.normal(0, 0.01, len(base)) for s in scales])
    save_csv(OUT / "ecg_normal_12d.csv", sig12, leads)

    taq = nk.ecg_simulate(duration=10, sampling_rate=FS, heart_rate=140,
                          random_state=7)
    save_csv(OUT / "ecg_taquicardia.csv", taq.reshape(-1, 1), ["II"])

    irr = nk.ecg_simulate(duration=10, sampling_rate=FS, heart_rate=95,
                          heart_rate_std=25, random_state=3)
    save_csv(OUT / "ecg_irregular.csv", irr.reshape(-1, 1), ["II"])

    render_image(base)


def render_image(signal: np.ndarray):
    """Renderiza o traçado em uma grade estilo papel de ECG (teste do digitalizador)."""
    import cv2

    px_mm = 8                      # 8 px por mm
    speed, gain = 25.0, 10.0       # mm/s, mm/mV
    seconds = len(signal) / FS
    w = int(seconds * speed * px_mm)
    h = 40 * px_mm
    img = np.full((h, w, 3), 255, dtype=np.uint8)

    for x in range(0, w, px_mm):
        color = (200, 190, 250) if (x // px_mm) % 5 else (140, 120, 240)
        cv2.line(img, (x, 0), (x, h), color, 1)
    for y in range(0, h, px_mm):
        color = (200, 190, 250) if (y // px_mm) % 5 else (140, 120, 240)
        cv2.line(img, (0, y), (w, y), color, 1)

    xs = (np.arange(len(signal)) / FS * speed * px_mm).astype(int)
    ys = (h / 2 - signal * gain * px_mm).astype(int)
    pts = np.column_stack([xs, ys])
    cv2.polylines(img, [pts], False, (30, 30, 30), 2, cv2.LINE_AA)

    out = OUT / "ecg_normal_imagem.png"
    cv2.imwrite(str(out), img)
    print(f"gerado: {out}")


if __name__ == "__main__":
    sys.exit(main())

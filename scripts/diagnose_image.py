"""Diagnostica passo a passo a digitalização de uma imagem de ECG.

Uso: .venv\\Scripts\\python scripts\\diagnose_image.py <caminho da imagem>
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.ingestion import image_digitizer as dig  # noqa: E402


def main(caminho: str) -> int:
    import cv2

    dados = Path(caminho).read_bytes()
    arr = np.frombuffer(dados, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    h, w = bgr.shape[:2]
    print(f"Imagem: {w} x {h} px")

    escala = dig._grid_px_per_mm(bgr)
    print(f"\nEscala estimada pela grade: {escala} px/mm")
    if escala:
        largura_mm = w / escala
        print(f"  → largura do papel implícita: {largura_mm:.0f} mm "
              f"({largura_mm / 10:.1f} cm)")
        print(f"  → duração implícita a 25 mm/s: {largura_mm / 25:.1f} s")
    esperado = w / 250.0
    print(f"\nEscala esperada se o papel tem 25 cm (10 s): {esperado:.2f} px/mm")

    resolvida, origem = dig._resolver_escala(bgr)
    print(f"\nEscala RESOLVIDA: {resolvida:.2f} px/mm por {origem}")
    print(f"  → papel de {w / resolvida / 10:.1f} cm, "
          f"{w / resolvida / 25:.1f} s")

    mask = dig._trace_mask(bgr)
    cobertura = (mask > 0).mean()
    print(f"\nMáscara do traçado: {cobertura:.1%} dos pixels")

    faixas = dig._separar_faixas(mask, resolvida)
    print(f"\nFaixas detectadas: {len(faixas)}")
    for i, (a, b) in enumerate(faixas):
        cont = dig._continuidade(mask, (a, b))
        dens = (mask[a:b, :] > 0).mean()
        print(f"  {i+1:2d}. y={a:4d}-{b:4d}  altura={b-a:3d}px "
              f"({(b-a)/resolvida:5.1f}mm)  continuidade={cont:5.1%}  "
              f"densidade={dens:.3f}")

    topo, base = dig._rhythm_band(mask, px_mm=resolvida)
    print(f"\nFaixa ESCOLHIDA: y={topo}..{base} "
          f"({base - topo} px, {(base - topo) / h:.0%} da imagem)")

    # Perfil de cobertura por linha, para ver a estrutura do laudo
    print("\nPerfil de cobertura por faixa horizontal (12 faixas):")
    linhas = (mask > 0).mean(axis=1)
    passo = h // 12
    for i in range(12):
        a, b = i * passo, min(h, (i + 1) * passo)
        media = linhas[a:b].mean()
        barra = "#" * int(media * 200)
        marca = " <-- faixa escolhida" if a <= (topo + base) // 2 < b else ""
        print(f"  y={a:4d}-{b:4d}  {media:6.3f} {barra}{marca}")

    # Colunas com traçado: revela se o traçado ocupa a largura toda
    colunas = (mask > 0).mean(axis=0)
    ocupadas = (colunas > 0.005).mean()
    print(f"\nColunas com traçado: {ocupadas:.0%} da largura")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

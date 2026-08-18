"""Examina a estrutura de colunas de um laudo de 12 derivações."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.ingestion import image_digitizer as dig  # noqa: E402


def main(caminho: str) -> int:
    import cv2

    bgr = cv2.imdecode(np.frombuffer(Path(caminho).read_bytes(), np.uint8),
                       cv2.IMREAD_COLOR)
    h, w = bgr.shape[:2]
    escala, _ = dig._resolver_escala(bgr)
    mask = dig._trace_mask(bgr)
    faixas = dig._separar_faixas(mask, escala)

    print(f"{Path(caminho).name}: {w}x{h}px, {escala:.2f} px/mm, "
          f"{len(faixas)} faixas")

    for i, (a, b) in enumerate(faixas):
        banda = mask[a:b, :]
        colunas = (banda > 0).any(axis=0)
        # Vãos horizontais: colunas sem traçado nenhum
        vaos = []
        inicio = None
        for x in range(w):
            if not colunas[x] and inicio is None:
                inicio = x
            elif colunas[x] and inicio is not None:
                if x - inicio >= max(2, int(escala)):   # vão de ao menos 1 mm
                    vaos.append((inicio, x))
                inicio = None
        if inicio is not None and w - inicio >= max(2, int(escala)):
            vaos.append((inicio, w))

        x0 = int(np.argmax(colunas))
        x1 = w - int(np.argmax(colunas[::-1]))
        print(f"\n  faixa {i+1}: y={a}-{b} ({(b-a)/escala:.0f}mm)")
        print(f"    traçado de x={x0} a x={x1} "
              f"({(x1-x0)/escala/25:.1f} s se contínuo)")
        print(f"    vãos internos (≥1mm): {len(vaos)}")
        for v0, v1 in vaos[:8]:
            print(f"      x={v0}-{v1} ({(v1-v0)/escala:.0f}mm) "
                  f"→ {(v0-x0)/(x1-x0)*100:.0f}% da largura")

        # Onde estariam as divisas se fossem 4 colunas iguais
        if x1 > x0:
            divisas = [x0 + (x1 - x0) * k / 4 for k in (1, 2, 3)]
            print(f"    divisas de 4 colunas iguais: "
                  + ", ".join(f"x={int(d)}" for d in divisas))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

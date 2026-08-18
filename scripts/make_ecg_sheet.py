"""Renderiza um laudo de 12 derivações realista a partir do PTB-XL.

Reproduz o formato clínico padrão — três linhas com quatro derivações lado a
lado mais a tira de ritmo — que é justamente o layout onde a digitalização
falhava: extrair a largura inteira de uma dessas linhas concatena quatro
derivações distintas.

Gera também uma versão em baixa resolução, para exercitar o caso em que a
grade de 1 mm ocupa poucos pixels e a calibração pela autocorrelação erra.

Uso: .venv\\Scripts\\python scripts\\make_ecg_sheet.py [--largura 1024]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts._ptbxl_path import find_ptbxl  # noqa: E402

OUT = ROOT / "data" / "samples"
COLUNAS = [["I", "aVR", "V1", "V4"],
           ["II", "aVL", "V2", "V5"],
           ["III", "aVF", "V3", "V6"]]
VELOCIDADE = 25.0   # mm/s
GANHO = 10.0        # mm/mV


def desenhar(sig: np.ndarray, nomes: list[str], fs: float,
             px_mm: float, saida: Path) -> None:
    import cv2

    # Folha padrão: 25 cm de largura (10 s) e 20 cm de altura.
    larg = int(250 * px_mm)
    alt = int(200 * px_mm)
    img = np.full((alt, larg, 3), 255, np.uint8)

    # Grade: 1 mm fina, 5 mm grossa — como no papel termosensível.
    passo = px_mm
    for i in range(int(250) + 1):
        x = int(i * passo)
        if x >= larg:
            break
        cor = (150, 130, 245) if i % 5 == 0 else (205, 195, 250)
        cv2.line(img, (x, 0), (x, alt), cor, 1)
    for i in range(int(200) + 1):
        y = int(i * passo)
        if y >= alt:
            break
        cor = (150, 130, 245) if i % 5 == 0 else (205, 195, 250)
        cv2.line(img, (0, y), (larg, y), cor, 1)

    # O PTB-XL grava AVR/AVL/AVF em maiúsculas e a convenção clínica usa
    # aVR/aVL/aVF: a busca precisa ignorar caixa, senão essas três colunas
    # saem vazias do laudo renderizado.
    idx = {str(n).strip().lower(): i for i, n in enumerate(nomes)}

    def canal_de(nome: str):
        i = idx.get(nome.strip().lower())
        return None if i is None else sig[:, i]

    def traco(canal: np.ndarray, x0: int, y0: int, amostras: slice) -> None:
        trecho = canal[amostras]
        if not len(trecho):
            return
        xs = x0 + (np.arange(len(trecho)) / fs * VELOCIDADE * px_mm).astype(int)
        ys = (y0 - trecho * GANHO * px_mm).astype(int)
        pts = np.column_stack([xs, ys])
        pts = pts[(pts[:, 0] >= 0) & (pts[:, 0] < larg)
                  & (pts[:, 1] >= 0) & (pts[:, 1] < alt)]
        if len(pts) > 1:
            cv2.polylines(img, [pts], False, (25, 25, 25), 2, cv2.LINE_AA)

    # Três linhas de quatro derivações: cada coluna mostra 2,5 s.
    por_coluna = int(2.5 * fs)
    for linha, grupo in enumerate(COLUNAS):
        y_base = int((35 + linha * 40) * px_mm)
        for col, nome in enumerate(grupo):
            canal = canal_de(nome)
            if canal is None:
                continue
            x0 = int((5 + col * 61) * px_mm)
            a = col * por_coluna
            traco(canal, x0, y_base, slice(a, a + por_coluna))
            cv2.putText(img, nome, (x0 + 4, y_base - int(12 * px_mm)),
                        cv2.FONT_HERSHEY_SIMPLEX, px_mm * 0.13, (40, 40, 40),
                        max(1, int(px_mm * 0.3)), cv2.LINE_AA)

    # Tira de ritmo: derivação II inteira, ocupando toda a largura.
    y_ritmo = int(165 * px_mm)
    if canal_de("II") is not None:
        traco(canal_de("II"), int(5 * px_mm), y_ritmo, slice(0, len(sig)))
        cv2.putText(img, "II", (int(5 * px_mm) + 4, y_ritmo - int(12 * px_mm)),
                    cv2.FONT_HERSHEY_SIMPLEX, px_mm * 0.13, (40, 40, 40),
                    max(1, int(px_mm * 0.3)), cv2.LINE_AA)

    cv2.imwrite(str(saida), img)
    print(f"gerado: {saida.name}  ({larg}x{alt} px, {px_mm:.1f} px/mm)")


def main() -> int:
    import pandas as pd
    import wfdb

    ap = argparse.ArgumentParser()
    ap.add_argument("--largura", type=int, default=1024,
                    help="largura em px da versão de baixa resolução")
    args = ap.parse_args()

    data = find_ptbxl()
    db = pd.read_csv(data / "ptbxl_database.csv", index_col="ecg_id")
    db.scp_codes = db.scp_codes.apply(ast.literal_eval)
    norm = db[db.scp_codes.apply(lambda c: c.get("NORM", 0) >= 100)]

    # Escolhe um registro com derivação II de boa amplitude. Uma tira de ritmo
    # de baixa voltagem mede mal em qualquer método — inclusive no sinal digital
    # puro — e faria o teste acusar a digitalização por um problema que é do
    # registro.
    escolhido = None
    for _, row in norm.head(40).iterrows():
        sig, meta = wfdb.rdsamp(str(data / row.filename_hr))
        sig = np.asarray(sig, float)
        nomes = [str(s) for s in meta["sig_name"]]
        if "II" not in nomes:
            continue
        amp = float(np.ptp(sig[:, nomes.index("II")]))
        if amp >= 1.2:
            escolhido = (row, sig, meta, nomes, amp)
            break
    if escolhido is None:
        raise SystemExit("Nenhum registro NORM com derivação II de amplitude "
                         "suficiente entre os primeiros avaliados.")

    row, sig, meta, nomes, amp = escolhido
    fs = float(meta["fs"])
    print(f"registro PTB-XL {row.name} — {len(sig) / fs:.0f} s, "
          f"{len(nomes)} derivações, amplitude da II = {amp:.2f} mV")

    OUT.mkdir(parents=True, exist_ok=True)
    # Alta resolução (~300 dpi) e baixa resolução (como imagem de internet).
    desenhar(sig, nomes, fs, 11.8, OUT / "ecg12_laudo_alta.png")
    desenhar(sig, nomes, fs, args.largura / 250.0, OUT / "ecg12_laudo_baixa.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())

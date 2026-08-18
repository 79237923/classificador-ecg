"""Localiza a raiz do PTB-XL, aceitando o diretório aninhado do PhysioNet.

Procura por ptbxl_database.csv em data/ptbxl/ e em data/ptbxl_raw/<qualquer>/.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def find_ptbxl() -> Path:
    candidates = [ROOT / "data" / "ptbxl"]
    raw = ROOT / "data" / "ptbxl_raw"
    if raw.exists():
        candidates += [p for p in raw.iterdir() if p.is_dir()]
        candidates.append(raw)

    for c in candidates:
        if (c / "ptbxl_database.csv").exists():
            return c

    hits = list((ROOT / "data").rglob("ptbxl_database.csv"))
    if hits:
        return hits[0].parent

    raise SystemExit(
        "PTB-XL não encontrado. Esperado ptbxl_database.csv em data/ptbxl/ "
        "ou data/ptbxl_raw/<pasta>/. Baixe em "
        "https://physionet.org/content/ptb-xl/1.0.3/")

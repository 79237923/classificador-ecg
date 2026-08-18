"""Mede o que muda no laudo quando o classificador de deep learning é desligado.

Roda o mesmo exame duas vezes — com e sem o modelo de IA — e compara medidas e
achados. Responde de forma verificável: a análise depende da IA?
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.classification import deep_model  # noqa: E402
from backend.app.classification.rules import classify, summarize  # noqa: E402
from backend.app.ingestion.image_digitizer import digitize  # noqa: E402
from backend.app.ingestion.loaders import load_digital  # noqa: E402
from backend.app.processing.analysis import analyze  # noqa: E402


def carregar(caminho: Path):
    dados = caminho.read_bytes()
    if caminho.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf"):
        return digitize(caminho.name, dados), "imagem"
    return load_digital(caminho.name, dados, sampling_rate=500.0), "digital"


def avaliar(caminho: Path) -> None:
    rec, origem = carregar(caminho)
    a = analyze(rec)
    achados = classify(a)
    dl = deep_model.predict(rec.signal, rec.sampling_rate, rec.lead_names)

    print(f"\n=== {caminho.name}  ({origem}) ===")
    def f(v, s=""):
        return f"{v:.0f}{s}" if isinstance(v, (int, float)) else "—"
    print(f"  Medidas   : FC {f(a.heart_rate_bpm)} | PR {f(a.pr_ms)} | "
          f"QRS {f(a.qrs_ms)} | QTcF {f(a.qtc_fridericia_ms)} | eixo {f(a.axis_degrees)}")
    print(f"  Achados   : {len(achados)} — " +
          "; ".join(x.label for x in achados if x.severity in ("critico", "anormal"))
          or "nenhum anormal")
    print(f"  Conclusão : {summarize(achados, a)[:100]}")
    if dl:
        print("  IA (DL)   : " +
              " | ".join(f"{p['code']} {p['probability']*100:.0f}%" for p in dl))
    else:
        print("  IA (DL)   : NÃO EXECUTADA (não contribui neste exame)")


def main() -> int:
    print("O motor clínico (medidas + critérios) e o classificador de IA são")
    print("independentes. Abaixo, o que cada exame produz e se a IA participou.\n")
    print(f"Modelo de IA presente no disco: {deep_model.MODEL_PATH.exists()}")
    print(f"deep_model.is_available()     : {deep_model.is_available()}")

    amostras = [
        ROOT / "data" / "samples" / "ecg_normal_12d.csv",
        ROOT / "data" / "samples" / "ecg12_laudo_baixa.png",
    ]
    supra = Path.home() / "Downloads" / "SUPRA2-1024x728.jpg"
    if supra.exists():
        amostras.append(supra)

    for c in amostras:
        if c.exists():
            try:
                avaliar(c)
            except Exception as exc:
                print(f"\n=== {c.name} === falhou: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

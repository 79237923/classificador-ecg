"""Verifica a conclusão narrativa do laudo em cenários variados.

O texto é determinístico: cada frase tem de remontar a um número medido. Estes
testes conferem que ele não afirma o que não foi medido, que muda conforme o
achado, e que o mesmo exame produz sempre o mesmo texto.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.classification.narrative import narrativa  # noqa: E402
from backend.app.classification.rules import classify  # noqa: E402
from backend.app.ingestion.image_digitizer import digitize  # noqa: E402
from backend.app.ingestion.loaders import load_digital  # noqa: E402
from backend.app.processing.analysis import AnalysisData, analyze  # noqa: E402

falhas: list[str] = []


def checar(cond: bool, desc: str) -> None:
    print(f"  [{'OK ' if cond else 'FALHA'}] {desc}")
    if not cond:
        falhas.append(desc)


def analisar(caminho: Path):
    dados = caminho.read_bytes()
    if caminho.suffix.lower() in (".png", ".jpg", ".jpeg"):
        rec = digitize(caminho.name, dados)
    else:
        rec = load_digital(caminho.name, dados, sampling_rate=500.0)
    a = analyze(rec)
    return a, classify(a)


def main() -> int:
    amostras = ROOT / "data" / "samples"

    print("\n1. Exames reais — o texto reflete o que foi medido")
    for nome in ("ecg_taquicardia.csv", "ecg_irregular.csv", "ecg12_laudo_baixa.png"):
        caminho = amostras / nome
        if not caminho.exists():
            continue
        a, ach = analisar(caminho)
        n = narrativa(a, ach)
        print(f"\n  --- {nome} ---")
        for s in n["secoes"]:
            print(f"    {s['titulo']}: {s['texto']}")
        print(f"    {n['conclusao']}")

        # A frequência citada tem de bater com a medida
        if a.heart_rate_bpm:
            checar(f"{a.heart_rate_bpm:.0f} bpm" in n["texto"],
                   f"{nome}: a FC medida aparece no texto")
        # Não pode afirmar intervalo que não foi medido
        if a.pr_ms is None:
            checar("PR " not in n["texto"].replace("intervalos PR", ""),
                   f"{nome}: não cita PR quando não foi medido")

    print("\n2. Determinismo — o mesmo exame produz o mesmo texto")
    a, ach = analisar(amostras / "ecg_taquicardia.csv")
    t1 = narrativa(a, ach)["texto"]
    t2 = narrativa(a, ach)["texto"]
    checar(t1 == t2, "duas execuções geram texto idêntico")

    print("\n3. Registro sem medidas — não afirma normalidade")
    vazio = AnalysisData(source_format="wfdb")
    n = narrativa(vazio, classify(vazio))
    checar("não classificável" in n["conclusao"] or "não é possível afirmar" in n["conclusao"],
           "conclusão recusa classificar exame sem medidas")
    checar("normalidade" not in n["conclusao"] or "não" in n["conclusao"].lower(),
           "não conclui normalidade sem ter medido")

    print("\n4. Origem imagem é sinalizada na conclusão")
    caminho = amostras / "ecg12_laudo_baixa.png"
    if caminho.exists():
        a, ach = analisar(caminho)
        n = narrativa(a, ach)
        tem_st = any(f.code.startswith("st_elev_") for f in ach)
        if tem_st:
            checar("imagem" in n["texto"].lower(),
                   "supra de ST vindo de imagem traz a ressalva de imprecisão")
        checar(a.source_format == "imagem", "origem registrada como imagem")

    print("\n5. Fibrilação atrial — o texto não se contradiz")
    # Sem onda P não há intervalo PR. O detector de PR ainda devolve um número
    # (mede o que encontra), e citá-lo contradiria a frase do ritmo.
    a, ach = analisar(amostras / "ecg_irregular.csv")
    n = narrativa(a, ach)
    if any(f.code == "fa_possivel" for f in ach):
        intervalos = next(s["texto"] for s in n["secoes"] if s["titulo"] == "Intervalos")
        checar("não mensurável" in intervalos,
               "declara o PR não mensurável em vez de reportar um valor")
        checar("PR 1" not in intervalos and "PR 2" not in intervalos,
               "não imprime um valor numérico de PR em fibrilação atrial")
        checar("QTc de interpretação limitada" in intervalos,
               "ressalva o QTc sob ritmo irregular")

    print("\n6. O laudo chega ao PDF")
    from backend.app.reporting.report import build_pdf  # noqa: PLC0415

    pdf = build_pdf({
        "analysis_id": "t", "created_at": "2026-01-01", "engine_version": "t",
        "source": {"filename": "x.csv", "patient": {}},
        "measurements": {"heart_rate_bpm": a.heart_rate_bpm},
        "findings": [], "summary": "", "narrativa": n, "deep_learning": None})
    checar(pdf.startswith(b"%PDF") and len(pdf) > 2000,
           "PDF é gerado com a narrativa embutida")

    print(f"\n{'TODOS OS TESTES DE NARRATIVA PASSARAM' if not falhas else f'{len(falhas)} FALHA(S): ' + '; '.join(falhas)}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

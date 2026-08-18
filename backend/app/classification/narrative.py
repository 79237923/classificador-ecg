"""Conclusão narrativa do laudo, no formato de um laudo eletrocardiográfico.

Gera texto **determinístico** a partir das medidas — não há modelo de linguagem
envolvido. Num laudo clínico o texto precisa ser reproduzível (o mesmo exame
sempre produz a mesma conclusão), auditável (cada frase remonta a um número
medido) e incapaz de afirmar um achado que não foi medido. Um gerador de
linguagem falharia nos três pontos.

Segue a estrutura convencional do laudo de ECG:
    ritmo e frequência → intervalos → eixo → repolarização → conclusão
"""
from __future__ import annotations

from ..processing.analysis import AnalysisData
from ..schemas import Finding

# Faixas de referência para adultos (AHA/ACC/HRS 2009).
PR_NORMAL = (120, 200)
QRS_NORMAL = (60, 110)


def _frase_ritmo(m: AnalysisData, codigos: set[str]) -> str:
    fc = m.heart_rate_bpm
    if fc is None:
        return "Não foi possível determinar a frequência cardíaca."

    if "fa_possivel" in codigos:
        ritmo = ("Ritmo irregularmente irregular, sem organização atrial "
                 "identificável — padrão compatível com fibrilação atrial")
    elif "ritmo_irregular" in codigos:
        ritmo = "Ritmo irregular"
    else:
        # Deliberadamente "regular", não "sinusal": a origem do ritmo depende da
        # onda P, cuja análise automática não é confiável (ver rules.py).
        ritmo = "Ritmo regular"

    if fc < 50:
        qualif = "com bradicardia acentuada"
    elif fc < 60:
        qualif = "com bradicardia leve"
    elif fc <= 100:
        qualif = "com frequência cardíaca normal"
    elif fc <= 150:
        qualif = "com taquicardia"
    else:
        qualif = "com taquicardia acentuada"

    texto = f"{ritmo}, {qualif} ({fc:.0f} bpm)"
    if m.n_beats:
        texto += f", sobre {m.n_beats} batimentos analisados"
    return texto + "."


def _frase_intervalos(m: AnalysisData, fibrilacao: bool = False) -> str:
    partes, alterados = [], []

    # Em fibrilação atrial não há onda P, logo não há intervalo PR: qualquer
    # valor medido é ruído. Relatá-lo contradiria a própria frase do ritmo.
    if fibrilacao:
        partes.append("PR não mensurável (ausência de atividade atrial organizada)")
    elif m.pr_ms is not None:
        partes.append(f"PR {m.pr_ms:.0f} ms")
        if m.pr_ms > PR_NORMAL[1]:
            alterados.append("PR prolongado (bloqueio atrioventricular de 1º grau)")
        elif m.pr_ms < PR_NORMAL[0]:
            alterados.append("PR curto")
    if m.qrs_ms is not None:
        partes.append(f"QRS {m.qrs_ms:.0f} ms")
        if m.qrs_ms > 160:
            alterados.append("QRS acentuadamente alargado")
        elif m.qrs_ms > 120:
            alterados.append("QRS alargado, indicando distúrbio de condução intraventricular")
    if m.qt_ms is not None:
        qtc = m.qtc_fridericia_ms or m.qtc_bazett_ms
        formula = "Fridericia" if m.qtc_fridericia_ms else "Bazett"
        partes.append(f"QT {m.qt_ms:.0f} ms (QTc {qtc:.0f} ms, {formula})")
        # A correção do QT pressupõe RR estável; com ritmo irregular ela varia
        # batimento a batimento, e o valor único deixa de ser confiável.
        if fibrilacao:
            alterados.append("QTc de interpretação limitada, por variação do "
                             "intervalo RR")

    if not partes:
        return ("Os intervalos PR, QRS e QT não puderam ser medidos neste "
                "registro.")

    texto = ", ".join(partes) + "."
    if alterados:
        conector = "Destacam-se" if len(alterados) > 1 else "Destaca-se"
        texto += f" {conector} " + "; ".join(alterados) + "."
    else:
        texto += " Todos dentro dos limites da normalidade."
    return texto


def _frase_eixo(m: AnalysisData, por_imagem: bool) -> str | None:
    if m.axis_degrees is None:
        return None
    ax = m.axis_degrees
    if -30 <= ax <= 90:
        desc = "eixo elétrico no plano frontal normal"
    elif ax < -90:
        desc = "desvio extremo do eixo (quadrante indeterminado)"
    elif ax < -30:
        desc = "desvio do eixo para a esquerda"
    else:
        desc = "desvio do eixo para a direita"
    texto = f"{desc.capitalize()} ({ax:.0f}°)"
    if por_imagem:
        texto += (" — medida aproximada, pois na leitura de imagem as "
                  "derivações não são simultâneas")
    return texto + "."


def _frase_repolarizacao(m: AnalysisData, achados: list[Finding],
                         por_imagem: bool) -> str | None:
    supra = [f for f in achados if f.code.startswith("st_elev_")]
    infra = [f for f in achados if f.code.startswith("st_depr_")]
    isolado = [f for f in achados if f.code == "st_isolado"]

    if not (supra or infra):
        if isolado:
            return ("Segmento ST sem padrão territorial definido; os desvios "
                    "observados são isolados e não preenchem critério de "
                    "corrente de lesão.")
        if m.st_deviation_mv:
            return "Segmento ST sem desvios significativos."
        return None

    frases = []
    for f in supra:
        parede = f.label.split("parede")[-1].strip() if "parede" in f.label else "indefinida"
        verbo = ("Observa-se possível supradesnivelamento" if por_imagem
                 else "Observa-se supradesnivelamento")
        frases.append(f"{verbo} do segmento ST em parede {parede}")
    for f in infra:
        parede = f.label.split("parede")[-1].strip() if "parede" in f.label else "indefinida"
        frases.append(f"infradesnivelamento do segmento ST em parede {parede}")

    texto = "; ".join(frases) + "."
    if supra:
        texto += (" Padrão que exige correlação clínica imediata para descartar "
                  "síndrome coronariana aguda")
        if por_imagem:
            texto += (", ressalvando que a amplitude medida a partir de imagem "
                      "é imprecisa e o traçado deve ser lido diretamente")
        texto += "."
    return texto


def _frase_conclusao(m: AnalysisData, achados: list[Finding],
                     por_imagem: bool) -> str:
    codigos = {f.code for f in achados}
    criticos = [f for f in achados if f.severity == "critico"]
    anormais = [f for f in achados if f.severity == "anormal"]
    faltando = [f for f in achados if f.code in ("medidas_incompletas", "inconclusivo")]

    if faltando and not (criticos or anormais):
        return ("CONCLUSÃO: exame não classificável — medidas essenciais não "
                "puderam ser obtidas. A ausência de achados aqui reflete falta "
                "de medida, não normalidade. Repetir o exame ou revisar o "
                "traçado manualmente.")

    if criticos:
        rotulos = "; ".join(f.label for f in criticos)
        return (f"CONCLUSÃO: exame com achados de gravidade elevada — {rotulos}. "
                "Priorizar avaliação médica.")

    if anormais:
        rotulos = "; ".join(f.label for f in anormais)
        base = f"CONCLUSÃO: exame alterado — {rotulos}."
        if por_imagem and any(f.code.startswith("st_") for f in anormais):
            base += (" Por se tratar de leitura a partir de imagem, confirmar "
                     "os achados no traçado original.")
        return base

    if faltando:
        return ("CONCLUSÃO: exame sem alterações nos parâmetros medidos, porém "
                "com medidas incompletas — não é possível afirmar normalidade.")

    return ("CONCLUSÃO: eletrocardiograma dentro dos limites da normalidade "
            "pelos critérios automatizados aplicados.")


def narrativa(m: AnalysisData, achados: list[Finding]) -> dict:
    """Monta a conclusão narrativa do laudo.

    Devolve as seções separadas (para exibição estruturada) e o texto corrido.
    """
    por_imagem = m.source_format == "imagem"
    codigos = {f.code for f in achados}

    secoes = [
        ("Ritmo e frequência", _frase_ritmo(m, codigos)),
        ("Intervalos", _frase_intervalos(m, "fa_possivel" in codigos)),
    ]
    eixo = _frase_eixo(m, por_imagem)
    if eixo:
        secoes.append(("Eixo elétrico", eixo))
    repol = _frase_repolarizacao(m, achados, por_imagem)
    if repol:
        secoes.append(("Repolarização", repol))

    conclusao = _frase_conclusao(m, achados, por_imagem)

    corrido = " ".join(texto for _, texto in secoes) + " " + conclusao
    return {
        "secoes": [{"titulo": t, "texto": x} for t, x in secoes],
        "conclusao": conclusao,
        "texto": corrido,
    }

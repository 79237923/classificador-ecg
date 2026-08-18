"""Classificação por critérios eletrocardiográficos estabelecidos.

Cada achado referencia o critério aplicado (transparência clínica). Os
limiares seguem as recomendações AHA/ACC/HRS de interpretação padronizada
do ECG (Surawicz et al., 2009) e diretrizes correlatas.

Severidades: normal | limitrofe | anormal | critico
"""
from __future__ import annotations

from ..processing.analysis import AnalysisData
from ..schemas import Finding


# Territórios coronarianos contíguos (4ª Definição Universal de IAM). O
# diagnóstico de supra de ST exige ≥2 derivações contíguas concordantes — e é
# justamente essa concordância territorial que sobrevive ao ruído de medição por
# imagem, onde a amplitude de uma derivação isolada é pouco confiável.
TERRITORIOS_ST = [
    ("anterior", "anterosseptal/anterior", ["V1", "V2", "V3", "V4"]),
    ("lateral", "lateral", ["I", "aVL", "V5", "V6"]),
    ("inferior", "inferior", ["II", "III", "aVF"]),
]
# Limiares de supra de ST por derivação, no ponto J (4ª Definição Universal de
# IAM). V2 e V3 têm limite normal mais alto — a repolarização precoce eleva o ST
# nessas derivações em pessoas saudáveis —, então exigem mais para caracterizar
# lesão. O limiar feminino de V2–V3 é menor (150 µV) que o masculino (200 µV).
ST_LIMIAR_DEPRESSAO_MV = 0.1


def _limiar_supra(lead: str, sex: str) -> float:
    if lead in ("V2", "V3"):
        return 0.15 if sex.startswith("f") else 0.20
    return 0.10


def _achados_st(m: AnalysisData, sex: str = "") -> list[Finding]:
    st = m.st_deviation_mv
    if not st:
        return []

    achados: list[Finding] = []
    por_imagem = m.source_format == "imagem"

    def maior_sequencia(leads: list[str], sinal: int) -> list[tuple[str, float]]:
        """Maior corrida de derivações anatomicamente ADJACENTES concordantes.

        Contiguidade exige adjacência na ordem anatômica, não duas quaisquer do
        território: V2 e V3 (vizinhas) formam um padrão; V1 e V4, com o meio
        discordante, são ruído e não devem disparar nada. O limiar de supra é
        por derivação (V2–V3 mais altos)."""
        melhor: list[tuple[str, float]] = []
        atual: list[tuple[str, float]] = []
        for l in leads:
            d = st.get(l)
            if d is None:
                atual = []
                continue
            if sinal > 0:
                bate = d >= _limiar_supra(l, sex)
            else:
                bate = d <= -ST_LIMIAR_DEPRESSAO_MV
            if bate:
                atual.append((l, d))
                if len(atual) > len(melhor):
                    melhor = list(atual)
            else:
                atual = []
        return melhor

    usadas: set[str] = set()
    for chave, rotulo, leads in TERRITORIOS_ST:
        elevadas = maior_sequencia(leads, +1)
        deprimidas = maior_sequencia(leads, -1)

        if len(elevadas) >= 2:
            usadas.update(l for l, _v in elevadas)
            nomes = ", ".join(l for l, _v in elevadas)
            picos = "; ".join(f"{l} {d * 1000:+.0f} µV" for l, d in elevadas)
            difer = ("Diferencial: IAM com supra de ST vs. repolarização precoce "
                     "(benigna) vs. pericardite — a distinção exige correlação "
                     "clínica e, se disponível, ECG seriado."
                     if chave == "anterior" else
                     "Diferencial: IAM com supra de ST vs. outras causas de "
                     "corrente de lesão — correlação clínica obrigatória.")
            # Origem imagem: a amplitude de ST por derivação é imprecisa (o ruído
            # de digitalização, ±100–180 µV, cria padrões territoriais falsos).
            # Surfa-se o achado como "anormal — confirmar no traçado", em vez de
            # afirmar STEMI ("critico"), que teria custo alto de falso positivo.
            # No sinal digital a medição é precisa e o achado é crítico.
            if por_imagem:
                achados.append(Finding(
                    code=f"st_elev_{chave}",
                    label=f"Possível supradesnivelamento de ST — parede {rotulo}",
                    severity="anormal",
                    criteria=f"ST elevado em derivações contíguas ({nomes}), medido por imagem",
                    detail=f"{picos}. Medição a partir de imagem é imprecisa e pode "
                           f"gerar falso padrão — LER O TRAÇADO para confirmar. Se "
                           f"real: {difer}"))
            else:
                achados.append(Finding(
                    code=f"st_elev_{chave}",
                    label=f"Supradesnivelamento de ST — parede {rotulo}",
                    severity="critico",
                    criteria=f"ST ≥ limiar por derivação em ≥2 contíguas ({nomes})",
                    detail=f"{picos}. {difer} Avaliação médica imediata."))
        if len(deprimidas) >= 2:
            usadas.update(l for l, _v in deprimidas)
            nomes = ", ".join(l for l, _v in deprimidas)
            picos = "; ".join(f"{l} {d * 1000:+.0f} µV" for l, d in deprimidas)
            achados.append(Finding(
                code=f"st_depr_{chave}",
                label=f"Infradesnivelamento de ST — parede {rotulo}",
                severity="anormal",
                criteria=f"ST ≤ −0,1 mV em ≥2 derivações contíguas ({nomes})",
                detail=f"{picos}. Considerar isquemia; correlação clínica."))

    # Derivações isoladas fora de um território: informação, não alarme. Uma só
    # derivação desviada é o achado mais provável de ser artefato de medição
    # (sobretudo por imagem), e não preenche critério de IAM.
    def desviada(l: str, d: float) -> bool:
        return d >= _limiar_supra(l, sex) or d <= -ST_LIMIAR_DEPRESSAO_MV
    isoladas = [(l, d) for l, d in st.items()
                if desviada(l, d) and l not in usadas]
    if isoladas:
        origem = ("medição por imagem, derivação isolada — provável imprecisão"
                  if por_imagem else "derivação isolada — não preenche critério territorial")
        picos = "; ".join(f"{l} {d * 1000:+.0f} µV" for l, d in isoladas)
        achados.append(Finding(
            code="st_isolado", label="Desvio de ST isolado (sem padrão territorial)",
            severity="limitrofe",
            criteria="Desvio ≥ 0,1 mV em derivação única, sem contiguidade",
            detail=f"{picos}. {origem}. Confirmar no traçado; isoladamente não "
                   "caracteriza síndrome coronariana."))
    return achados


def classify(m: AnalysisData, age: int | None = None,
             sex: str | None = None) -> list[Finding]:
    f: list[Finding] = []
    sex = (sex or "").lower()

    # ---------- Ritmo e frequência ----------
    hr = m.heart_rate_bpm
    if hr is not None:
        if hr < 40:
            f.append(Finding(code="brady_severa", label="Bradicardia acentuada",
                             severity="critico", criteria="FC < 40 bpm",
                             detail=f"FC média {hr:.0f} bpm."))
        elif hr < 50:
            f.append(Finding(code="bradicardia", label="Bradicardia",
                             severity="anormal", criteria="FC < 50 bpm",
                             detail=f"FC média {hr:.0f} bpm."))
        elif hr < 60:
            f.append(Finding(code="bradicardia_leve", label="Bradicardia leve",
                             severity="limitrofe", criteria="FC entre 50 e 59 bpm",
                             detail=f"FC média {hr:.0f} bpm. Pode ser fisiológica "
                                    "(atletas, sono, uso de betabloqueador)."))
        elif hr <= 100:
            f.append(Finding(code="fc_normal", label="Frequência cardíaca normal",
                             severity="normal", criteria="60–100 bpm",
                             detail=f"FC média {hr:.0f} bpm."))
        elif hr <= 150:
            f.append(Finding(code="taquicardia", label="Taquicardia",
                             severity="anormal", criteria="FC > 100 bpm",
                             detail=f"FC média {hr:.0f} bpm."))
        else:
            f.append(Finding(code="taqui_severa", label="Taquicardia acentuada",
                             severity="critico", criteria="FC > 150 bpm",
                             detail=f"FC média {hr:.0f} bpm — correlacionar com clínica com urgência."))

    # Irregularidade do ritmo: dois índices independentes, calibrados em 149 ECGs
    # com fibrilação atrial e 150 normais do PTB-XL (scripts/tune_afib.py).
    # A ausência de onda P NÃO é usada como critério: a delineação automática
    # detecta ondas P em ~100% dos casos mesmo na FA (mediana de p_wave_ratio =
    # 1,00 nos dois grupos), o que tornava a regra praticamente inerte (2% de
    # sensibilidade). O RMSSD normalizado separa os grupos com clareza
    # (mediana 0,30 na FA contra 0,03 no ritmo sinusal).
    cv_alto = m.rr_cv is not None and m.rr_cv > 0.12
    rmssd_alto = m.rmssd_ratio is not None and m.rmssd_ratio > 0.15

    if cv_alto and rmssd_alto:
        f.append(Finding(
            code="fa_possivel", label="Possível fibrilação atrial",
            severity="critico",
            criteria="RR irregularmente irregular: CV > 0,12 e RMSSD/RR > 0,15 "
                     "(sensibilidade 89%, especificidade 96% no PTB-XL)",
            detail=f"CV dos RR = {m.rr_cv:.2f}; RMSSD/RR = {m.rmssd_ratio:.2f}. "
                   "Outras arritmias com RR irregular (flutter de condução "
                   "variável, extrassistolia frequente, taquicardia atrial "
                   "multifocal) produzem o mesmo padrão. Confirmação médica "
                   "é obrigatória."))
    elif cv_alto or rmssd_alto:
        f.append(Finding(code="ritmo_irregular", label="Ritmo irregular",
                         severity="anormal",
                         criteria="Variabilidade dos intervalos RR acima do esperado "
                                  "para ritmo sinusal (CV > 0,12 ou RMSSD/RR > 0,15)",
                         detail=f"CV dos RR = {m.rr_cv:.2f}" +
                                (f"; RMSSD/RR = {m.rmssd_ratio:.2f}" if m.rmssd_ratio else "") +
                                ". Considerar arritmia sinusal, extrassístoles "
                                "ou fibrilação atrial."))
    elif m.rr_cv is not None and hr is not None and 50 <= hr <= 100:
        # Deliberadamente "ritmo regular", NÃO "ritmo sinusal": a origem sinusal
        # exige onda P confiável, e a detecção automática de onda P mostrou-se
        # não discriminante (p_wave_ratio mediano = 1,00 tanto na FA quanto no
        # ritmo sinusal). Flutter atrial com condução fixa e ritmo juncional
        # produzem RR regular sem origem sinusal — afirmar "sinusal" aqui seria
        # uma conclusão não sustentada pelo dado.
        f.append(Finding(code="ritmo_regular", label="Ritmo regular",
                         severity="normal",
                         criteria="RR regular (CV ≤ 0,12) com FC entre 50 e 100 bpm",
                         detail="A origem do ritmo (sinusal, juncional, flutter com "
                                "condução fixa) não é determinada pelo algoritmo: a "
                                "análise automática da onda P não é confiável. "
                                "Confirmar a origem na leitura médica do traçado."))

    # ---------- Condução ----------
    if m.pr_ms is not None:
        if m.pr_ms > 200:
            f.append(Finding(code="bav1", label="Bloqueio AV de 1º grau",
                             severity="anormal", criteria="PR > 200 ms",
                             detail=f"PR = {m.pr_ms:.0f} ms."))
        elif m.pr_ms < 120 and m.p_wave_present:
            f.append(Finding(code="pr_curto", label="Intervalo PR curto",
                             severity="anormal", criteria="PR < 120 ms",
                             detail=f"PR = {m.pr_ms:.0f} ms. Considerar pré-excitação "
                                    "(avaliar onda delta)."))

    if m.qrs_ms is not None and m.qrs_ms > 160:
        f.append(Finding(
            code="qrs_muito_largo", label="QRS acentuadamente alargado",
            severity="critico", criteria="QRS > 160 ms",
            detail=f"QRS = {m.qrs_ms:.0f} ms. Alargamento extremo sugere ritmo de "
                   "origem ventricular, hipercalemia grave ou intoxicação por "
                   "bloqueador de canal de sódio — avaliação médica imediata."))
    elif m.qrs_ms is not None and m.qrs_ms > 120:
        f.append(Finding(code="qrs_largo", label="QRS alargado — distúrbio de condução intraventricular",
                         severity="anormal", criteria="QRS > 120 ms",
                         detail=f"QRS = {m.qrs_ms:.0f} ms. Morfologia (BRD/BRE) requer "
                                "análise das derivações precordiais no sinal de 12 derivações."))

    # ---------- Repolarização ----------
    qtc = m.qtc_fridericia_ms or m.qtc_bazett_ms
    if qtc is not None:
        limit = 460 if sex.startswith("f") else 450
        formula = "Fridericia" if m.qtc_fridericia_ms else "Bazett"
        if qtc > 500:
            f.append(Finding(code="qtc_muito_longo", label="QTc acentuadamente prolongado",
                             severity="critico", criteria="QTc > 500 ms",
                             detail=f"QTc ({formula}) = {qtc:.0f} ms — risco de torsades de pointes."))
        elif qtc > limit:
            f.append(Finding(code="qtc_longo", label="Intervalo QTc prolongado",
                             severity="anormal", criteria=f"QTc > {limit} ms ({'mulheres' if limit == 460 else 'homens'})",
                             detail=f"QTc ({formula}) = {qtc:.0f} ms."))
        elif qtc < 340:
            f.append(Finding(code="qtc_curto", label="Intervalo QTc curto",
                             severity="anormal", criteria="QTc < 340 ms",
                             detail=f"QTc ({formula}) = {qtc:.0f} ms."))

    f.extend(_achados_st(m, sex))

    # ---------- Eixo e voltagem (12 derivações) ----------
    # Por imagem, I e aVF vêm de colunas (janelas de tempo) diferentes do laudo,
    # não de registro simultâneo: o eixo e a voltagem ficam aproximados. Nesse
    # caso não se afirma o achado mais alarmante (eixo "extremo", tipicamente
    # artefato) — rebaixa-se a limítrofe com pedido de confirmação.
    por_imagem = m.source_format == "imagem"
    cav_ax = (" Medida aproximada: derivações não simultâneas (origem imagem)."
              if por_imagem else "")
    if m.axis_degrees is not None:
        ax = m.axis_degrees
        if -30 <= ax <= 90:
            f.append(Finding(code="eixo_normal", label="Eixo elétrico normal",
                             severity="normal", criteria="Eixo entre −30° e +90°",
                             detail=f"Eixo ≈ {ax:.0f}°.{cav_ax}"))
        elif ax < -90:
            f.append(Finding(
                code="eixo_extremo", label="Desvio extremo do eixo (eixo indeterminado)",
                severity="limitrofe" if por_imagem else "anormal",
                criteria="Eixo entre −90° e −180°",
                detail=f"Eixo ≈ {ax:.0f}°. Considerar ritmo de origem ventricular ou "
                       f"inversão de eletrodos.{cav_ax}"))
        elif ax < -30:
            f.append(Finding(code="eixo_esq", label="Desvio do eixo para a esquerda",
                             severity="anormal", criteria="Eixo entre −30° e −90°",
                             detail=f"Eixo ≈ {ax:.0f}°.{cav_ax}"))
        else:
            f.append(Finding(code="eixo_dir", label="Desvio do eixo para a direita",
                             severity="anormal", criteria="Eixo > +90°",
                             detail=f"Eixo ≈ {ax:.0f}°.{cav_ax}"))

    if m.sokolow_lyon_mv is not None and m.sokolow_lyon_mv > 3.5:
        f.append(Finding(code="hve", label="Critério de voltagem para hipertrofia ventricular esquerda",
                         severity="limitrofe" if por_imagem else "anormal",
                         criteria="Sokolow-Lyon: S(V1) + R(V5/V6) > 3,5 mV",
                         detail=f"Índice = {m.sokolow_lyon_mv:.2f} mV.{cav_ax}"))

    faltando = missing_essentials(m)
    if faltando:
        f.append(Finding(
            code="medidas_incompletas", label="Medidas incompletas",
            severity="limitrofe",
            criteria="Intervalos essenciais não puderam ser medidos",
            detail=f"Não obtidos: {', '.join(faltando)}. A ausência de achados "
                   "nestes parâmetros indica falta de medida, não normalidade — "
                   "revisar o traçado manualmente."))

    if not f:
        f.append(Finding(code="inconclusivo", label="Análise inconclusiva",
                         severity="limitrofe",
                         criteria="Medidas insuficientes para classificação",
                         detail="; ".join(m.quality_warnings) or "Sinal de qualidade insuficiente."))
    return f


#  Medidas sem as quais não se pode afirmar que um ECG é normal.
ESSENTIAL_MEASURES = (
    ("qrs_ms", "duração do QRS"),
    ("pr_ms", "intervalo PR"),
    ("qt_ms", "intervalo QT"),
)


def missing_essentials(m: AnalysisData) -> list[str]:
    return [label for attr, label in ESSENTIAL_MEASURES if getattr(m, attr, None) is None]


def summarize(findings: list[Finding], m: AnalysisData) -> str:
    order = {"critico": 0, "anormal": 1, "limitrofe": 2, "normal": 3}
    ranked = sorted(findings, key=lambda x: order.get(x.severity, 9))
    abnormal = [x for x in ranked if x.severity in ("critico", "anormal")]
    parts = []
    if m.heart_rate_bpm:
        parts.append(f"FC {m.heart_rate_bpm:.0f} bpm")

    faltando = missing_essentials(m)
    inconclusivo = any(x.code in ("inconclusivo", "medidas_incompletas")
                       for x in findings)

    if abnormal:
        parts.append("achados: " + "; ".join(x.label for x in abnormal))
        head = "ECG ALTERADO"
        if any(x.severity == "critico" for x in abnormal):
            head = "ECG COM ACHADOS CRÍTICOS — priorizar avaliação médica"
    elif inconclusivo and not faltando:
        head = ("ECG NÃO CLASSIFICÁVEL — qualidade do traçado insuficiente; "
                "repetir o exame e submeter à avaliação médica")
    elif faltando:
        # Nunca declarar normalidade sobre medidas que não foram obtidas:
        # a ausência de achado aqui reflete falta de dado, não ausência de doença.
        head = ("ANÁLISE INCOMPLETA — não é possível afirmar normalidade sem "
                + ", ".join(faltando))
    else:
        head = "ECG dentro dos limites da normalidade pelos critérios automatizados"

    texto = f"{head}. " + (", ".join(parts) + "." if parts else "")
    if faltando and abnormal:
        texto += (f" Atenção: medidas não obtidas neste registro ({', '.join(faltando)}); "
                  "a análise é parcial.")
    return texto

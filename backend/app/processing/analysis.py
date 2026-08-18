"""Processamento de sinal e medidas eletrocardiográficas.

Usa NeuroKit2 para limpeza, detecção de picos R e delineação de ondas
(P, QRS, T), e deriva as medidas clínicas padrão: FC, RR, PR, QRS, QT/QTc,
eixo elétrico e critérios de voltagem.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..ingestion.loaders import ECGRecord
from .qrs_bounds import qrs_bounds


@dataclass
class AnalysisData:
    heart_rate_bpm: float | None = None
    rr_mean_ms: float | None = None
    rr_cv: float | None = None
    rmssd_ratio: float | None = None
    pr_ms: float | None = None
    qrs_ms: float | None = None
    qt_ms: float | None = None
    qtc_bazett_ms: float | None = None
    qtc_fridericia_ms: float | None = None
    axis_degrees: float | None = None
    sokolow_lyon_mv: float | None = None
    p_wave_present: bool | None = None
    p_wave_ratio: float | None = None
    n_beats: int = 0
    duration_s: float = 0.0
    sampling_rate_hz: float = 0.0
    rhythm_lead: str = ""
    source_format: str = ""
    quality_warnings: list[str] = field(default_factory=list)
    st_deviation_mv: dict[str, float] = field(default_factory=dict)
    beats_por_derivacao: dict[str, int] = field(default_factory=dict)
    cleaned_rhythm: np.ndarray | None = None
    r_peaks: np.ndarray | None = None


MIN_BEATS_FOR_INTERVAL = 3


def _median_interval(onsets, offsets, fs: float,
                     lo: float, hi: float) -> float | None:
    """Mediana de (offset - onset) em ms, filtrando pares implausíveis.

    Exige um mínimo de batimentos válidos: uma mediana sobre um ou dois
    batimentos não é uma medida representativa e não deve alimentar uma
    classificação clínica.
    """
    vals = []
    for a, b in zip(onsets, offsets):
        if a is None or b is None or np.isnan(a) or np.isnan(b):
            continue
        ms = (b - a) / fs * 1000.0
        if lo <= ms <= hi:
            vals.append(ms)
    if len(vals) < MIN_BEATS_FOR_INTERVAL:
        return None
    return float(np.median(vals))


def _net_qrs_amplitude(signal: np.ndarray, r_peaks: np.ndarray, fs: float) -> float:
    """Amplitude líquida média do QRS (R - S) em torno dos picos, em mV.

    Ignora batimentos fora da janela da derivação: num laudo em imagem cada
    derivação cobre apenas 2,5 s, e o restante vem como NaN.
    """
    half = int(0.05 * fs)
    vals = []
    for r in r_peaks:
        a, b = max(0, r - half), min(len(signal), r + half)
        seg = signal[a:b]
        seg = seg[np.isfinite(seg)]
        if len(seg):
            vals.append(float(seg.max() + seg.min()))
    return float(np.median(vals)) if vals else 0.0


def _peak_amplitude(signal: np.ndarray, r_peaks: np.ndarray, fs: float,
                    positive: bool) -> float:
    half = int(0.06 * fs)
    vals = []
    for r in r_peaks:
        a, b = max(0, r - half), min(len(signal), r + half)
        seg = signal[a:b]
        seg = seg[np.isfinite(seg)]
        if len(seg):
            vals.append(float(seg.max()) if positive else float(-seg.min()))
    return float(np.median(vals)) if vals else 0.0


def _detectar_r(limpo: np.ndarray, fs: float) -> np.ndarray:
    """Detecta picos R numa janela curta (uma célula de 2,5 s).

    NeuroKit precisa de mais contexto e falha com frequência em 2,5 s ruidosos,
    derrubando derivações que existem no laudo. Aqui usa-se um detector direto:
    picos acima de uma fração do maior |desvio|, separados por ao menos 300 ms
    (o período refratário mínimo). É robusto porque não depende de estatística
    de longo prazo — só da forma local do complexo.
    """
    from scipy.signal import find_peaks

    base = float(np.median(limpo))
    desvio = np.abs(limpo - base)
    if desvio.max() <= 0:
        return np.asarray([], dtype=int)
    # A onda dominante pode ser positiva (R) ou negativa (QS): busca no |desvio|.
    dist = int(round(0.30 * fs))
    picos, _ = find_peaks(desvio, distance=dist, height=desvio.max() * 0.4)
    return picos.astype(int)


def _medir_derivacao(canal: np.ndarray, fs: float,
                     rhythm_peaks: np.ndarray | None = None) -> dict | None:
    """Mede uma derivação com os batimentos dela própria.

    Num laudo em imagem cada derivação cobre 2,5 s de uma janela diferente do
    exame. Usar os picos R da tira de ritmo mede o instante errado do ciclo e
    chega a inverter o sinal do ST (verificado: piora de 11/12 para 2/12 sinais
    corretos). Por isso os batimentos são sempre detectados na própria célula;
    `rhythm_peaks` é aceito só por compatibilidade e ignorado.
    """
    import neurokit2 as nk

    finito = np.isfinite(canal)
    if finito.sum() < fs * 1.0:          # menos de 1 s de traçado
        return None
    ini = int(np.argmax(finito))
    fim = len(finito) - int(np.argmax(finito[::-1]))
    trecho = canal[ini:fim]
    if not np.isfinite(trecho).all():
        trecho = np.nan_to_num(trecho, nan=float(np.nanmedian(trecho)))

    try:
        limpo = nk.ecg_clean(trecho, sampling_rate=fs)
    except Exception:
        return None

    # Detector próprio (robusto em janela curta); NeuroKit como reforço se ele
    # não achar batimentos suficientes.
    rp = _detectar_r(limpo, fs)
    if len(rp) < 2:
        try:
            _, info = nk.ecg_peaks(limpo, sampling_rate=fs)
            rp = np.asarray(info.get("ECG_R_Peaks", []), dtype=int)
        except Exception:
            return None
    rp = rp[(rp > 0) & (rp < len(limpo))]
    if len(rp) < 2:
        return None

    on, off = qrs_bounds(trecho.reshape(-1, 1), rp, fs)
    st = _st_por_derivacao(limpo, on, off, fs)
    return {"st": st, "n_batimentos": int(len(rp)),
            "net_qrs": _net_qrs_amplitude(limpo, rp, fs),
            "r_amp": _peak_amplitude(limpo, rp, fs, positive=True),
            "s_amp": _peak_amplitude(limpo, rp, fs, positive=False),
            "sinal": limpo, "r_peaks": rp}


def _st_por_derivacao(signal: np.ndarray, q_on: np.ndarray, q_off: np.ndarray,
                      fs: float) -> float | None:
    """Desvio de ST no ponto J, relativo ao segmento PR, para uma derivação.

    Mede-se no próprio ponto J (fim do QRS), como na 4ª Definição Universal de
    IAM. O deslocamento de +60 ms usado antes empurrava a medida para dentro da
    onda T, que é alta em V2–V3, criando pseudoelevação: em ECGs normais a
    mediana de ST em V2 saía em +109 µV. Uma pequena janela logo após o J,
    tomada pelo valor mais próximo de zero, evita medir sobre a subida da T.
    """
    j_delay = int(round(0.02 * fs))       # ~20 ms após o fim do QRS
    janela = max(2, int(round(0.02 * fs)))
    base_margin = int(round(0.02 * fs))
    devs = []
    for on, off in zip(q_on, q_off):
        if not (np.isfinite(on) and np.isfinite(off)):
            continue
        j = int(off) + j_delay
        b = int(on) - base_margin
        if 0 <= b < len(signal) and j + janela < len(signal):
            base = signal[b]
            trecho = signal[j:j + janela]
            if np.isfinite(base) and np.isfinite(trecho).all() and len(trecho):
                # valor do segmento ST mais próximo da linha de base na janela:
                # não superestima elevação por causa da subida da onda T.
                nivel = trecho[int(np.argmin(np.abs(trecho - base)))]
                devs.append(float(nivel - base))
    return float(np.median(devs)) if len(devs) >= 2 else None


def analyze(record: ECGRecord) -> AnalysisData:
    import neurokit2 as nk

    fs = record.sampling_rate
    out = AnalysisData(sampling_rate_hz=fs, duration_s=record.duration_s,
                       source_format=record.source_format)

    lead_name, raw = record.best_rhythm_lead()
    out.rhythm_lead = lead_name

    if record.duration_s < 5:
        out.quality_warnings.append(
            f"Registro curto ({record.duration_s:.1f} s): medidas de ritmo pouco confiáveis.")

    cleaned = nk.ecg_clean(raw, sampling_rate=fs)
    out.cleaned_rhythm = cleaned

    _, info = nk.ecg_peaks(cleaned, sampling_rate=fs)
    r_peaks = np.asarray(info.get("ECG_R_Peaks", []), dtype=int)
    r_peaks = r_peaks[(r_peaks > 0) & (r_peaks < len(cleaned))]
    out.r_peaks = r_peaks
    out.n_beats = int(len(r_peaks))
    if len(r_peaks) < 3:
        out.quality_warnings.append("Menos de 3 batimentos detectados: análise abortada.")
        return out

    rr = np.diff(r_peaks) / fs  # s
    rr = rr[(rr > 0.2) & (rr < 3.0)]
    if len(rr):
        out.rr_mean_ms = float(np.mean(rr) * 1000)
        out.heart_rate_bpm = float(60.0 / np.mean(rr))
        out.rr_cv = float(np.std(rr) / np.mean(rr))
        if len(rr) > 1:
            # RMSSD normalizado: irregularidade batimento-a-batimento, o
            # discriminante clássico de fibrilação atrial.
            rmssd = float(np.sqrt(np.mean(np.diff(rr) ** 2)))
            out.rmssd_ratio = rmssd / float(np.mean(rr))

    # QRS global por velocidade espacial (todas as derivações quando houver).
    # A delineação por derivação isolada superestimava o QRS em ~80 ms.
    # A velocidade espacial soma as derivações; só entram as que cobrem o
    # registro inteiro. Num laudo em imagem, apenas a tira de ritmo é completa —
    # as demais têm 2,5 s e NaN no restante, e contaminariam a soma.
    completas = [i for i in range(record.signal.shape[1])
                 if np.isfinite(record.signal[:, i]).all()]
    base_qrs = record.signal[:, completas] if completas else cleaned.reshape(-1, 1)

    q_on, q_off = qrs_bounds(base_qrs, r_peaks, fs)
    out.qrs_ms = _median_interval(q_on, q_off, fs, 40, 300)
    if out.qrs_ms is None:
        out.quality_warnings.append(
            "Não foi possível delimitar o complexo QRS de forma confiável; "
            "as medidas de QRS, PR e QT não estão disponíveis neste registro.")

    # Onda P e fim da onda T continuam vindo da delineação por wavelet.
    try:
        _, waves = nk.ecg_delineate(cleaned, r_peaks, sampling_rate=fs, method="dwt")
        p_on = np.asarray(waves.get("ECG_P_Onsets", []), dtype=float)
        p_pk = np.asarray(waves.get("ECG_P_Peaks", []), dtype=float)
        t_off = np.asarray(waves.get("ECG_T_Offsets", []), dtype=float)

        # PR e QT são medidos até/desde o início do QRS global, não o da wavelet.
        out.pr_ms = _median_interval(p_on, q_on, fs, 60, 400)
        out.qt_ms = _median_interval(q_on, t_off, fs, 200, 700)

        if len(p_pk):
            out.p_wave_ratio = float(np.sum(~np.isnan(p_pk)) / len(p_pk))
            out.p_wave_present = out.p_wave_ratio > 0.6
    except Exception:
        out.quality_warnings.append(
            "Delineação de ondas P/T falhou; intervalos PR e QT indisponíveis.")

    if out.qt_ms and out.rr_mean_ms:
        rr_s = out.rr_mean_ms / 1000.0
        out.qtc_bazett_ms = float(out.qt_ms / np.sqrt(rr_s))
        out.qtc_fridericia_ms = float(out.qt_ms / (rr_s ** (1 / 3)))

    # Segmento ST: desvio medido em J+60 ms, onde J é o **fim do QRS
    # delineado** deste batimento — não um deslocamento fixo a partir de R.
    # A linha de base é o segmento PR imediatamente anterior ao início do QRS,
    # também por batimento: uma referência fixa em R−80 ms cai dentro da onda P
    # em taquicardia, deslocando toda a medida de ST.
    dev_ritmo = _st_por_derivacao(cleaned, q_on, q_off, fs)
    if dev_ritmo is not None:
        out.st_deviation_mv[lead_name] = dev_ritmo
    else:
        out.quality_warnings.append(
            "Batimentos válidos insuficientes para medir o segmento ST.")

    # Medida unificada por derivação, cada uma com os batimentos dela própria.
    # É o que torna correta a análise a partir de imagem, onde cada derivação
    # cobre uma janela de 2,5 s diferente: ST, eixo e voltagem passam a usar
    # amplitudes medidas no instante certo do ciclo de cada célula, em vez dos
    # picos R da tira de ritmo (que não se alinham às outras células).
    # Chaveado pelo nome canônico em minúsculas: o PTB-XL grava 'AVF'/'V1' e a
    # convenção clínica usa 'aVF', então a busca deve ignorar a caixa.
    medidas: dict[str, dict] = {}
    for i, nome in enumerate(record.lead_names):
        if nome == lead_name:
            continue
        m = _medir_derivacao(record.signal[:, i], fs, rhythm_peaks=r_peaks)
        if m:
            medidas[str(nome).strip().lower()] = m

    canonico = {"i": "I", "ii": "II", "iii": "III", "avr": "aVR", "avl": "aVL",
                "avf": "aVF", "v1": "V1", "v2": "V2", "v3": "V3", "v4": "V4",
                "v5": "V5", "v6": "V6"}

    # ST territorial: é aqui que um infarto anterior aparece (supra em V2–V4),
    # ainda que a derivação de ritmo não mostre nada. As chaves de ST usam o
    # nome canônico para casar com os territórios em rules.py.
    for chave, m in medidas.items():
        if m["st"] is not None:
            nome = canonico.get(chave, chave)
            out.st_deviation_mv[nome] = m["st"]
            out.beats_por_derivacao[nome] = m["n_batimentos"]

    def _net(nome: str) -> float | None:
        m = medidas.get(nome)
        return m["net_qrs"] if m else None

    # Eixo elétrico no plano frontal, a partir do QRS líquido de I e aVF.
    net_i, net_f = _net("i"), _net("avf")
    if net_i is not None and net_f is not None and (abs(net_i) > 1e-3 or abs(net_f) > 1e-3):
        out.axis_degrees = float(np.degrees(np.arctan2(net_f, net_i)))

    # Índice de Sokolow-Lyon: S(V1) + maior R entre V5 e V6.
    m_v1 = medidas.get("v1")
    if m_v1 is not None and ("v5" in medidas or "v6" in medidas):
        s_v1 = m_v1["s_amp"]
        r_v5 = max(medidas["v5"]["r_amp"] if "v5" in medidas else 0.0,
                   medidas["v6"]["r_amp"] if "v6" in medidas else 0.0)
        out.sokolow_lyon_mv = float(s_v1 + r_v5)

    return out

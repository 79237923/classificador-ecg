"""Delimitação de QRS por velocidade espacial (global QRS).

Os eletrocardiógrafos clínicos não medem a largura do QRS em uma única
derivação: medem o QRS *global*, definido pelo intervalo entre o início mais
precoce e o fim mais tardio da ativação ventricular considerando todas as
derivações simultaneamente. O estimador padrão é a **velocidade espacial** —
a magnitude da derivada temporal somada entre derivações — cujo envelope
delimita o complexo.

Motivo: a delineação por derivação isolada (NeuroKit2 dwt/cwt) superestimou
sistematicamente o QRS em ECGs reais do PTB-XL (mediana 177 ms para ECGs
rotulados normais, contra 60–110 ms esperados), porque componentes iniciais e
finais de baixa amplitude em uma derivação são confundidos com o complexo.

Referência de método: Surawicz et al., AHA/ACC/HRS 2009 — recomendações para
padronização e interpretação do ECG, parte III (intervalos e medidas globais).
"""
from __future__ import annotations

import numpy as np

# Fração do pico do envelope de velocidade que define as bordas do QRS.
# Calibrado em 80 ECGs NORM + 80 com bloqueio de ramo do PTB-XL
# (scripts/tune_qrs.py): mediana 92,5 ms nos normais (88,8% em 60–110 ms) e
# 152 ms nos bloqueios (98,7% detectados como > 120 ms). Limiares maiores
# estreitam demais o complexo e perdem bloqueios de ramo — priorizamos
# sensibilidade à patologia.
ONSET_FRACTION = 0.05
# Limite superior de plausibilidade. Precisa acomodar a patologia grave —
# taquicardia ventricular, hipercalemia e bloqueios avançados produzem QRS de
# 200–300 ms. Um corte em 200 ms descartaria silenciosamente justamente os
# ECGs mais anormais, transformando o achado mais grave em "sem medida".
MAX_QRS_MS = 300.0
MIN_QRS_MS = 30.0
SEARCH_MS = 200.0

# Tolerância de vale (ver comentário no laço). Calibrada em
# scripts/tune_tolerancia.py contra ECGs normais, com bloqueio de ramo e um
# laudo real digitalizado a partir de imagem.
TOLERANCIA_VALE_S = 0.030
PICO_PROXIMO_S = 0.040

# Piso de ruído. Um limiar apenas relativo ao pico pressupõe que a velocidade
# volte a ~zero fora do complexo — verdade em sinal digital, falso em sinal
# extraído de imagem: a quantização em linhas inteiras de pixel produz um
# serrilhado que mantém a velocidade permanentemente acima de zero. Quando isso
# acontece, o limiar precisa subir junto, senão o complexo se estende até o
# limite da busca. A mediana da janela estima o nível de base porque o QRS
# ocupa uma fração pequena dela.
FATOR_RUIDO = 1.0


def _spatial_velocity(signal: np.ndarray, fs: float) -> np.ndarray:
    """Envelope de velocidade espacial, suavizado, normalizado por batimento."""
    grad = np.abs(np.gradient(signal, axis=0))
    sv = grad.sum(axis=1) if signal.ndim > 1 else grad

    win = max(3, int(round(0.012 * fs)))  # ~12 ms
    if win % 2 == 0:
        win += 1
    kernel = np.ones(win) / win
    return np.convolve(sv, kernel, mode="same")


def qrs_bounds(signal: np.ndarray, r_peaks: np.ndarray, fs: float,
               fraction: float = ONSET_FRACTION) -> tuple[np.ndarray, np.ndarray]:
    """Retorna (onsets, offsets) em amostras para cada pico R.

    `signal` pode ser (n,) para derivação única ou (n, n_leads) para 12
    derivações — neste caso a velocidade espacial usa todas.
    """
    if signal.ndim == 1:
        signal = signal.reshape(-1, 1)
    sv = _spatial_velocity(signal, fs)
    n = len(sv)

    search = int(round(SEARCH_MS / 1000.0 * fs))
    onsets = np.full(len(r_peaks), np.nan)
    offsets = np.full(len(r_peaks), np.nan)

    # Tolerância de vale: a velocidade zera no ápice do R (o sinal está no topo,
    # sem inclinação) e volta a subir na descida. Parar no primeiro ponto abaixo
    # do limiar mede largura zero — o que de fato ocorria em derivação única,
    # como no sinal vindo de imagem. Com 12 derivações o vale se preenche pela
    # soma e o defeito ficava invisível.
    tolerancia = max(2, int(round(TOLERANCIA_VALE_S * fs)))
    perto = max(2, int(round(PICO_PROXIMO_S * fs)))

    for i, r in enumerate(r_peaks):
        r = int(r)
        a, b = max(0, r - search), min(n, r + search)
        if b - a < 5:
            continue
        janela = sv[a:b]
        local_peak = janela.max()
        if local_peak <= 0:
            continue
        thr = max(local_peak * fraction,
                  float(np.median(janela)) * FATOR_RUIDO)

        # Parte do pico de VELOCIDADE próximo ao R (a inclinação do complexo),
        # não do pico de amplitude.
        ja, jb = max(a, r - perto), min(b, r + perto + 1)
        centro = int(np.argmax(sv[ja:jb])) + ja

        # Início: recua até a velocidade ficar abaixo do limiar de forma
        # sustentada, atravessando o vale do ápice.
        j = centro
        ultimo_acima = centro
        vale = 0
        while j > a and vale < tolerancia:
            j -= 1
            if sv[j] > thr:
                ultimo_acima = j
                vale = 0
            else:
                vale += 1
        onsets[i] = ultimo_acima

        # Fim: mesma lógica para frente.
        k = centro
        ultimo_acima = centro
        vale = 0
        while k < b - 1 and vale < tolerancia:
            k += 1
            if sv[k] > thr:
                ultimo_acima = k
                vale = 0
            else:
                vale += 1
        offsets[i] = ultimo_acima

    # Descarta complexos implausivelmente largos (artefato/ruído)
    width_ms = (offsets - onsets) / fs * 1000.0
    bad = ~np.isfinite(width_ms) | (width_ms > MAX_QRS_MS) | (width_ms < MIN_QRS_MS)
    onsets[bad] = np.nan
    offsets[bad] = np.nan
    return onsets, offsets

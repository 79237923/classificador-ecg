"""Digitalização de traçado de ECG a partir de imagem (JPG/PNG) ou PDF.

Pipeline (baseline):
1. PDF → renderiza a 1ª página em alta resolução (PyMuPDF).
2. Separa o traçado (escuro) da grade milimetrada (vermelha/rosa ou clara).
3. Estima a calibração px/mm pela periodicidade da grade (autocorrelação).
4. Localiza a faixa de ritmo (banda horizontal com traçado mais contínuo).
5. Extrai y(x) por centroide de pixels escuros e converte para sinal em mV,
   assumindo padrão 25 mm/s e 10 mm/mV.

Limitações (documentadas no laudo): análise de imagem cobre ritmo e
intervalos na faixa extraída; medidas completas de 12 derivações exigem
sinal digital.
"""
from __future__ import annotations

import numpy as np

from .loaders import ECGRecord

PAPER_SPEED_MM_S = 25.0
GAIN_MM_MV = 10.0

# Restrição física do papel de ECG: uma folha padrão tem entre ~15 e ~35 cm de
# largura útil (10 s a 25 mm/s são 25 cm). A estimativa da grade por
# autocorrelação erra com facilidade em imagens de baixa resolução — quando o
# quadrado de 1 mm ocupa poucos pixels, as linhas finas somem e o algoritmo
# trava na linha grossa de 5 mm, resultando numa escala 5x maior. Confrontar o
# candidato com a largura de papel que ele implica descarta esses casos.
LARGURA_PAPEL_MM_MIN = 120.0
LARGURA_PAPEL_MM_MAX = 400.0
LARGURA_PAPEL_MM_TIPICA = 250.0


PDF_DPI = 300
MM_PER_INCH = 25.4


def _pdf_to_image(data: bytes, dpi: int = PDF_DPI) -> np.ndarray:
    import fitz  # PyMuPDF

    doc = fitz.open(stream=data, filetype="pdf")
    page = doc.load_page(0)
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return img[:, :, :3][:, :, ::-1].copy()  # RGB -> BGR


def _trace_mask(bgr: np.ndarray) -> np.ndarray:
    """Máscara binária do traçado (tinta escura), removendo a grade."""
    import cv2

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    saturation = hsv[:, :, 1].astype(int)
    value = hsv[:, :, 2].astype(int)
    # Traçado: pixels escuros e pouco saturados (tinta preta/azul-escura).
    # Grade típica: vermelha/rosa (alta saturação) ou cinza-claro.
    dark = value < 110
    colored_grid = (saturation > 60) & (value > 90)
    mask = (dark & ~colored_grid).astype(np.uint8) * 255

    if mask.mean() < 0.5:  # imagem clara/escaneada fraca: threshold adaptativo
        mask = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                     cv2.THRESH_BINARY_INV, 31, 15)
        grid = ((saturation > 60) & (value > 90)).astype(np.uint8) * 255
        mask = cv2.subtract(mask, grid)

    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    return mask


def _grid_px_per_mm(bgr: np.ndarray) -> float | None:
    """Estima px/mm pela periodicidade das linhas da grade."""
    import cv2

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(float)
    profile = gray.mean(axis=0)  # colunas: linhas verticais da grade criam vales periódicos
    profile = profile - profile.mean()
    ac = np.correlate(profile, profile, mode="full")[len(profile) - 1:]
    if ac[0] <= 0:
        return None
    ac /= ac[0]
    # A grade tem duas periodicidades: 1 mm (fina) e 5 mm (grossa). O pico mais
    # alto da autocorrelação costuma ser o de 5 mm, porque as linhas grossas são
    # mais escuras. Adotá-lo como "1 mm" multiplicaria por 5 todos os intervalos
    # de tempo — um erro clínico grave e silencioso. Por isso procuramos o menor
    # período que se sustente como fundamental: seus múltiplos também devem
    # apresentar picos.
    lo, hi = 2, min(140, len(ac) - 1)
    if hi - lo < 5:
        return None

    melhor = int(np.argmax(ac[lo:hi])) + lo
    if ac[melhor] < 0.1:
        return None

    # O papel de ECG tem razão exata de 5:1 — a linha grossa de 5 mm costuma
    # dominar a autocorrelação. Se o quinto submúltiplo também exibir um pico
    # real, é ele o quadrado de 1 mm; adotar a linha grossa como 1 mm
    # multiplicaria por 5 todos os intervalos de tempo.
    # A verificação é apenas sobre o divisor 5: buscar o menor período
    # "com suporte harmônico" de forma genérica seleciona sub-harmônicos
    # espúrios, cujos múltiplos coincidem com os picos do fundamental.
    def pico_em(lag: int) -> float:
        if lag < lo or lag >= hi:
            return 0.0
        return float(ac[max(lo, lag - 1): min(hi, lag + 2)].max())

    candidato = int(round(melhor / 5))
    if candidato >= lo and pico_em(candidato) > 0.5 * ac[melhor]:
        return float(candidato)
    return float(melhor)


def _escala_plausivel(escala: float | None, largura_px: int) -> bool:
    """A escala implica uma largura de papel compatível com um ECG real?"""
    if not escala or escala <= 0:
        return False
    largura_mm = largura_px / escala
    return LARGURA_PAPEL_MM_MIN <= largura_mm <= LARGURA_PAPEL_MM_MAX


def _resolver_escala(bgr: np.ndarray) -> tuple[float, str]:
    """Escolhe a escala px/mm confrontando a grade com a largura do papel.

    A autocorrelação sozinha não basta: em imagens de baixa resolução ela trava
    na linha de 5 mm. Testamos o candidato e seus submúltiplos, e ficamos com o
    que implica uma largura de papel plausível — mais próxima dos 25 cm padrão.
    """
    largura = bgr.shape[1]
    bruto = _grid_px_per_mm(bgr)

    candidatos: list[tuple[float, str]] = []
    if bruto:
        # O candidato pode ser o quadrado de 1 mm, ou a linha grossa de 5 mm
        # tomada por engano como 1 mm (daí dividir por 5).
        for divisor, origem in ((1, "grade (1 mm)"), (5, "grade (linha de 5 mm)")):
            valor = bruto / divisor
            if _escala_plausivel(valor, largura):
                candidatos.append((valor, origem))

    # Referência sempre disponível: a folha padrão de 25 cm.
    candidatos.append((largura / LARGURA_PAPEL_MM_TIPICA, "largura padrão de 25 cm"))

    # Entre os plausíveis, o que implica papel mais próximo do tamanho típico.
    melhor, origem = min(
        candidatos,
        key=lambda c: abs(largura / c[0] - LARGURA_PAPEL_MM_TIPICA))
    return melhor, origem


def _separar_faixas(mask: np.ndarray, px_mm: float) -> list[tuple[int, int]]:
    """Separa as faixas horizontais de traçado do laudo.

    Um laudo de 12 derivações tem várias linhas empilhadas, com espaços entre
    elas. Sem essa separação, a busca por "a banda com mais traçado" cresce até
    abraçar a folha inteira, e o centroide por coluna mistura todas as
    derivações — foi o que produziu um sinal sem sentido em laudos reais.
    """
    h, _ = mask.shape
    cobertura = (mask > 0).mean(axis=1)

    # Suavização de ~2 mm: une o traçado da mesma derivação sem colar linhas.
    win = max(3, int(round(2 * px_mm)))
    suave = np.convolve(cobertura, np.ones(win) / win, mode="same")
    if suave.max() <= 0:
        return []

    limiar = max(suave.max() * 0.15, 0.004)
    dentro = suave > limiar

    faixas: list[tuple[int, int]] = []
    inicio = None
    for y in range(h):
        if dentro[y] and inicio is None:
            inicio = y
        elif not dentro[y] and inicio is not None:
            faixas.append((inicio, y))
            inicio = None
    if inicio is not None:
        faixas.append((inicio, h))

    # Descarta faixas finas demais para conter um traçado (texto, régua, borda).
    altura_minima = max(3, int(round(3 * px_mm)))
    return [(a, b) for a, b in faixas if (b - a) >= altura_minima]


def _continuidade(mask: np.ndarray, faixa: tuple[int, int]) -> float:
    """Fração das colunas com traçado dentro da faixa."""
    a, b = faixa
    banda = mask[a:b, :]
    if banda.size == 0:
        return 0.0
    return float((banda > 0).any(axis=0).mean())


# Altura máxima atribuída a uma derivação. A restrição que de fato impede
# invadir a derivação vizinha é a metade da distância até ela; este teto é só
# um limite de sanidade quando não há vizinha. Precisa ser generoso: ondas R de
# 3–4 mV são comuns em precordiais e em hipertrofia, e ocupam 30–40 mm a
# 10 mm/mV. Um teto apertado deceparia o pico e inverteria o sinal do ST.
ALTURA_MAX_DERIVACAO_MM = 80.0


def _expandir_faixa(faixas: list[tuple[int, int]], escolhida: int,
                    altura_img: int, px_mm: float) -> tuple[int, int]:
    """Estende a faixa até a metade do caminho para as faixas vizinhas.

    A segmentação por densidade encontra o corpo do traçado — a região próxima
    à linha de base, onde ele passa a maior parte do tempo — mas corta os picos
    R, que são estreitos e pouco densos. Sem essa expansão o complexo aparece
    decepado e a largura do QRS sai muito maior que a real.
    """
    a, b = faixas[escolhida]
    teto = int(round(ALTURA_MAX_DERIVACAO_MM * px_mm / 2))

    limite_sup = faixas[escolhida - 1][1] if escolhida > 0 else 0
    limite_inf = (faixas[escolhida + 1][0] if escolhida + 1 < len(faixas)
                  else altura_img)

    topo = max(limite_sup + (a - limite_sup) // 2, a - teto, 0)
    base = min(b + (limite_inf - b) // 2, b + teto, altura_img)
    return topo, base


def _rhythm_band(mask: np.ndarray, px_mm: float = 8.0) -> tuple[int, int]:
    """Escolhe a faixa a analisar, preferindo a tira de ritmo.

    No laudo padrão de 12 derivações as linhas superiores trazem quatro
    derivações lado a lado (I, aVR, V1, V4 …): ler a largura inteira dessas
    linhas concatenaria quatro sinais distintos. A tira de ritmo, normalmente a
    última faixa, é uma derivação única ocupando toda a largura — a única que
    pode ser lida como sinal contínuo.
    """
    h, _ = mask.shape
    faixas = _separar_faixas(mask, px_mm)
    if not faixas:
        return 0, h

    # Faixas contínuas de ponta a ponta são candidatas a tira de ritmo; a mais
    # baixa é a posição habitual dela no laudo.
    continuas = [i for i, f in enumerate(faixas)
                 if _continuidade(mask, f) >= 0.90]
    escolhida = (continuas[-1] if continuas else
                 max(range(len(faixas)),
                     key=lambda i: (mask[faixas[i][0]:faixas[i][1], :] > 0).mean()))

    return _expandir_faixa(faixas, escolhida, h, px_mm)


def _indice_faixa_ritmo(mask: np.ndarray, faixas: list[tuple[int, int]]) -> int:
    """Índice da faixa usada como tira de ritmo."""
    continuas = [i for i, f in enumerate(faixas) if _continuidade(mask, f) >= 0.90]
    if continuas:
        return continuas[-1]
    return max(range(len(faixas)),
               key=lambda i: (mask[faixas[i][0]:faixas[i][1], :] > 0).mean())


def _runs_da_coluna(coluna: np.ndarray) -> list[tuple[int, int]]:
    """Trechos contíguos de pixels de traçado numa coluna."""
    idx = np.flatnonzero(coluna)
    if not len(idx):
        return []
    cortes = np.flatnonzero(np.diff(idx) > 1)
    inicios = np.concatenate(([0], cortes + 1))
    fins = np.concatenate((cortes, [len(idx) - 1]))
    return [(int(idx[a]), int(idx[b])) for a, b in zip(inicios, fins)]


def _seguir_tracado(band: np.ndarray, y_inicial: float | None = None) -> np.ndarray:
    """Extrai y(x) seguindo a continuidade da curva.

    O centroide da coluna inteira distorce o traçado: num trecho vertical — a
    subida do QRS — a coluna contém uma barra de pixels, e a média dela fica no
    meio da barra, não onde a curva está. Isso achata o pico R e alarga o
    complexo. Aqui escolhemos, entre os trechos contíguos da coluna, o mais
    próximo do ponto anterior (a curva é contínua) e, dentro dele, o extremo que
    dá seguimento ao movimento — preservando a amplitude do pico.

    Como efeito colateral útil, rótulos de derivação e marcas soltas dentro da
    faixa são ignorados: estão longe da curva que vem sendo seguida.
    """
    _, w = band.shape
    ys = np.full(w, np.nan)

    colunas = [_runs_da_coluna(band[:, x]) for x in range(w)]

    # Ponto de partida. Quando se sabe onde está a linha de base da derivação
    # (`y_inicial`), começa-se na coluna cujo traçado passa mais perto dela: a
    # célula de um laudo pode conter parte da derivação vizinha, e iniciar sobre
    # a curva errada faria o rastreamento seguir o vizinho por toda a extensão.
    inicio = None
    if y_inicial is not None:
        melhor = None
        for x, runs in enumerate(colunas):
            if not runs:
                continue
            dist = min(abs((a + b) / 2 - y_inicial) for a, b in runs)
            if melhor is None or dist < melhor:
                melhor, inicio = dist, x
    else:
        melhor = None
        for x, runs in enumerate(colunas):
            if not runs:
                continue
            curto = min(b - a for a, b in runs)
            if melhor is None or curto < melhor:
                melhor, inicio = curto, x
    if inicio is None:
        return ys

    # Espessura do traço, medida na própria imagem: a maioria das colunas cai em
    # trecho plano, onde a altura do run é exatamente a espessura da linha.
    alturas = [b - a for runs in colunas for a, b in runs]
    espessura = float(np.median(alturas)) if alturas else 1.0
    limite_ingreme = max(espessura * 2.0, espessura + 2.0)

    def escolher(runs, anterior, direcao):
        alvo = min(runs, key=lambda r: abs((r[0] + r[1]) / 2 - anterior))
        a, b = alvo
        # Run da espessura da linha = trecho plano: usa-se o centro. Tomar a
        # ponta aqui faria o valor alternar entre topo e base a cada coluna,
        # criando uma ondulação com a amplitude da espessura do traço — ruído
        # que mantém a derivada alta e impede achar o fim do QRS.
        if (b - a) < limite_ingreme or not direcao:
            return (a + b) / 2.0
        # Run muito mais alto que a linha = segmento íngreme (subida do QRS):
        # segue-se a ponta no sentido do movimento, preservando a amplitude.
        return float(b if direcao > 0 else a)

    runs0 = colunas[inicio]
    if y_inicial is not None:
        a0, b0 = min(runs0, key=lambda r: abs((r[0] + r[1]) / 2 - y_inicial))
        y = (a0 + b0) / 2.0
    else:
        y = float(np.mean([(a + b) / 2 for a, b in runs0]))
    ys[inicio] = y

    for x in range(inicio + 1, w):
        runs = colunas[x]
        if not runs:
            continue
        anterior = ys[x - 1] if not np.isnan(ys[x - 1]) else y
        direcao = 0.0
        if x >= 2 and not np.isnan(ys[x - 2]) and not np.isnan(ys[x - 1]):
            direcao = ys[x - 1] - ys[x - 2]
        y = escolher(runs, anterior, direcao)
        ys[x] = y

    for x in range(inicio - 1, -1, -1):
        runs = colunas[x]
        if not runs:
            continue
        seguinte = ys[x + 1] if not np.isnan(ys[x + 1]) else y
        direcao = 0.0
        if x + 2 < w and not np.isnan(ys[x + 2]) and not np.isnan(ys[x + 1]):
            direcao = ys[x + 1] - ys[x + 2]
        ys[x] = escolher(runs, seguinte, direcao)

    return ys


# Layout clínico padrão "3×4": três linhas de quatro derivações, cada coluna
# cobrindo 2,5 s consecutivos do mesmo registro de 10 s, mais a tira de ritmo.
# As colunas de uma mesma linha NÃO são simultâneas entre si — I ocupa 0–2,5 s,
# aVR 2,5–5 s e assim por diante. Já as derivações de uma mesma coluna são
# simultâneas (I, II e III compartilham 0–2,5 s).
LAYOUT_3X4 = [["I", "aVR", "V1", "V4"],
              ["II", "aVL", "V2", "V5"],
              ["III", "aVF", "V3", "V6"]]
COLUNAS_LAYOUT = 4
SEGUNDOS_POR_COLUNA = 2.5
MARGEM_COLUNA_MM = 2.0   # descarta a transição entre derivações vizinhas


def _extensao_tracado(mask: np.ndarray, faixa: tuple[int, int]) -> tuple[int, int]:
    a, b = faixa
    colunas = (mask[a:b, :] > 0).any(axis=0)
    if not colunas.any():
        return 0, mask.shape[1]
    return int(np.argmax(colunas)), len(colunas) - int(np.argmax(colunas[::-1]))


def _celulas_12_derivacoes(mask: np.ndarray, faixas: list[tuple[int, int]],
                           px_mm: float, indice_ritmo: int
                           ) -> tuple[dict[str, tuple[int, int, int, int, int]],
                                      dict[str, float]]:
    """Mapeia cada derivação à sua célula (y0, y1, x0, x1, coluna).

    As divisas de coluna são geométricas: o traçado corre continuamente de uma
    derivação para a seguinte, sem vão detectável entre elas, de modo que não há
    o que segmentar por imagem. O que define o formato é a norma de impressão —
    quatro colunas iguais de 2,5 s numa folha de 10 s.
    """
    # Só faixas que atravessam a folha são linhas de derivação. Rótulos de
    # derivação formam faixas próprias — densas onde há texto, mas cobrindo uma
    # fração mínima da largura. Sem esse filtro, os rótulos são tomados por
    # linhas e as derivações saem de células vazias.
    linhas = [i for i in range(len(faixas))
              if i != indice_ritmo and _continuidade(mask, faixas[i]) >= 0.5]
    if len(linhas) < len(LAYOUT_3X4):
        return {}
    if len(linhas) > len(LAYOUT_3X4):
        # Mais candidatas que linhas previstas: ficam as de maior cobertura,
        # devolvidas na ordem vertical original.
        melhores = sorted(linhas,
                          key=lambda i: -_continuidade(mask, faixas[i])
                          )[: len(LAYOUT_3X4)]
        linhas = sorted(melhores)
    linhas = linhas[: len(LAYOUT_3X4)]

    celulas: dict[str, tuple[int, int, int, int, int]] = {}
    bases: dict[str, float] = {}
    margem = int(round(MARGEM_COLUNA_MM * px_mm))

    # A expansão vertical considera apenas as faixas estruturais (linhas de
    # derivação e tira de ritmo). Incluir as faixas de rótulo faria o texto
    # limitar o território da derivação, decepando ondas de grande amplitude —
    # e um pico decepado inverte o sinal do desvio de ST.
    estruturais = sorted(set(linhas) | {indice_ritmo})
    faixas_est = [faixas[i] for i in estruturais]

    for nome_linha, idx in zip(LAYOUT_3X4, linhas):
        pos = estruturais.index(idx)
        y0, y1 = _expandir_faixa(faixas_est, pos, mask.shape[0], px_mm)
        x0, x1 = _extensao_tracado(mask, faixas[idx])
        largura = (x1 - x0) / COLUNAS_LAYOUT
        nucleo = (faixas[idx][0] + faixas[idx][1]) / 2.0
        for col, nome in enumerate(nome_linha):
            cx0 = int(round(x0 + col * largura)) + (margem if col else 0)
            cx1 = int(round(x0 + (col + 1) * largura)) - (
                margem if col < COLUNAS_LAYOUT - 1 else 0)
            if cx1 - cx0 > 4:
                celulas[nome] = (y0, y1, cx0, cx1, col)
                bases[nome] = nucleo
    return celulas, bases


def _celula_para_mv(mask: np.ndarray, celula: tuple[int, int, int, int, int],
                    px_mm: float, y_base: float | None = None) -> np.ndarray | None:
    y0, y1, x0, x1, _ = celula
    banda = mask[y0:y1, x0:x1]
    # y_base é o centro do núcleo denso da linha, antes da expansão: é ali que
    # está a linha isoelétrica desta derivação, e é dali que o rastreamento deve
    # partir para não engatar na derivação vizinha.
    ys = _seguir_tracado(banda, None if y_base is None else y_base - y0)
    validos = ~np.isnan(ys)
    if validos.sum() < len(ys) * 0.5:
        return None
    xs = np.arange(len(ys))
    ys = np.interp(xs, xs[validos], ys[validos])
    if len(ys) > 7:
        from scipy.signal import savgol_filter
        ys = savgol_filter(ys, window_length=5, polyorder=2)
    return -(ys - float(np.median(ys))) / (GAIN_MM_MV * px_mm)


def digitize(filename: str, data: bytes,
             px_per_mm: float | None = None) -> ECGRecord:
    import cv2

    notes: list[str] = []
    is_pdf = filename.lower().endswith(".pdf")

    if is_pdf:
        bgr = _pdf_to_image(data)
    else:
        arr = np.frombuffer(data, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError("Não foi possível decodificar a imagem enviada.")

    scale = px_per_mm
    if scale is None and is_pdf:
        # Em PDF a escala é determinística: nós escolhemos o DPI da renderização.
        scale = PDF_DPI / MM_PER_INCH
        notes.append(f"Calibração pelo DPI de renderização do PDF: {scale:.1f} px/mm.")

    if scale is None:
        scale, origem = _resolver_escala(bgr)
        largura_mm = bgr.shape[1] / scale
        notes.append(f"Calibração: {scale:.1f} px/mm por {origem} — "
                     f"papel de {largura_mm / 10:.0f} cm "
                     f"({largura_mm / PAPER_SPEED_MM_S:.1f} s a 25 mm/s).")
        if "largura padrão" in origem:
            notes.append("A grade não forneceu escala confiável (comum em imagens "
                         "de baixa resolução); as medidas de tempo dependem de o "
                         "exame ter sido impresso a 25 mm/s em folha de 25 cm.")

    mask = _trace_mask(bgr)
    top, bottom = _rhythm_band(mask, px_mm=scale)
    band = mask[top:bottom, :]
    altura_mm = (bottom - top) / scale
    notes.append(f"Faixa analisada: y={top}–{bottom} px ({altura_mm:.0f} mm de "
                 f"altura), de {len(_separar_faixas(mask, scale))} faixa(s) "
                 "detectada(s) no laudo.")

    w = band.shape[1]
    ys = _seguir_tracado(band)

    valid = ~np.isnan(ys)
    if valid.sum() < w * 0.3:
        raise ValueError("Traçado insuficiente detectado na imagem. Verifique a "
                         "qualidade/contraste do exame enviado.")
    xs = np.arange(w)
    ys = np.interp(xs, xs[valid], ys[valid])

    # O traçado sai quantizado em linhas inteiras de pixel, o que acrescenta um
    # serrilhado de ±1 px. Ele é artefato da leitura, não do exame, e mantém a
    # derivada permanentemente acima de zero — o que impedia a delimitação do
    # QRS de encontrar o fim do complexo. Savitzky-Golay remove esse degrau
    # preservando a amplitude dos picos, ao contrário de uma média móvel.
    if len(ys) > 7:
        from scipy.signal import savgol_filter
        ys = savgol_filter(ys, window_length=5, polyorder=2)

    fs = PAPER_SPEED_MM_S * scale                 # amostras (colunas) por segundo
    baseline = float(np.median(ys))
    mv = -(ys - baseline) / (GAIN_MM_MV * scale)  # y cresce para baixo

    # Reamostra para 500 Hz para padronizar o pipeline de análise
    target_fs = 500.0
    t_old = np.arange(len(mv)) / fs
    t_new = np.arange(0, t_old[-1], 1.0 / target_fs)
    signal = np.interp(t_new, t_old, mv).reshape(-1, 1)

    notes.append(f"Duração extraída: {t_old[-1]:.1f} s.")

    # ---- Demais derivações do laudo ----
    # Cada uma cobre apenas os 2,5 s da sua coluna; o restante fica como NaN
    # para que nenhuma medida seja calculada sobre trecho inexistente.
    faixas = _separar_faixas(mask, scale)
    nomes = ["ritmo"]
    canais = [signal[:, 0]]
    if len(faixas) >= len(LAYOUT_3X4) + 1:
        idx_ritmo = _indice_faixa_ritmo(mask, faixas)
        celulas, bases = _celulas_12_derivacoes(mask, faixas, scale, idx_ritmo)
        n_total = signal.shape[0]
        obtidas = []
        for nome, celula in celulas.items():
            mv = _celula_para_mv(mask, celula, scale, bases.get(nome))
            if mv is None:
                continue
            coluna = celula[4]
            # Reamostra a célula para a taxa alvo e a posiciona no instante
            # correspondente à sua coluna dentro dos 10 s.
            dur = len(mv) / (PAPER_SPEED_MM_S * scale)
            n_alvo = max(2, int(round(dur * target_fs)))
            reamostrado = np.interp(np.linspace(0, len(mv) - 1, n_alvo),
                                    np.arange(len(mv)), mv)
            canal = np.full(n_total, np.nan)
            inicio = int(round(coluna * SEGUNDOS_POR_COLUNA * target_fs))
            fim = min(n_total, inicio + n_alvo)
            if fim > inicio:
                canal[inicio:fim] = reamostrado[: fim - inicio]
                nomes.append(nome)
                canais.append(canal)
                obtidas.append(nome)

        if obtidas:
            ordem = [n for linha in LAYOUT_3X4 for n in linha]
            faltando = [n for n in ordem if n not in obtidas]
            notes.append(
                f"Layout 3×4 reconhecido: {len(obtidas)} derivações extraídas "
                f"({', '.join(n for n in ordem if n in obtidas)})"
                + (f"; não obtidas: {', '.join(faltando)}" if faltando else "") + ".")
            notes.append(
                "As colunas do laudo são trechos consecutivos do exame, não "
                "simultâneos: cada derivação cobre 2,5 s. As medidas por "
                "derivação (ST, voltagem, eixo) usam apenas os batimentos "
                "dentro da própria janela.")
        else:
            notes.append("Layout de 12 derivações não pôde ser extraído; "
                         "análise restrita à tira de ritmo.")
    else:
        notes.append("Layout de 12 derivações não reconhecido "
                     f"({len(faixas)} faixa(s) detectada(s)); análise restrita "
                     "à tira de ritmo.")

    if len(nomes) == 1:
        notes.append("Para medidas completas de 12 derivações, envie o sinal digital.")

    return ECGRecord(signal=np.column_stack(canais), sampling_rate=target_fs,
                     lead_names=nomes, source_format="imagem", notes=notes)

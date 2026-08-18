"""Ponte entre o JavaScript da página e o motor de análise do CardioLaudo.

Roda dentro do Pyodide, no navegador de quem acessa. O exame nunca sai do
computador do usuário — não há upload, não há servidor.

Reaproveita exatamente os mesmos módulos do servidor (mesmo NumPy, mesmo SciPy,
mesmo NeuroKit2), o que preserva integralmente a validação feita contra o
PTB-XL: as medidas saem numericamente iguais.
"""
from __future__ import annotations

import base64
import json
import sys

sys.path.insert(0, "/cardiolaudo")

from backend.app.classification.narrative import narrativa  # noqa: E402
from backend.app.classification.rules import classify, summarize  # noqa: E402
from backend.app.ingestion.image_digitizer import digitize  # noqa: E402
from backend.app.ingestion.loaders import load_digital  # noqa: E402
from backend.app.processing.analysis import analyze  # noqa: E402

EXT_IMAGEM = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".pdf")
EXT_DIGITAL = (".csv", ".txt", ".zip")

MAX_PONTOS_TRACADO = 4000


def _preview(analise, registro) -> list[dict]:
    """Amostra do traçado analisado, para desenhar no canvas."""
    import numpy as np

    sinal = analise.cleaned_rhythm
    if sinal is None:
        return []
    passo = max(1, len(sinal) // MAX_PONTOS_TRACADO)
    amostras = np.asarray(sinal[::passo], dtype=float)
    return [{
        "lead_name": analise.rhythm_lead,
        "sampling_rate_hz": registro.sampling_rate / passo,
        "samples": [round(float(v), 4) for v in amostras],
    }]


def analisar(nome_arquivo: str, dados_b64: str,
             idade: int | None = None, sexo: str | None = None) -> str:
    """Analisa um exame. Recebe o arquivo em base64 (vindo do JavaScript).

    Retorna JSON — atravessar a fronteira Python↔JS com um texto simples evita
    surpresas de conversão de tipos.
    """
    import numpy as np

    dados = base64.b64decode(dados_b64)
    nome = (nome_arquivo or "").lower()

    try:
        if nome.endswith(EXT_IMAGEM):
            registro = digitize(nome_arquivo, dados)
        elif nome.endswith(EXT_DIGITAL):
            registro = load_digital(nome_arquivo, dados, sampling_rate=500.0)
        else:
            return json.dumps({"erro": (
                "Formato não suportado. Envie sinal digital (CSV, TXT) ou "
                "imagem do traçado (PNG, JPG, PDF).")})
    except ValueError as exc:
        return json.dumps({"erro": str(exc)})
    except Exception as exc:
        return json.dumps({"erro": f"Não foi possível ler o arquivo: {exc}"})

    if sexo:
        s = sexo.strip().lower()
        sexo = {"f": "f", "feminino": "f", "m": "m", "masculino": "m"}.get(s)

    try:
        a = analyze(registro)
    except Exception as exc:
        return json.dumps({"erro": f"Falha ao analisar o sinal: {exc}"})

    achados = classify(a, age=idade, sex=sexo)

    def num(v):
        return None if v is None or not np.isfinite(v) else float(v)

    return json.dumps({
        "arquivo": nome_arquivo,
        "origem": registro.source_format,
        "derivacoes": list(registro.lead_names),
        "duracao_s": round(registro.duration_s, 2),
        "medidas": {
            "fc": num(a.heart_rate_bpm), "rr": num(a.rr_mean_ms),
            "rr_cv": num(a.rr_cv), "pr": num(a.pr_ms), "qrs": num(a.qrs_ms),
            "qt": num(a.qt_ms), "qtc_bazett": num(a.qtc_bazett_ms),
            "qtc_fridericia": num(a.qtc_fridericia_ms),
            "eixo": num(a.axis_degrees), "sokolow": num(a.sokolow_lyon_mv),
            "batimentos": int(a.n_beats),
        },
        "st": {k: round(v * 1000) for k, v in a.st_deviation_mv.items()},
        "achados": [{"code": f.code, "label": f.label, "severity": f.severity,
                     "criteria": f.criteria, "detail": f.detail} for f in achados],
        "resumo": summarize(achados, a),
        "narrativa": narrativa(a, achados),
        "avisos": list(a.quality_warnings) + list(registro.notes),
        "preview": _preview(a, registro),
    }, ensure_ascii=False)


def gerar_pdf(resultado_json: str) -> str:
    """Gera o laudo em PDF e devolve em base64, para o navegador baixar."""
    from datetime import datetime, timezone

    from backend.app.reporting.report import build_pdf

    r = json.loads(resultado_json)
    m = r.get("medidas", {})
    payload = {
        "analysis_id": r.get("id", "local"),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "engine_version": r.get("versao", "web"),
        "source": {"filename": r.get("arquivo"), "patient": r.get("paciente", {})},
        "measurements": {
            "heart_rate_bpm": m.get("fc"), "rr_mean_ms": m.get("rr"),
            "pr_ms": m.get("pr"), "qrs_ms": m.get("qrs"), "qt_ms": m.get("qt"),
            "qtc_bazett_ms": m.get("qtc_bazett"),
            "qtc_fridericia_ms": m.get("qtc_fridericia"),
            "axis_degrees": m.get("eixo"),
        },
        "findings": r.get("achados", []),
        "summary": r.get("resumo", ""),
        "narrativa": r.get("narrativa"),
        "deep_learning": None,
    }
    return base64.b64encode(build_pdf(payload)).decode("ascii")


def diagnostico() -> str:
    """Versões carregadas — útil para depurar problemas do usuário."""
    import numpy, scipy
    try:
        import neurokit2
        nk = neurokit2.__version__
    except Exception as exc:
        nk = f"indisponível ({exc})"
    try:
        import cv2
        ocv = cv2.__version__
    except Exception as exc:
        ocv = f"indisponível ({exc})"
    return json.dumps({"python": sys.version.split()[0], "numpy": numpy.__version__,
                       "scipy": scipy.__version__, "neurokit2": nk, "opencv": ocv})

"""Geração de laudo em PDF (ReportLab) e trilha de auditoria."""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

DISCLAIMER = (
    "Este resultado foi gerado por software de análise automatizada e destina-se "
    "exclusivamente ao APOIO à decisão clínica. NÃO constitui laudo médico nem "
    "substitui a interpretação por médico habilitado. Achados críticos exigem "
    "confirmação médica imediata. Ferramenta em validação clínica — não registrada "
    "na ANVISA como dispositivo médico."
)

AUDIT_DIR = Path(__file__).resolve().parents[3] / "data" / "audit"

SEVERITY_LABELS = {"normal": "Normal", "limitrofe": "Limítrofe",
                   "anormal": "Anormal", "critico": "CRÍTICO"}


def audit_log(entry: dict) -> None:
    """Trilha de auditoria: uma linha por análise, cifrada.

    A trilha carrega os mesmos dados clínicos do laudo (achados, medidas, idade
    e sexo do paciente, identificação do operador). Deixá-la em texto puro
    tornaria inútil cifrar o banco: bastaria ler os arquivos ao lado.

    Cada linha guarda, fora do envelope cifrado, apenas o carimbo de tempo e o
    identificador da análise — o suficiente para localizar um registro sem
    revelar seu conteúdo. Leitura: `python scripts/manage_keys.py ler-auditoria`.
    """
    from ..auth.crypto import cifrar

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    fname = AUDIT_DIR / f"{datetime.now(timezone.utc):%Y%m}.jsonl"
    envelope = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "analysis_id": entry.get("analysis_id"),
        "dados": cifrar(json.dumps(entry, ensure_ascii=False, default=str)),
    }
    with open(fname, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(envelope, ensure_ascii=False) + "\n")


def build_pdf(result: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm,
                            bottomMargin=18 * mm, leftMargin=18 * mm,
                            rightMargin=18 * mm,
                            title="Análise automatizada de ECG")
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=15, spaceAfter=4)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8,
                           textColor=colors.HexColor("#555555"))
    warn = ParagraphStyle("warn", parent=styles["Normal"], fontSize=8,
                          textColor=colors.HexColor("#7a1f1f"),
                          borderColor=colors.HexColor("#c0392b"),
                          borderWidth=0.7, borderPadding=5)

    source = result.get("source") or {}
    op = source.get("operator") or {}
    paciente = source.get("patient") or {}

    identificacao = [f"ID {result['analysis_id']}", result["created_at"],
                     f"motor v{result['engine_version']}"]
    if op.get("full_name"):
        registro = f" — {op['professional_id']}" if op.get("professional_id") else ""
        identificacao.append(f"executado por {escape(op['full_name'])}{escape(registro)}")
    # `age` e `sex` vêm do formulário: precisam ser escapados como qualquer
    # outro texto de origem externa — Paragraph interpreta marcação XML.
    dados_paciente = " · ".join(
        x for x in (f"idade {escape(str(paciente['age']))}" if paciente.get("age") else "",
                    f"sexo {escape(str(paciente['sex']))}" if paciente.get("sex") else "") if x)

    el = [Paragraph("Análise automatizada de eletrocardiograma", h1),
          Paragraph(" — ".join(identificacao), small)]
    if source.get("filename"):
        el.append(Paragraph(f"Arquivo: {escape(str(source['filename']))}"
                            + (f" · Paciente: {dados_paciente}" if dados_paciente else ""),
                            small))
    el.append(Spacer(1, 6 * mm))

    m = result.get("measurements", {})
    def fmt(v, suffix=""):
        return f"{v:.0f}{suffix}" if isinstance(v, (int, float)) else "—"

    rows = [["Medida", "Valor", "Medida", "Valor"],
            ["Freq. cardíaca", fmt(m.get("heart_rate_bpm"), " bpm"),
             "QT", fmt(m.get("qt_ms"), " ms")],
            ["RR médio", fmt(m.get("rr_mean_ms"), " ms"),
             "QTc (Bazett)", fmt(m.get("qtc_bazett_ms"), " ms")],
            ["PR", fmt(m.get("pr_ms"), " ms"),
             "QTc (Fridericia)", fmt(m.get("qtc_fridericia_ms"), " ms")],
            ["QRS", fmt(m.get("qrs_ms"), " ms"),
             "Eixo elétrico", fmt(m.get("axis_degrees"), "°")]]
    t = Table(rows, colWidths=[42 * mm, 32 * mm, 42 * mm, 32 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#123a5c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8d3dd")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f6f9")]),
    ]))
    el += [t, Spacer(1, 6 * mm), Paragraph("<b>Achados</b>", styles["Heading3"])]

    sev_color = {"critico": "#c0392b", "anormal": "#b9770e",
                 "limitrofe": "#7d6608", "normal": "#1e8449"}
    for x in result.get("findings", []):
        el.append(Paragraph(
            f'<font color="{sev_color.get(x["severity"], "#333")}">'
            f'<b>[{escape(SEVERITY_LABELS.get(x["severity"], x["severity"]))}]</b></font> '
            f'<b>{escape(str(x["label"]))}</b> — critério: {escape(str(x["criteria"]))}. '
            f'{escape(str(x.get("detail", "")))}',
            styles["Normal"]))
        el.append(Spacer(1, 1.5 * mm))

    if result.get("deep_learning"):
        el.append(Paragraph("<b>Classificador por deep learning (PTB-XL)</b>",
                            styles["Heading3"]))
        for p in result["deep_learning"]:
            el.append(Paragraph(f'{escape(str(p["label"]))}: {p["probability"] * 100:.0f}%',
                                styles["Normal"]))

    el += [Spacer(1, 4 * mm),
           Paragraph(f"<b>Conclusão automatizada:</b> {escape(str(result['summary']))}",
                     styles["Normal"]),
           Spacer(1, 6 * mm), Paragraph(DISCLAIMER, warn)]
    doc.build(el)
    return buf.getvalue()

"""CardioLaudo — API de análise automatizada de ECG.

Executar (a partir da raiz do projeto):
    .venv\\Scripts\\python -m uvicorn backend.app.main:app --reload --port 8000
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .auth import service as auth_service
from .auth.crypto import carregar_chave
from .auth.db import DB_PATH, init_db, precisa_migrar_cifragem, transacao
from . import scheduler
from .retention import backups_texto_puro
from .seed import semear_admin
from .auth.routes import admin_router
from .auth.routes import router as auth_router
from .auth.routes import usuario_atual
from .auth.service import User
from .classification import deep_model
from .classification.narrative import narrativa
from .classification.rules import classify, summarize
from .ingestion.image_digitizer import digitize
from .ingestion.loaders import load_digital
from .processing.analysis import analyze
from .reporting.report import DISCLAIMER, audit_log, build_pdf
from .schemas import AnalysisResult, Measurements, SignalPreview

ENGINE_VERSION = "0.1.0"
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

logger = logging.getLogger("cardiolaudo")

# O logger da aplicação precisa de destino próprio: sem isto, avisos do
# agendador — inclusive o disparo do disjuntor de segurança, que interrompe o
# expurgo de prontuários — não apareceriam em lugar nenhum.
if not logging.getLogger("cardiolaudo").handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)
    logger.propagate = False

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".pdf"}
DIGITAL_EXT = {".csv", ".txt", ".zip"}
MAX_UPLOAD_MB = 50
CHUNK_BYTES = 1 << 20

# Origens autorizadas do frontend. Curinga "*" é inadequado para uma API que
# processa dado de saúde: defina CARDIOLAUDO_ORIGINS no ambiente para publicar.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv(
    "CARDIOLAUDO_ORIGINS",
    "http://localhost:8000,http://127.0.0.1:8000").split(",") if o.strip()]

app = FastAPI(title="CardioLaudo — Análise automatizada de ECG",
              version=ENGINE_VERSION)
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                   allow_credentials=True,
                   allow_methods=["GET", "POST"], allow_headers=["*"])
app.include_router(auth_router)
app.include_router(admin_router)


@app.on_event("startup")
def _startup() -> None:
    # Falha cedo e de forma clara se a chave de cifragem não estiver
    # configurada: subir sem ela só descobriria o problema na primeira análise,
    # já com o exame de um paciente em mãos.
    carregar_chave()
    init_db()

    # Cria a conta administradora se o banco estiver vazio (hospedagem com disco
    # efêmero recria o banco a cada deploy). Não faz nada se já houver contas.
    try:
        semear_admin()
    except Exception:
        logger.exception("Falha ao semear a conta administradora")

    with transacao() as conn:
        pendentes = precisa_migrar_cifragem(conn)
    if pendentes:
        logger.warning(
            "Dados em texto puro nas tabelas: %s. Rode "
            "`python scripts/migrate_encrypt.py --backup` para cifrá-los.",
            ", ".join(pendentes))

    # Um ciclo imediato ao subir (o servidor pode ter ficado dias parado) e,
    # daí em diante, o agendador diário.
    try:
        scheduler.executar_ciclo(executor="startup")
    except Exception:
        logger.exception("Falha no expurgo de inicialização")

    try:
        antigos = backups_texto_puro(DB_PATH.parent)
        if antigos:
            logger.warning(
                "%d backup(s) anterior(es) à cifragem em TEXTO PURO em %s — "
                "ainda dentro do prazo de retenção. Se a migração já foi "
                "conferida, apague-os agora.", len(antigos), DB_PATH.parent)
    except Exception:
        logger.exception("Falha ao verificar backups em texto puro")

    scheduler.iniciar(app)


@app.on_event("shutdown")
async def _shutdown() -> None:
    await scheduler.encerrar(app)


@app.get("/api/health")
def health():
    return {"status": "ok", "engine_version": ENGINE_VERSION,
            "deep_learning_available": deep_model.is_available()}


@app.post("/api/analyze", response_model=AnalysisResult)
async def analyze_ecg(
    file: UploadFile = File(...),
    sampling_rate: float = Form(500.0),
    age: int | None = Form(None),
    sex: str | None = Form(None),
    user: User = Depends(usuario_atual),
):
    # Leitura em blocos: aborta assim que o limite é ultrapassado, em vez de
    # carregar um corpo arbitrariamente grande na memória antes de verificar.
    # Campos do formulário são normalizados a valores conhecidos: texto livre
    # aqui acabaria interpolado no laudo em PDF e na trilha de auditoria.
    if sex is not None:
        s = sex.strip().lower()
        sex = {"f": "f", "feminino": "f", "female": "f",
               "m": "m", "masculino": "m", "male": "m"}.get(s)
        if sex is None and s:
            raise HTTPException(422, "Campo 'sex' deve ser 'f' ou 'm'.")
    if age is not None and not (0 <= age <= 120):
        raise HTTPException(422, "Campo 'age' fora da faixa admitida (0–120).")

    limite = MAX_UPLOAD_MB * 1024 * 1024
    partes: list[bytes] = []
    total = 0
    while chunk := await file.read(CHUNK_BYTES):
        total += len(chunk)
        if total > limite:
            raise HTTPException(413, f"Arquivo acima de {MAX_UPLOAD_MB} MB.")
        partes.append(chunk)
    data = b"".join(partes)
    if not data:
        raise HTTPException(400, "Arquivo vazio.")

    # Apenas o sufixo é usado, nunca o nome recebido — evita path traversal.
    suffix = Path(file.filename or "").suffix.lower()
    try:
        if suffix in IMAGE_EXT:
            record = digitize(file.filename, data)
        elif suffix in DIGITAL_EXT:
            record = load_digital(file.filename, data, sampling_rate=sampling_rate)
        else:
            raise ValueError(
                f"Extensão não suportada: '{suffix}'. Envie sinal digital "
                "(CSV, TXT, ZIP/WFDB) ou imagem (PNG, JPG, PDF).")
    except ValueError as exc:
        # ValueError carrega mensagens redigidas para o usuário (formato não
        # suportado, traçado ilegível): pode ser exposta.
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        # Demais exceções podem conter caminho de arquivo, versão de biblioteca
        # ou trecho de stack: registram-se no servidor, não na resposta.
        logger.exception("Falha ao ler o arquivo enviado")
        raise HTTPException(500, "Não foi possível ler o arquivo enviado.") from exc

    try:
        analysis = analyze(record)
    except Exception as exc:
        logger.exception("Falha na análise do sinal")
        raise HTTPException(500, "Falha ao analisar o sinal do exame.") from exc

    findings = classify(analysis, age=age, sex=sex)
    dl = deep_model.predict(record.signal, record.sampling_rate, record.lead_names)

    preview = _make_preview(analysis, record)

    result = AnalysisResult(
        # Token imprevisível: o ID é a única credencial para baixar o laudo
        # enquanto não houver autenticação de usuário.
        analysis_id=secrets.token_urlsafe(24),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        engine_version=ENGINE_VERSION,
        source={
            "filename": file.filename,
            "format": record.source_format,
            "sha256": hashlib.sha256(data).hexdigest(),
            "leads": record.lead_names,
            "sampling_rate_hz": record.sampling_rate,
            "duration_s": round(record.duration_s, 2),
            "patient": {"age": age, "sex": sex},
            # Rastreabilidade SaMD: quem executou a análise.
            "operator": {"email": user.email, "full_name": user.full_name,
                         "professional_id": user.professional_id},
        },
        quality={"warnings": analysis.quality_warnings + record.notes,
                 "rhythm_lead": analysis.rhythm_lead},
        measurements=Measurements(
            heart_rate_bpm=analysis.heart_rate_bpm,
            rr_mean_ms=analysis.rr_mean_ms,
            rr_cv=analysis.rr_cv,
            rmssd_ratio=analysis.rmssd_ratio,
            pr_ms=analysis.pr_ms,
            qrs_ms=analysis.qrs_ms,
            qt_ms=analysis.qt_ms,
            qtc_bazett_ms=analysis.qtc_bazett_ms,
            qtc_fridericia_ms=analysis.qtc_fridericia_ms,
            axis_degrees=analysis.axis_degrees,
            sokolow_lyon_mv=analysis.sokolow_lyon_mv,
            p_wave_present=analysis.p_wave_present,
            n_beats=analysis.n_beats,
            duration_s=analysis.duration_s,
            sampling_rate_hz=analysis.sampling_rate_hz,
        ),
        findings=findings,
        deep_learning=dl,
        summary=summarize(findings, analysis),
        narrativa=narrativa(analysis, findings),
        disclaimer=DISCLAIMER,
        preview=preview,
    )

    payload = result.model_dump()
    auth_service.salvar_analise(result.analysis_id, user.id, payload)
    audit_log({**{k: v for k, v in payload.items() if k != "preview"},
               "operator_id": user.id, "operator_email": user.email})
    return result


def _make_preview(analysis, record, max_points: int = 4000) -> list[SignalPreview]:
    sig = analysis.cleaned_rhythm
    if sig is None:
        return []
    step = max(1, len(sig) // max_points)
    ds = np.asarray(sig[::step], dtype=float)
    return [SignalPreview(lead_name=analysis.rhythm_lead,
                          sampling_rate_hz=record.sampling_rate / step,
                          samples=[round(float(v), 4) for v in ds])]


@app.get("/api/report/{analysis_id}/pdf")
def report_pdf(analysis_id: str, user: User = Depends(usuario_atual)):
    # A consulta filtra por user_id: o identificador da análise não é
    # credencial — outro usuário não recupera o laudo mesmo conhecendo o ID.
    result = auth_service.obter_analise(analysis_id, user.id)
    if not result:
        raise HTTPException(404, "Análise não encontrada.")
    pdf = build_pdf(result)
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="ecg_{analysis_id}.pdf"'})


@app.get("/api/analyses")
def listar_analises(user: User = Depends(usuario_atual)):
    """Histórico de exames do próprio usuário."""
    return {"analyses": auth_service.listar_analises(user.id)}


@app.get("/")
def index():
    """Serve a interface com os assets versionados.

    O cabeçalho `no-cache` sozinho não resolve: navegadores que já guardaram a
    versão anterior continuam usando-a. Carimbar a URL com a versão do motor
    (e a data de modificação, em desenvolvimento) garante que uma atualização
    do software chegue de fato ao usuário — um CSS ou JS defasado pode
    apresentar de forma incorreta o resultado de um exame.
    """
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    marca = ENGINE_VERSION
    for nome in ("styles.css", "app.js"):
        caminho = FRONTEND_DIR / nome
        if caminho.exists():
            marca_arquivo = f"{ENGINE_VERSION}-{int(caminho.stat().st_mtime)}"
            html = html.replace(f"/static/{nome}", f"/static/{nome}?v={marca_arquivo}")
    return Response(html, media_type="text/html",
                    headers={"Cache-Control": "no-cache"})


class StaticSemCache(StaticFiles):
    """Obriga o navegador a revalidar os arquivos da interface.

    Um CSS ou JS servido de cache após uma atualização pode renderizar de forma
    incorreta o resultado de um exame — inclusive esconder um achado crítico.
    `no-cache` não reenvia o conteúdo desnecessariamente: o navegador revalida
    e recebe 304 quando nada mudou.
    """

    def is_not_modified(self, response_headers, request_headers) -> bool:
        return super().is_not_modified(response_headers, request_headers)

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", StaticSemCache(directory=FRONTEND_DIR), name="static")

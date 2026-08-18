"""Testes da política de retenção e do expurgo.

O que precisa ser provado não é que o comando roda, e sim que ele **não apaga o
que não deve**: simulação por padrão, retenção legal respeitada, piso de 20 anos
inviolável por configuração acidental, e exclusão deixando rastro.

Uso: .venv\\Scripts\\python scripts\\test_retention.py
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import purge, retention  # noqa: E402
from backend.app.auth import crypto, service  # noqa: E402
from backend.app.auth.db import DB_PATH, init_db, transacao  # noqa: E402
from backend.app.reporting.report import AUDIT_DIR  # noqa: E402

falhas: list[str] = []


def checar(cond: bool, desc: str) -> None:
    print(f"  [{'OK ' if cond else 'FALHA'}] {desc}")
    if not cond:
        falhas.append(desc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def semear_laudo(user_id: int, idade_dias: int, hold: bool = False) -> str:
    """Grava um laudo com data retroativa para simular envelhecimento."""
    aid = uuid.uuid4().hex[:16]
    criado = _iso(datetime.now(timezone.utc) - timedelta(days=idade_dias))
    payload = {"analysis_id": aid, "summary": f"laudo de teste {aid}",
               "source": {"filename": "teste.csv"}}
    with transacao() as conn:
        conn.execute(
            "INSERT INTO analyses (id, user_id, created_at, payload_enc,"
            " legal_hold, legal_hold_motivo) VALUES (?,?,?,?,?,?)",
            (aid, user_id, criado,
             crypto.cifrar(json.dumps(payload, ensure_ascii=False)),
             1 if hold else 0, "processo judicial" if hold else None))
    return aid


def existe(aid: str) -> bool:
    with transacao() as conn:
        return conn.execute("SELECT 1 FROM analyses WHERE id = ?",
                            (aid,)).fetchone() is not None


def main() -> int:
    init_db()
    sufixo = uuid.uuid4().hex[:8]
    email = f"retencao.{sufixo}@teste.local"
    user = service.criar_usuario(email, f"Dr. Retencao {sufixo}",
                                 "RetencaoTeste2026", "CRM-RET")

    anos21 = int(21 * retention.DIAS_POR_ANO)

    print("\n1. Piso legal de 20 anos para laudos")
    os.environ["CARDIOLAUDO_RETENCAO_LAUDOS_DIAS"] = "90"
    os.environ.pop("CARDIOLAUDO_RETENCAO_ACEITA_RISCO", None)
    try:
        retention.politicas()
        checar(False, "configuração de 90 dias deveria ser recusada")
    except ValueError as exc:
        checar("CFM" in str(exc) and "1.821" in str(exc),
               "90 dias é recusado citando a Resolução CFM 1.821/2007")

    os.environ["CARDIOLAUDO_RETENCAO_ACEITA_RISCO"] = "1"
    try:
        p = retention.politica("laudos")
        checar(p.dias == 90, "aceita 90 dias apenas com assunção explícita de risco")
    except ValueError:
        checar(False, "com ACEITA_RISCO=1 deveria aceitar")
    os.environ.pop("CARDIOLAUDO_RETENCAO_LAUDOS_DIAS", None)
    os.environ.pop("CARDIOLAUDO_RETENCAO_ACEITA_RISCO", None)
    checar(retention.politica("laudos").dias == retention.PISO_LEGAL_LAUDOS_DIAS,
           "padrão volta a 20 anos quando nada é configurado")

    print("\n2. Laudo dentro do prazo nunca é tocado")
    recente = semear_laudo(user.id, idade_dias=30)
    r = purge.expurgar_laudos(simular=False)
    checar(existe(recente), "laudo de 30 dias sobrevive ao expurgo")
    checar(r.excluidos == 0, "nenhuma exclusão com todos dentro do prazo")

    print("\n3. Simulação é o padrão")
    antigo = semear_laudo(user.id, idade_dias=anos21)
    r = purge.expurgar_laudos()  # simular=True por omissão
    checar(r.vencidos == 1, "laudo de 21 anos é reconhecido como vencido")
    checar(r.excluidos == 0 and existe(antigo),
           "simulação não apaga: o laudo continua no banco")

    print("\n4. Retenção legal protege mesmo vencido")
    retido = semear_laudo(user.id, idade_dias=anos21, hold=True)
    r = purge.expurgar_laudos(simular=False)
    checar(existe(retido), "laudo sob retenção legal sobrevive ao expurgo")
    checar(r.retidos >= 1, "resultado reporta o laudo retido")
    checar(not existe(antigo), "laudo vencido sem retenção foi apagado")

    print("\n5. Exclusão fica registrada")
    with transacao() as conn:
        reg = conn.execute(
            "SELECT categoria, referencia, politica_dias FROM deletions"
            " WHERE referencia = ?", (antigo,)).fetchone()
    checar(reg is not None, "exclusão do laudo foi registrada em `deletions`")
    if reg:
        checar(reg["categoria"] == "laudos"
               and reg["politica_dias"] == retention.PISO_LEGAL_LAUDOS_DIAS,
               "registro guarda categoria e prazo aplicado")
    with transacao() as conn:
        vazou = conn.execute(
            "SELECT COUNT(*) AS n FROM deletions WHERE referencia LIKE '%summary%'"
        ).fetchone()["n"]
    checar(vazou == 0, "registro de exclusão não guarda conteúdo clínico")

    print("\n6. Dado apagado sai do disco, não só das consultas")
    # Semeia, guarda o texto cifrado gravado, apaga e confere que o blob deixou
    # o arquivo: sem VACUUM, o SQLite manteria o conteúdo em páginas liberadas.
    efemero = semear_laudo(user.id, idade_dias=anos21)
    with transacao() as conn:
        blob = conn.execute("SELECT payload_enc FROM analyses WHERE id = ?",
                            (efemero,)).fetchone()["payload_enc"]
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    checar(blob.encode() in DB_PATH.read_bytes(),
           "antes do expurgo, o registro cifrado está no arquivo")

    purge.expurgar_tudo(simular=False, categorias=["laudos"])
    checar(not existe(efemero), "laudo não é mais recuperável por consulta")
    checar(blob.encode() not in DB_PATH.read_bytes(),
           "registro cifrado saiu do arquivo em disco (VACUUM aplicado)")

    print("\n7. Retenção legal pode ser liberada")
    checar(purge.liberar_retencao(retido), "retenção legal removida")
    r = purge.expurgar_laudos(simular=False)
    checar(not existe(retido), "após liberar, o laudo vencido é apagado")

    print("\n8. Expurgo de dado operacional")
    with transacao() as conn:
        conn.execute("INSERT INTO login_attempts (email, ts, ip) VALUES (?,?,?)",
                     ("velho@teste.local",
                      (datetime.now(timezone.utc) - timedelta(days=3)).timestamp(),
                      "10.0.0.1"))
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at)"
            " VALUES (?,?,?,?)",
            (f"hash-teste-{sufixo}", user.id,
             _iso(datetime.now(timezone.utc) - timedelta(days=5)),
             _iso(datetime.now(timezone.utc) - timedelta(days=4))))
    rt = purge.expurgar_tentativas(simular=False)
    rs = purge.expurgar_sessoes(simular=False)
    checar(rt.excluidos >= 1, "tentativa de login antiga expurgada")
    checar(rs.excluidos >= 1, "sessão expirada há dias expurgada")

    print("\n9. Trilha de auditoria respeita a data")
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    arq = AUDIT_DIR / "999912.jsonl"
    antiga = _iso(datetime.now(timezone.utc) - timedelta(days=anos21))
    nova = _iso(datetime.now(timezone.utc))
    arq.write_text(
        json.dumps({"ts": antiga, "analysis_id": "velho", "dados": crypto.cifrar("x")}) + "\n"
        + json.dumps({"ts": nova, "analysis_id": "novo", "dados": crypto.cifrar("y")}) + "\n",
        encoding="utf-8")
    ra = purge.expurgar_auditoria(simular=False)
    restante = arq.read_text(encoding="utf-8") if arq.exists() else ""
    checar(ra.excluidos >= 1, "registro de auditoria de 21 anos expurgado")
    checar('"novo"' in restante and '"velho"' not in restante,
           "registro recente preservado, antigo removido")
    arq.unlink(missing_ok=True)

    print("\n10. Backups em texto puro são tratados como risco")
    falso = DB_PATH.parent / "cardiolaudo.pre-cifragem-19990101000000.db"
    falso.write_bytes(b"conteudo em texto puro de teste")
    antigo_ts = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
    os.utime(falso, (antigo_ts, antigo_ts))
    rb = purge.expurgar_backups(simular=True)
    checar(rb.vencidos >= 1 and falso.exists(), "simulação identifica sem apagar")
    rb = purge.expurgar_backups(simular=False)
    checar(rb.excluidos >= 1 and not falso.exists(), "backup vencido é apagado")

    service.desativar_usuario(email)
    with transacao() as conn:
        conn.execute("DELETE FROM analyses WHERE user_id = ?", (user.id,))
        conn.execute("DELETE FROM login_attempts")

    print(f"\n{'TODOS OS TESTES DE RETENÇÃO PASSARAM' if not falhas else f'{len(falhas)} FALHA(S): ' + '; '.join(falhas)}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

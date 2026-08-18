"""Execução do expurgo conforme a política de retenção.

Toda operação aceita `simular=True` (padrão): levanta o que seria apagado sem
tocar em nada. A exclusão efetiva de laudo é registrada em `deletions` — apagar
prontuário sem deixar rastro destruiria a rastreabilidade que a guarda pretende
assegurar.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import retention
from .auth.db import DB_PATH, connect, transacao
from .reporting.report import AUDIT_DIR
from .retention import backups_texto_puro, corte, politica


@dataclass
class Resultado:
    categoria: str
    rotulo: str
    vencidos: int = 0
    excluidos: int = 0
    retidos: int = 0          # protegidos por retenção legal
    detalhes: list[str] = field(default_factory=list)
    bloqueado: str | None = None   # motivo do disjuntor, se disparou


class DisjuntorAberto(Exception):
    """Volume de exclusão implausível: o expurgo automático foi interrompido."""


def avaliar_disjuntor(a_excluir: int, total: int) -> str | None:
    """Verifica se o volume a apagar é compatível com envelhecimento natural.

    Retorna o motivo do bloqueio, ou None se o expurgo pode prosseguir. Só se
    aplica ao expurgo automático: a execução manual tem um humano decidindo.
    """
    if a_excluir <= 0:
        return None

    teto = retention.limite_absoluto()
    if a_excluir > teto:
        return (f"{a_excluir} laudos vencidos de uma vez excedem o limite de "
                f"{teto} por execução automática. Um expurgo legítimo remove "
                "poucos registros por vez; volume alto sugere prazo mal "
                "configurado, relógio do servidor errado ou data corrompida.")

    fracao = retention.limite_proporcional()
    piso = retention.piso_proporcional()
    # O teto proporcional nunca fica abaixo do piso: em acervo pequeno, alguns
    # laudos completando 20 anos já passariam de 10% sem que haja anomalia.
    teto_proporcional = max(piso, int(total * fracao))
    if total and a_excluir > teto_proporcional:
        return (f"{a_excluir} de {total} laudos ({a_excluir / total:.0%} do "
                f"acervo) excedem o limite de {fracao:.0%} (mínimo de {piso}) "
                "por execução automática. Verifique "
                "CARDIOLAUDO_RETENCAO_LAUDOS_DIAS e a data do servidor antes "
                "de prosseguir manualmente.")
    return None


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _registrar(conn, categoria: str, referencia: str | None,
               criado_em: str | None, dias: int, executor: str) -> None:
    conn.execute(
        "INSERT INTO deletions (categoria, referencia, criado_em, excluido_em,"
        " politica_dias, executor) VALUES (?,?,?,?,?,?)",
        (categoria, referencia, criado_em,
         _iso(datetime.now(timezone.utc)), dias, executor))


def expurgar_laudos(simular: bool = True, executor: str = "cli",
                    automatico: bool = False) -> Resultado:
    pol = politica("laudos")
    limite = _iso(corte("laudos"))
    r = Resultado("laudos", pol.rotulo)

    with transacao() as conn:
        vencidos = conn.execute(
            "SELECT id, created_at, legal_hold, legal_hold_motivo FROM analyses"
            " WHERE created_at < ?", (limite,)).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM analyses").fetchone()["n"]
        r.vencidos = len(vencidos)
        alvos = [x for x in vencidos if not x["legal_hold"]]
        r.retidos = len(vencidos) - len(alvos)

        for x in vencidos:
            if x["legal_hold"]:
                r.detalhes.append(
                    f"{x['id']} ({x['created_at']}) RETIDO — "
                    f"{x['legal_hold_motivo'] or 'retenção legal'}")
            else:
                r.detalhes.append(f"{x['id']} ({x['created_at']}) vencido")

        # O disjuntor vale só para a execução automática: no modo manual há um
        # humano vendo a prévia e digitando a confirmação.
        if automatico:
            motivo = avaliar_disjuntor(len(alvos), int(total))
            if motivo:
                r.bloqueado = motivo
                return r

        if not simular:
            for x in alvos:
                # O registro da exclusão é gravado ANTES da remoção, na mesma
                # transação: se algo falhar, ou os dois efeitos ocorrem ou nenhum.
                _registrar(conn, "laudos", x["id"], x["created_at"],
                           pol.dias, executor)
                conn.execute("DELETE FROM analyses WHERE id = ?", (x["id"],))
            r.excluidos = len(alvos)
    return r


def expurgar_auditoria(simular: bool = True, executor: str = "cli") -> Resultado:
    pol = politica("auditoria")
    limite = corte("auditoria")
    r = Resultado("auditoria", pol.rotulo)
    if not AUDIT_DIR.exists():
        return r

    for arq in sorted(AUDIT_DIR.glob("*.jsonl")):
        mantidas: list[str] = []
        removidas = 0
        for linha in arq.read_text(encoding="utf-8").splitlines():
            if not linha.strip():
                continue
            try:
                obj = json.loads(linha)
                ts = datetime.fromisoformat(obj.get("ts", ""))
            except (json.JSONDecodeError, ValueError):
                mantidas.append(linha)   # ilegível: preservado, nunca descartado
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < limite:
                removidas += 1
            else:
                mantidas.append(linha)

        if removidas:
            r.vencidos += removidas
            r.detalhes.append(f"{arq.name}: {removidas} registro(s) vencido(s)")
            if not simular:
                if mantidas:
                    arq.write_text("\n".join(mantidas) + "\n", encoding="utf-8")
                else:
                    arq.unlink()
                    r.detalhes.append(f"{arq.name}: arquivo removido (ficou vazio)")
                r.excluidos += removidas

    if r.excluidos and not simular:
        with transacao() as conn:
            _registrar(conn, "auditoria", f"{r.excluidos} registro(s)",
                       None, pol.dias, executor)
    return r


def expurgar_sessoes(simular: bool = True, executor: str = "cli") -> Resultado:
    pol = politica("sessoes")
    limite = _iso(corte("sessoes"))
    r = Resultado("sessoes", pol.rotulo)
    with transacao() as conn:
        # Sessões já expiradas há mais tempo que a política.
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE expires_at < ?",
            (limite,)).fetchone()["n"]
        r.vencidos = int(n)
        if n and not simular:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (limite,))
            r.excluidos = int(n)
    return r


def expurgar_tentativas(simular: bool = True, executor: str = "cli") -> Resultado:
    pol = politica("tentativas")
    limite = corte("tentativas").timestamp()
    r = Resultado("tentativas", pol.rotulo)
    with transacao() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM login_attempts WHERE ts < ?",
                         (limite,)).fetchone()["n"]
        r.vencidos = int(n)
        if n and not simular:
            conn.execute("DELETE FROM login_attempts WHERE ts < ?", (limite,))
            r.excluidos = int(n)
    return r


def expurgar_backups(simular: bool = True, executor: str = "cli") -> Resultado:
    pol = politica("backups")
    limite = corte("backups")
    r = Resultado("backups", pol.rotulo)
    for arq in backups_texto_puro(DB_PATH.parent):
        modificado = datetime.fromtimestamp(arq.stat().st_mtime, tz=timezone.utc)
        if modificado < limite:
            r.vencidos += 1
            r.detalhes.append(f"{arq.name} ({modificado:%Y-%m-%d}) — TEXTO PURO")
            if not simular:
                arq.unlink()
                r.excluidos += 1
    if r.excluidos and not simular:
        with transacao() as conn:
            _registrar(conn, "backups", f"{r.excluidos} arquivo(s)",
                       None, pol.dias, executor)
    return r


EXPURGOS = {
    "laudos": expurgar_laudos,
    "auditoria": expurgar_auditoria,
    "sessoes": expurgar_sessoes,
    "tentativas": expurgar_tentativas,
    "backups": expurgar_backups,
}


def expurgar_tudo(simular: bool = True, executor: str = "cli",
                  categorias: list[str] | None = None,
                  automatico: bool = False) -> list[Resultado]:
    escolhidas = categorias or list(EXPURGOS)
    resultados = []
    for c in escolhidas:
        if c not in EXPURGOS:
            continue
        if c == "laudos":
            resultados.append(expurgar_laudos(simular=simular, executor=executor,
                                              automatico=automatico))
        else:
            resultados.append(EXPURGOS[c](simular=simular, executor=executor))

    # Sem VACUUM o conteúdo apagado continua nas páginas liberadas do arquivo:
    # o registro sairia das consultas mas não do disco.
    if not simular and any(r.excluidos for r in resultados):
        conn = connect()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.isolation_level = None
            conn.execute("VACUUM")
        finally:
            conn.close()
    return resultados


def marcar_retencao(analysis_id: str, motivo: str) -> bool:
    with transacao() as conn:
        cur = conn.execute(
            "UPDATE analyses SET legal_hold = 1, legal_hold_motivo = ? WHERE id = ?",
            (motivo.strip() or "retenção legal", analysis_id))
        return cur.rowcount > 0


def liberar_retencao(analysis_id: str) -> bool:
    with transacao() as conn:
        cur = conn.execute(
            "UPDATE analyses SET legal_hold = 0, legal_hold_motivo = NULL"
            " WHERE id = ?", (analysis_id,))
        return cur.rowcount > 0


def inventario() -> dict:
    """Panorama do que existe e do que está vencido, sem apagar nada."""
    with transacao() as conn:
        total_laudos = conn.execute("SELECT COUNT(*) AS n FROM analyses").fetchone()["n"]
        retidos = conn.execute(
            "SELECT COUNT(*) AS n FROM analyses WHERE legal_hold = 1").fetchone()["n"]
        mais_antigo = conn.execute(
            "SELECT MIN(created_at) AS m FROM analyses").fetchone()["m"]
        exclusoes = conn.execute(
            "SELECT COUNT(*) AS n FROM deletions").fetchone()["n"]
    return {"laudos": int(total_laudos), "retidos": int(retidos),
            "mais_antigo": mais_antigo, "exclusoes_registradas": int(exclusoes),
            "backups_texto_puro": len(backups_texto_puro(Path(DB_PATH).parent))}

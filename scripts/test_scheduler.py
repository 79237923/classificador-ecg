"""Testes do expurgo automático agendado.

O que precisa ser provado é o que o agendador **não** faz: não apaga laudo sem
autorização explícita, e para sozinho quando o volume indica configuração
errada em vez de envelhecimento natural.

Uso: .venv\\Scripts\\python scripts\\test_scheduler.py
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import purge, retention, scheduler  # noqa: E402
from backend.app.auth import crypto, service  # noqa: E402
from backend.app.auth.db import init_db, transacao  # noqa: E402

falhas: list[str] = []


def checar(cond: bool, desc: str) -> None:
    print(f"  [{'OK ' if cond else 'FALHA'}] {desc}")
    if not cond:
        falhas.append(desc)


def semear(user_id: int, idade_dias: int, n: int = 1) -> list[str]:
    ids = []
    with transacao() as conn:
        for _ in range(n):
            aid = uuid.uuid4().hex[:16]
            criado = (datetime.now(timezone.utc)
                      - timedelta(days=idade_dias)).isoformat(timespec="seconds")
            conn.execute(
                "INSERT INTO analyses (id, user_id, created_at, payload_enc,"
                " legal_hold) VALUES (?,?,?,?,0)",
                (aid, user_id, criado,
                 crypto.cifrar(json.dumps({"analysis_id": aid, "summary": "teste"}))))
            ids.append(aid)
    return ids


def existem(ids: list[str]) -> int:
    with transacao() as conn:
        marcas = ",".join("?" * len(ids))
        return conn.execute(
            f"SELECT COUNT(*) AS n FROM analyses WHERE id IN ({marcas})",
            ids).fetchone()["n"]


def limpar(user_id: int) -> None:
    with transacao() as conn:
        conn.execute("DELETE FROM analyses WHERE user_id = ?", (user_id,))


def main() -> int:
    init_db()
    sufixo = uuid.uuid4().hex[:8]
    email = f"agenda.{sufixo}@teste.local"
    user = service.criar_usuario(email, f"Dr. Agenda {sufixo}",
                                 "AgendaTeste2026", "CRM-AG")
    anos21 = int(21 * retention.DIAS_POR_ANO)

    for var in ("CARDIOLAUDO_EXPURGO_AUTOMATICO_LAUDOS",
                "CARDIOLAUDO_EXPURGO_LIMITE",
                "CARDIOLAUDO_EXPURGO_LIMITE_FRACAO",
                "CARDIOLAUDO_EXPURGO_PISO",
                "CARDIOLAUDO_EXPURGO_AUTOMATICO"):
        os.environ.pop(var, None)

    print("\n1. Padrão seguro: laudos não são apagados automaticamente")
    checar(not retention.expurgo_automatico_laudos(),
           "expurgo automático de laudos vem desligado")
    antigos = semear(user.id, anos21, n=3)
    scheduler.executar_ciclo(executor="teste")
    checar(existem(antigos) == 3,
           "laudos vencidos sobrevivem ao ciclo automático padrão")

    print("\n2. Dado operacional é expurgado sem intervenção")
    with transacao() as conn:
        conn.execute("INSERT INTO login_attempts (email, ts, ip) VALUES (?,?,?)",
                     ("antigo@teste.local",
                      (datetime.now(timezone.utc) - timedelta(days=3)).timestamp(),
                      "10.0.0.9"))
    scheduler.executar_ciclo(executor="teste")
    with transacao() as conn:
        restou = conn.execute(
            "SELECT COUNT(*) AS n FROM login_attempts WHERE email = ?",
            ("antigo@teste.local",)).fetchone()["n"]
    checar(restou == 0, "tentativa de login antiga é expurgada automaticamente")

    print("\n3. Com autorização explícita, os laudos vencidos são apagados")
    os.environ["CARDIOLAUDO_EXPURGO_AUTOMATICO_LAUDOS"] = "1"
    checar(retention.expurgo_automatico_laudos(), "flag reconhecida")
    scheduler.executar_ciclo(executor="teste")
    checar(existem(antigos) == 0, "laudos vencidos apagados com a flag ligada")

    print("\n4. Disjuntor interrompe exclusão em massa (limite absoluto)")
    os.environ["CARDIOLAUDO_EXPURGO_LIMITE"] = "5"
    os.environ["CARDIOLAUDO_EXPURGO_LIMITE_FRACAO"] = "1.0"
    muitos = semear(user.id, anos21, n=12)
    resultados = scheduler.executar_ciclo(executor="teste")
    laudos = next(r for r in resultados if r.categoria == "laudos")
    checar(laudos.bloqueado is not None, "disjuntor disparou")
    checar("limite de 5" in (laudos.bloqueado or ""),
           "motivo cita o limite configurado")
    checar(existem(muitos) == 12, "NENHUM laudo foi apagado com o disjuntor aberto")
    checar(laudos.excluidos == 0, "resultado reporta zero exclusões")

    print("\n4b. Piso evita alarme falso em acervo pequeno")
    # Poucos laudos vencendo num acervo pequeno ultrapassam 10% sem que haja
    # anomalia; o piso precisa tolerar isso, senão o alarme perde o sentido.
    os.environ["CARDIOLAUDO_EXPURGO_LIMITE"] = "50"
    os.environ["CARDIOLAUDO_EXPURGO_LIMITE_FRACAO"] = "0.10"
    checar(purge.avaliar_disjuntor(3, 18) is None,
           "3 de 18 laudos (17%) é tolerado pelo piso mínimo de 5")
    checar(purge.avaliar_disjuntor(6, 18) is not None,
           "6 de 18 já ultrapassa o piso e dispara o disjuntor")
    checar(purge.avaliar_disjuntor(30, 1000) is None,
           "30 de 1000 (3%) passa normalmente em acervo grande")
    checar(purge.avaliar_disjuntor(200, 1000) is not None,
           "200 de 1000 (20%) dispara em acervo grande")

    print("\n5. Disjuntor proporcional protege acervo pequeno")
    os.environ["CARDIOLAUDO_EXPURGO_LIMITE"] = "1000"
    os.environ["CARDIOLAUDO_EXPURGO_LIMITE_FRACAO"] = "0.10"
    resultados = scheduler.executar_ciclo(executor="teste")
    laudos = next(r for r in resultados if r.categoria == "laudos")
    checar(laudos.bloqueado is not None and "do acervo" in laudos.bloqueado,
           "fração do acervo dispara o disjuntor")
    checar(existem(muitos) == 12, "acervo preservado")

    print("\n6. Dentro dos limites, o expurgo prossegue")
    os.environ["CARDIOLAUDO_EXPURGO_LIMITE_FRACAO"] = "1.0"
    resultados = scheduler.executar_ciclo(executor="teste")
    laudos = next(r for r in resultados if r.categoria == "laudos")
    checar(laudos.bloqueado is None, "disjuntor não dispara dentro dos limites")
    checar(existem(muitos) == 0, "laudos vencidos apagados")

    print("\n7. Retenção legal vence o expurgo automático")
    retido = semear(user.id, anos21, n=1)[0]
    purge.marcar_retencao(retido, "processo em curso")
    scheduler.executar_ciclo(executor="teste")
    checar(existem([retido]) == 1,
           "laudo sob retenção legal sobrevive ao ciclo automático")
    purge.liberar_retencao(retido)

    print("\n8. Execução manual não é limitada pelo disjuntor")
    os.environ["CARDIOLAUDO_EXPURGO_LIMITE"] = "1"
    lote = semear(user.id, anos21, n=6)
    r = purge.expurgar_laudos(simular=False, executor="teste")  # automatico=False
    checar(r.bloqueado is None,
           "modo manual ignora o disjuntor (há um humano confirmando)")
    checar(existem(lote) == 0, "expurgo manual apaga o lote inteiro")

    print("\n9. Cálculo do próximo horário")
    base = datetime(2026, 8, 18, 10, 0, 0)
    checar(scheduler._proxima_execucao(base, 3).day == 19,
           "hora já passada hoje agenda para amanhã")
    checar(scheduler._proxima_execucao(base, 15).day == 18,
           "hora ainda por vir agenda para hoje")
    checar(scheduler._proxima_execucao(base, 15).hour == 15, "horário correto")

    print("\n10. Agendador pode ser desligado por completo")
    os.environ["CARDIOLAUDO_EXPURGO_AUTOMATICO"] = "0"
    checar(not retention.expurgo_automatico_ativo(),
           "CARDIOLAUDO_EXPURGO_AUTOMATICO=0 desliga o agendador")

    for var in ("CARDIOLAUDO_EXPURGO_AUTOMATICO_LAUDOS",
                "CARDIOLAUDO_EXPURGO_LIMITE",
                "CARDIOLAUDO_EXPURGO_LIMITE_FRACAO",
                "CARDIOLAUDO_EXPURGO_PISO",
                "CARDIOLAUDO_EXPURGO_AUTOMATICO"):
        os.environ.pop(var, None)
    limpar(user.id)
    service.desativar_usuario(email)

    print(f"\n{'TODOS OS TESTES DO AGENDADOR PASSARAM' if not falhas else f'{len(falhas)} FALHA(S): ' + '; '.join(falhas)}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

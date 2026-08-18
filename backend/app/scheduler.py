"""Agendador do expurgo automático.

Executa uma vez por dia, em horário de baixo movimento, **em duas camadas**:

- **Dado operacional** (sessões expiradas, tentativas de login, backups em texto
  puro vencidos): sempre automático. Nenhum deles tem valor clínico e todos se
  refazem sozinhos; deixá-los acumular é que seria o risco.

- **Laudos**: só com `CARDIOLAUDO_EXPURGO_AUTOMATICO_LAUDOS=1`. Desligado por
  padrão porque um prontuário apagado por engano não volta. Mesmo ligado, passa
  antes pelo disjuntor de `purge.avaliar_disjuntor`, que interrompe a execução
  quando o volume não corresponde a envelhecimento natural — o sintoma de prazo
  mal configurado ou relógio errado.

Roda dentro do processo do servidor para não depender de configuração externa.
Em implantação com várias instâncias, use o agendador do sistema operacional
apontando para `scripts/manage_retention.py` e desligue este com
`CARDIOLAUDO_EXPURGO_AUTOMATICO=0`, evitando execuções concorrentes.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from . import retention
from .purge import expurgar_tudo

logger = logging.getLogger("cardiolaudo.expurgo")

CATEGORIAS_OPERACIONAIS = ["sessoes", "tentativas", "backups"]


def _proxima_execucao(agora: datetime, hora: int) -> datetime:
    alvo = agora.replace(hour=hora, minute=0, second=0, microsecond=0)
    if alvo <= agora:
        alvo += timedelta(days=1)
    return alvo


def executar_ciclo(executor: str = "agendador") -> list:
    """Um ciclo de expurgo. Isolado do laço para poder ser testado direto."""
    categorias = list(CATEGORIAS_OPERACIONAIS)
    if retention.expurgo_automatico_laudos():
        categorias.append("laudos")

    resultados = expurgar_tudo(simular=False, executor=executor,
                               categorias=categorias, automatico=True)

    for r in resultados:
        if r.bloqueado:
            # Nível de erro de propósito: exige atenção humana, e o expurgo de
            # laudos fica parado até alguém verificar a configuração.
            logger.error(
                "EXPURGO DE LAUDOS INTERROMPIDO pelo disjuntor de segurança. %s "
                "Nenhum laudo foi apagado. Verifique com "
                "`python scripts/manage_retention.py status`.", r.bloqueado)
        elif r.excluidos:
            logger.info("Expurgo automático: %d registro(s) de %s.",
                        r.excluidos, r.rotulo)
        if r.retidos:
            logger.info("Expurgo automático: %d laudo(s) preservado(s) por "
                        "retenção legal.", r.retidos)
    return resultados


async def laco_expurgo() -> None:
    hora = retention.hora_do_expurgo()
    logger.info(
        "Expurgo automático ativo: diariamente às %02d:00 (dado operacional%s).",
        hora,
        " + laudos" if retention.expurgo_automatico_laudos()
        else "; laudos exigem comando manual")

    while True:
        agora = datetime.now().astimezone()
        espera = (_proxima_execucao(agora, hora) - agora).total_seconds()
        try:
            await asyncio.sleep(espera)
        except asyncio.CancelledError:
            logger.info("Agendador de expurgo encerrado.")
            raise

        try:
            await asyncio.to_thread(executar_ciclo)
        except Exception:
            # Uma falha no expurgo não pode derrubar o agendador: ele precisa
            # continuar tentando nos dias seguintes.
            logger.exception("Falha no ciclo de expurgo automático")


def iniciar(app) -> None:
    if not retention.expurgo_automatico_ativo():
        logger.info("Expurgo automático desligado "
                    "(CARDIOLAUDO_EXPURGO_AUTOMATICO=0).")
        return
    app.state.tarefa_expurgo = asyncio.create_task(laco_expurgo())


async def encerrar(app) -> None:
    tarefa = getattr(app.state, "tarefa_expurgo", None)
    if tarefa:
        tarefa.cancel()
        try:
            await tarefa
        except asyncio.CancelledError:
            pass

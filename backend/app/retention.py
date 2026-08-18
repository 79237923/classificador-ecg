"""Política de retenção e expurgo.

**O ponto central**: em software médico, apagar cedo demais é tão irregular
quanto guardar para sempre. A Resolução CFM 1.821/2007 exige guarda do
prontuário por no mínimo 20 anos contados do último registro, e a LGPD
(Art. 16, I) ressalva justamente o cumprimento de obrigação legal de guarda ao
tratar da eliminação. Por isso o dado clínico tem prazo longo e protegido,
enquanto o dado meramente operacional é descartado em dias.

Categorias e prazos padrão:

| Categoria            | Prazo   | Por quê |
|----------------------|---------|---------|
| Laudos               | 20 anos | CFM 1.821/2007 — mínimo legal do prontuário |
| Trilha de auditoria  | 20 anos | Acompanha o laudo: sem ela não há rastreabilidade |
| Sessões expiradas    | 1 dia   | Sem valor após expirar |
| Tentativas de login  | 1 dia   | Só servem à janela de proteção contra força bruta |
| Backups pré-cifragem | 7 dias  | Estão em TEXTO PURO — é o item mais perigoso em disco |

Três salvaguardas contra apagar o que não se deve:

1. **Piso legal**: configurar os laudos abaixo de 20 anos exige assumir
   explicitamente a decisão (`CARDIOLAUDO_RETENCAO_ACEITA_RISCO=1`). O padrão
   nunca apaga um exame dentro do prazo do prontuário.
2. **Retenção legal** (*legal hold*): um laudo marcado fica imune ao expurgo,
   independentemente da idade — necessário em litígio ou auditoria em curso.
3. **Simulação por padrão**: o expurgo só executa com `--confirmar`. Sem isso,
   apenas relata o que seria apagado.

A própria exclusão é registrada: apagar um prontuário sem deixar rastro
destruiria a rastreabilidade que a guarda pretende garantir.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DIAS_POR_ANO = 365.25

# Piso legal do prontuário médico (CFM 1.821/2007, art. 8º).
PISO_LEGAL_LAUDOS_DIAS = int(20 * DIAS_POR_ANO)


@dataclass(frozen=True)
class Politica:
    chave: str
    rotulo: str
    dias: int
    fundamento: str


def _dias(env: str, padrao: int) -> int:
    bruto = os.getenv(env, "").strip()
    if not bruto:
        return padrao
    try:
        valor = int(bruto)
    except ValueError:
        raise ValueError(f"{env} deve ser um número inteiro de dias.")
    if valor < 1:
        raise ValueError(f"{env} deve ser de ao menos 1 dia.")
    return valor


def aceita_risco() -> bool:
    return os.getenv("CARDIOLAUDO_RETENCAO_ACEITA_RISCO", "0") == "1"


# ---------------------------------------------------------------- agendamento
def expurgo_automatico_ativo() -> bool:
    """Se o agendador roda sem intervenção humana."""
    return os.getenv("CARDIOLAUDO_EXPURGO_AUTOMATICO", "1") == "1"


def expurgo_automatico_laudos() -> bool:
    """Se o agendador pode apagar PRONTUÁRIO sozinho.

    Desligado por padrão, e de propósito: dado operacional expurgado por engano
    se refaz sozinho; um laudo apagado, não. Ligar isso é uma decisão de quem
    responde pelo acervo clínico, não um padrão de instalação.
    """
    return os.getenv("CARDIOLAUDO_EXPURGO_AUTOMATICO_LAUDOS", "0") == "1"


def hora_do_expurgo() -> int:
    """Hora local (0–23) da execução diária. Padrão: 3h, fora do expediente."""
    try:
        h = int(os.getenv("CARDIOLAUDO_EXPURGO_HORA", "3"))
    except ValueError:
        return 3
    return h if 0 <= h <= 23 else 3


# --- Disjuntor: limites que interrompem o expurgo automático de laudos -------
# Um expurgo legítimo remove poucos registros por vez — os que acabaram de
# completar 20 anos. Um volume grande não é envelhecimento natural: é sinal de
# prazo mal configurado, relógio errado ou data corrompida. Nesses casos o
# agendador precisa PARAR e chamar um humano, não seguir apagando.
def limite_absoluto() -> int:
    try:
        return max(1, int(os.getenv("CARDIOLAUDO_EXPURGO_LIMITE", "50")))
    except ValueError:
        return 50


def limite_proporcional() -> float:
    """Fração máxima do acervo que um expurgo automático pode remover."""
    try:
        v = float(os.getenv("CARDIOLAUDO_EXPURGO_LIMITE_FRACAO", "0.10"))
    except ValueError:
        return 0.10
    return min(max(v, 0.001), 1.0)


def piso_proporcional() -> int:
    """Quantidade sempre tolerada, por menor que seja o acervo.

    Sem esse piso, uma clínica com poucas dezenas de exames dispararia o
    disjuntor no envelhecimento normal — dois ou três laudos completando 20 anos
    já ultrapassam 10% de um acervo pequeno. Alarme que toca sem motivo é
    ignorado, e aí deixa de proteger quando importa.
    """
    try:
        return max(1, int(os.getenv("CARDIOLAUDO_EXPURGO_PISO", "5")))
    except ValueError:
        return 5


def politicas() -> list[Politica]:
    laudos = _dias("CARDIOLAUDO_RETENCAO_LAUDOS_DIAS", PISO_LEGAL_LAUDOS_DIAS)
    if laudos < PISO_LEGAL_LAUDOS_DIAS and not aceita_risco():
        raise ValueError(
            f"Retenção de laudos configurada em {laudos} dias, abaixo do mínimo "
            f"de {PISO_LEGAL_LAUDOS_DIAS} dias (20 anos) da Resolução CFM "
            "1.821/2007. Apagar prontuário antes desse prazo é irregular. "
            "Para assumir essa decisão de forma explícita, defina "
            "CARDIOLAUDO_RETENCAO_ACEITA_RISCO=1.")

    auditoria = _dias("CARDIOLAUDO_RETENCAO_AUDITORIA_DIAS", PISO_LEGAL_LAUDOS_DIAS)
    return [
        Politica("laudos", "Laudos de ECG", laudos,
                 "CFM 1.821/2007 art. 8º — guarda mínima do prontuário; "
                 "LGPD art. 16, I — obrigação legal de guarda"),
        Politica("auditoria", "Trilha de auditoria", auditoria,
                 "Acompanha o laudo: rastreabilidade de quem gerou cada análise"),
        Politica("sessoes", "Sessões expiradas",
                 _dias("CARDIOLAUDO_RETENCAO_SESSOES_DIAS", 1),
                 "LGPD art. 15, I — término do tratamento; sem utilidade após expirar"),
        Politica("tentativas", "Tentativas de login",
                 _dias("CARDIOLAUDO_RETENCAO_TENTATIVAS_DIAS", 1),
                 "Só servem à janela de proteção contra força bruta (15 min)"),
        Politica("backups", "Backups anteriores à cifragem",
                 _dias("CARDIOLAUDO_RETENCAO_BACKUPS_DIAS", 7),
                 "Estão em TEXTO PURO — LGPD art. 46, dever de segurança"),
    ]


def politica(chave: str) -> Politica:
    for p in politicas():
        if p.chave == chave:
            return p
    raise KeyError(chave)


def corte(chave: str) -> datetime:
    """Data-limite: registros anteriores a ela estão vencidos."""
    return datetime.now(timezone.utc) - timedelta(days=politica(chave).dias)


def humanizar(dias: int) -> str:
    if dias >= DIAS_POR_ANO:
        anos = dias / DIAS_POR_ANO
        return f"{anos:.0f} ano(s)" if anos >= 1 else f"{dias} dia(s)"
    return f"{dias} dia(s)"


def backups_texto_puro(data_dir: Path) -> list[Path]:
    return sorted(data_dir.glob("cardiolaudo.pre-cifragem-*.db"))

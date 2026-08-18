"""Semeia a conta inicial na inicialização.

Hospedagens gratuitas costumam ter disco efêmero: o banco some a cada reinício
ou novo deploy. Sem isto, o sistema voltaria ao ar sem nenhuma conta e ninguém
conseguiria entrar — inclusive o administrador.

Só age quando NÃO existe nenhuma conta. Numa instalação com disco persistente
roda uma única vez, na primeira subida, e depois nunca mais interfere.

Configuração (variáveis de ambiente):
    CARDIOLAUDO_ADMIN_EMAIL   e-mail da conta administradora
    CARDIOLAUDO_ADMIN_SENHA   senha (mín. 12 caracteres, letras e números)
    CARDIOLAUDO_ADMIN_NOME    nome exibido (opcional)
"""
from __future__ import annotations

import logging
import os

from .auth import service
from .auth.db import transacao

logger = logging.getLogger("cardiolaudo")


def semear_admin() -> None:
    email = os.getenv("CARDIOLAUDO_ADMIN_EMAIL", "").strip()
    senha = os.getenv("CARDIOLAUDO_ADMIN_SENHA", "")
    nome = os.getenv("CARDIOLAUDO_ADMIN_NOME", "Administrador").strip()

    if not email or not senha:
        return

    with transacao() as conn:
        ja_tem = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if ja_tem:
        return

    try:
        service.criar_usuario(email, nome or "Administrador", senha, role="admin")
    except service.AuthError as exc:
        # Senha fraca ou e-mail inválido: falha ruidosa, senão o serviço subiria
        # sem nenhuma conta e o problema só apareceria na hora de entrar.
        logger.error("Não foi possível semear a conta administradora: %s", exc)
        return

    logger.info("Conta administradora criada a partir do ambiente: %s", email)

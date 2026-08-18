"""Primitivas de senha e token.

Senhas: **scrypt** (biblioteca padrão do Python). É uma função de derivação
memória-dura, resistente a ataque por GPU/ASIC — diferente de SHA/PBKDF2, cujo
custo é apenas computacional. Parâmetros seguem a recomendação de custo
interativo (n=2^15, r=8, p=1).

Sessões: tokens opacos aleatórios, guardados no banco **apenas como hash**.
Escolhidos em vez de JWT porque em ambiente clínico a revogação imediata é
requisito: desligar um profissional precisa invalidar o acesso na hora, o que
um JWT autocontido não permite sem uma lista de revogação — que reintroduz o
estado que o JWT prometia eliminar.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

# n=2^17 segue a recomendação atual da OWASP para scrypt (o valor anterior,
# 2^15, está abaixo do mínimo sugerido). maxmem é dimensionado para acomodar
# o custo: 128 * n * r ≈ 134 MB, com folga.
SCRYPT_N = 2 ** 17
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 192 * 1024 * 1024
KEY_LEN = 64
SALT_BYTES = 16
TOKEN_BYTES = 32

MIN_PASSWORD_LEN = 12


def gerar_salt() -> bytes:
    return secrets.token_bytes(SALT_BYTES)


def hash_senha(senha: str, salt: bytes) -> str:
    dk = hashlib.scrypt(senha.encode("utf-8"), salt=salt,
                        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_LEN,
                        maxmem=SCRYPT_MAXMEM)
    return dk.hex()


def conferir_senha(senha: str, salt: bytes, hash_esperado: str) -> bool:
    # compare_digest evita vazar informação pelo tempo de comparação.
    return hmac.compare_digest(hash_senha(senha, salt), hash_esperado)


def validar_forca_senha(senha: str) -> str | None:
    """Retorna mensagem de erro, ou None se a senha for aceitável."""
    if len(senha) < MIN_PASSWORD_LEN:
        return f"A senha deve ter ao menos {MIN_PASSWORD_LEN} caracteres."
    if senha.isdigit() or senha.isalpha():
        return "A senha deve combinar letras e números."
    return None


def gerar_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Token é guardado só como hash: um vazamento do banco não concede sessão.

    SHA-256 puro é adequado aqui (ao contrário de senhas): o token já tem
    entropia alta o suficiente para inviabilizar busca exaustiva.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

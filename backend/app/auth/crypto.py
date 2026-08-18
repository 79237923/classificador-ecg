"""Cifragem em repouso dos dados de saúde.

**Modelo de ameaça**: alguém obtém o arquivo do banco ou a trilha de auditoria —
notebook furtado, backup mal configurado, pasta compartilhada por engano. A
cifragem só ajuda se a chave NÃO estiver no mesmo lugar dos dados; por isso a
chave nunca é gravada dentro de `data/`, que é justamente o diretório que se
copia num backup.

**Algoritmo**: AES-256-GCM. É cifragem autenticada — além de tornar o conteúdo
ilegível, detecta adulteração: um registro de exame alterado no banco falha na
verificação em vez de ser aceito silenciosamente. Cada registro recebe um nonce
aleatório próprio de 96 bits.

**Envelope**: `v1` + nonce(12) + texto cifrado, codificado em base64. O prefixo
de versão permite trocar de algoritmo ou rodar a chave sem ambiguidade sobre
como um registro antigo foi cifrado.

**Busca por e-mail**: o login precisa localizar a conta, e um valor cifrado com
nonce aleatório muda a cada gravação — não serve de chave de busca. Guardamos
então um *índice cego*: HMAC-SHA256 do e-mail normalizado com uma chave
derivada. É determinístico (permite a busca) mas, sem a chave, não permite
testar se um e-mail específico está cadastrado.

**Não cifrado, por necessidade funcional**: identificadores de análise, carimbos
de tempo e o vínculo análise→usuário. São necessários para consulta e controle
de acesso. Isso significa que quem obtiver o banco sabe *quantos* exames existem
e *quando* — mas não de quem nem o conteúdo clínico.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import stat
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[3]
# Fora de `data/` de propósito: um backup do diretório de dados não deve
# carregar junto a chave que o decifra.
KEY_FILE = ROOT / "secrets" / "cardiolaudo.key"
ENV_KEY = "CARDIOLAUDO_KEY"

VERSAO = b"v1"
NONCE_BYTES = 12
KEY_BYTES = 32

_chave: bytes | None = None


class CryptoError(RuntimeError):
    """Falha de configuração ou de decifragem."""


def _modo_dev() -> bool:
    return os.getenv("CARDIOLAUDO_ENV", "producao").lower() == "dev"


def _gerar_key_file() -> bytes:
    chave = secrets.token_bytes(KEY_BYTES)
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(base64.b64encode(chave))
    try:
        if sys.platform == "win32":
            # Remove herança e concede acesso apenas ao dono do processo.
            os.system(f'icacls "{KEY_FILE}" /inheritance:r /grant:r "%USERNAME%":F >nul 2>&1')
        else:
            KEY_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    print(f"[cardiolaudo] chave de cifragem gerada em {KEY_FILE} — "
          "faça backup dela em local seguro e separado do banco. "
          "Sem esta chave os exames já gravados não podem ser lidos.",
          file=sys.stderr)
    return chave


def carregar_chave() -> bytes:
    """Chave mestra: variável de ambiente em produção, arquivo em desenvolvimento."""
    global _chave
    if _chave is not None:
        return _chave

    bruto = os.getenv(ENV_KEY, "").strip()
    if bruto:
        try:
            chave = base64.b64decode(bruto, validate=True)
        except Exception as exc:
            raise CryptoError(
                f"{ENV_KEY} não é base64 válido. Gere uma chave com: "
                "python scripts/manage_keys.py gerar") from exc
        if len(chave) != KEY_BYTES:
            raise CryptoError(
                f"{ENV_KEY} deve ter {KEY_BYTES} bytes ({KEY_BYTES * 8} bits) "
                f"em base64; recebidos {len(chave)}.")
        _chave = chave
        return _chave

    if KEY_FILE.exists():
        _chave = base64.b64decode(KEY_FILE.read_bytes())
        if len(_chave) != KEY_BYTES:
            raise CryptoError(f"Chave inválida em {KEY_FILE}.")
        return _chave

    if not _modo_dev():
        # Gerar chave silenciosamente em produção seria pior que falhar: numa
        # reimplantação sem o arquivo, uma chave nova tornaria ilegível todo o
        # histórico de exames, com aparência de funcionamento normal.
        raise CryptoError(
            f"Nenhuma chave de cifragem configurada. Defina {ENV_KEY} com uma "
            "chave base64 de 32 bytes (gere com "
            "`python scripts/manage_keys.py gerar`) ou rode em "
            "desenvolvimento com CARDIOLAUDO_ENV=dev.")

    _chave = _gerar_key_file()
    return _chave


def _subchave(rotulo: bytes) -> bytes:
    """Deriva chaves distintas por finalidade a partir da chave mestra.

    Separar o material usado para cifrar do usado no índice cego evita que uma
    fraqueza em um dos usos comprometa o outro.
    """
    return hashlib.sha256(carregar_chave() + rotulo).digest()


def cifrar(texto: str | None) -> str | None:
    if texto is None:
        return None
    nonce = secrets.token_bytes(NONCE_BYTES)
    ct = AESGCM(_subchave(b"dados")).encrypt(nonce, texto.encode("utf-8"), VERSAO)
    return base64.b64encode(VERSAO + nonce + ct).decode("ascii")


def decifrar(valor: str | None) -> str | None:
    if valor is None:
        return None
    try:
        bruto = base64.b64decode(valor)
    except Exception as exc:
        raise CryptoError("Registro cifrado ilegível (base64 inválido).") from exc
    if not bruto.startswith(VERSAO):
        raise CryptoError("Registro com versão de cifragem desconhecida.")
    nonce = bruto[len(VERSAO):len(VERSAO) + NONCE_BYTES]
    ct = bruto[len(VERSAO) + NONCE_BYTES:]
    try:
        return AESGCM(_subchave(b"dados")).decrypt(nonce, ct, VERSAO).decode("utf-8")
    except Exception as exc:
        raise CryptoError(
            "Não foi possível decifrar o registro: a chave não corresponde à "
            "usada na gravação, ou o dado foi adulterado.") from exc


def parece_cifrado(valor: str | None) -> bool:
    """Distingue registros já cifrados dos herdados em texto puro (migração)."""
    if not valor:
        return False
    try:
        return base64.b64decode(valor, validate=True).startswith(VERSAO)
    except Exception:
        return False


def indice_cego(valor: str) -> str:
    """HMAC determinístico para busca por e-mail sem expor o e-mail."""
    return hmac.new(_subchave(b"indice"),
                    valor.strip().lower().encode("utf-8"),
                    hashlib.sha256).hexdigest()

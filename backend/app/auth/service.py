"""Regras de negócio de contas, sessões e posse de laudos."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .crypto import cifrar, decifrar, indice_cego
from .db import transacao
from .security import (conferir_senha, gerar_salt, gerar_token, hash_senha,
                       hash_token, validar_forca_senha)

SESSION_TTL_HOURS = 12
JANELA_TENTATIVAS_S = 900  # 15 min

# Estratégia contra força bruta, em duas camadas:
#
# 1. NÃO bloqueamos a conta. Bloquear permitiria que alguém que apenas conhece o
#    e-mail de um médico o mantivesse fora do sistema durante um atendimento,
#    bastando errar a senha periodicamente. O NIST SP 800-63B desaconselha o
#    bloqueio de conta e prescreve limitação progressiva. Quem apresenta a senha
#    correta entra sempre; o custo recai sobre quem erra.
#
# 2. O bloqueio por origem conta CONTAS DISTINTAS atingidas, não o total de
#    falhas. Uma clínica inteira costuma sair por um único IP público: barrar a
#    origem por volume de erros deixaria todos os médicos de fora por causa de
#    alguns enganos de digitação. Já varrer muitas contas diferentes a partir da
#    mesma origem é a assinatura de password spraying, e não acontece por acaso.
MAX_CONTAS_DISTINTAS_IP = 10
MAX_ATRASO_S = 4.0


class AuthError(Exception):
    """Falha de autenticação ou autorização."""


@dataclass(frozen=True)
class User:
    id: int
    email: str
    full_name: str
    professional_id: str | None
    role: str


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# --------------------------------------------------------------- usuários
def criar_usuario(email: str, full_name: str, senha: str,
                  professional_id: str | None = None,
                  role: str = "medico") -> User:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise AuthError("E-mail inválido.")
    if not full_name.strip():
        raise AuthError("Nome completo é obrigatório.")
    if (erro := validar_forca_senha(senha)):
        raise AuthError(erro)

    salt = gerar_salt()
    idx = indice_cego(email)
    with transacao() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email_idx = ?", (idx,)).fetchone():
            raise AuthError("Já existe uma conta com este e-mail.")
        cur = conn.execute(
            "INSERT INTO users (email_idx, email_enc, full_name_enc,"
            " professional_id_enc, password_hash, salt, role, active, created_at)"
            " VALUES (?,?,?,?,?,?,?,1,?)",
            (idx, cifrar(email), cifrar(full_name.strip()),
             cifrar((professional_id or "").strip() or None),
             hash_senha(senha, salt), salt, role, _iso(_agora())))
        return User(int(cur.lastrowid), email, full_name.strip(),
                    professional_id, role)


def desativar_usuario(email: str) -> bool:
    """Desativa a conta e encerra todas as sessões abertas dela."""
    with transacao() as conn:
        row = conn.execute("SELECT id FROM users WHERE email_idx = ?",
                           (indice_cego(email),)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE users SET active = 0 WHERE id = ?", (row["id"],))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        return True


def reativar_usuario(email: str) -> bool:
    with transacao() as conn:
        cur = conn.execute("UPDATE users SET active = 1 WHERE email_idx = ?",
                           (indice_cego(email),))
        return cur.rowcount > 0


def definir_papel(email: str, role: str) -> bool:
    """Define o papel da conta ('medico' ou 'admin'). Usado para promover o
    primeiro administrador — não há como se autopromover pela interface."""
    if role not in ("medico", "admin"):
        raise AuthError("Papel inválido (use 'medico' ou 'admin').")
    with transacao() as conn:
        cur = conn.execute("UPDATE users SET role = ? WHERE email_idx = ?",
                           (role, indice_cego(email)))
        return cur.rowcount > 0


def alterar_senha(user_id: int, senha_atual: str, senha_nova: str,
                  manter_token: str | None = None) -> None:
    """Troca a senha do próprio usuário.

    Exige a senha atual (impede que uma sessão sequestrada troque a senha sem
    conhecê-la) e encerra todas as OUTRAS sessões — se a conta estava
    comprometida, os demais acessos caem. A sessão que fez a troca é mantida.
    """
    if (erro := validar_forca_senha(senha_nova)):
        raise AuthError(erro)
    with transacao() as conn:
        row = conn.execute(
            "SELECT password_hash, salt FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or not conferir_senha(senha_atual, row["salt"], row["password_hash"]):
            raise AuthError("Senha atual incorreta.")
        if conferir_senha(senha_nova, row["salt"], row["password_hash"]):
            raise AuthError("A nova senha deve ser diferente da atual.")
        salt = gerar_salt()
        conn.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                     (hash_senha(senha_nova, salt), salt, user_id))
        if manter_token:
            conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?",
                         (user_id, hash_token(manter_token)))
        else:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def resetar_senha(email: str, senha_nova: str) -> bool:
    """Redefine a senha sem exigir a atual (privilégio de administrador / CLI).

    Encerra todas as sessões da conta: após um reset, ninguém deve seguir
    autenticado com a credencial antiga.
    """
    if (erro := validar_forca_senha(senha_nova)):
        raise AuthError(erro)
    with transacao() as conn:
        row = conn.execute("SELECT id FROM users WHERE email_idx = ?",
                           (indice_cego(email),)).fetchone()
        if not row:
            return False
        salt = gerar_salt()
        conn.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                     (hash_senha(senha_nova, salt), salt, row["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        return True


def listar_usuarios() -> list[dict]:
    """Lista as contas para o painel de administração (dados decifrados)."""
    with transacao() as conn:
        rows = conn.execute(
            "SELECT id, email_enc, full_name_enc, professional_id_enc, role,"
            " active, created_at, last_login_at FROM users ORDER BY created_at"
        ).fetchall()
    return [{"id": r["id"], "email": decifrar(r["email_enc"]),
             "full_name": decifrar(r["full_name_enc"]),
             "professional_id": decifrar(r["professional_id_enc"]),
             "role": r["role"], "active": bool(r["active"]),
             "created_at": r["created_at"], "last_login_at": r["last_login_at"]}
            for r in rows]


def usuario_por_id(user_id: int) -> dict | None:
    with transacao() as conn:
        r = conn.execute(
            "SELECT id, email_enc, role, active FROM users WHERE id = ?",
            (user_id,)).fetchone()
    if not r:
        return None
    return {"id": r["id"], "email": decifrar(r["email_enc"]),
            "role": r["role"], "active": bool(r["active"])}


# --------------------------------------------------------- limite de tentativas
# A tabela de tentativas guarda apenas ÍNDICES CEGOS (HMAC) do e-mail e do IP,
# nunca os valores em claro. A proteção contra força bruta só precisa de um
# identificador estável para contar — não do dado em si. Guardar o e-mail em
# texto puro aqui anularia a cifragem do restante do banco: bastaria ler esta
# tabela para saber quem usa o sistema.
def _registrar_tentativa(conn, email: str, ip: str) -> None:
    conn.execute("INSERT INTO login_attempts (email, ts, ip) VALUES (?,?,?)",
                 (indice_cego(email), time.time(),
                  indice_cego(ip) if ip else ""))


def _tentativas_recentes(conn, email: str, ip: str) -> tuple[int, int]:
    """(falhas recentes neste e-mail, contas distintas falhadas desta origem)."""
    limite = time.time() - JANELA_TENTATIVAS_S
    conn.execute("DELETE FROM login_attempts WHERE ts < ?", (limite,))
    idx_email = indice_cego(email)
    idx_ip = indice_cego(ip) if ip else ""
    por_email = conn.execute(
        "SELECT COUNT(*) AS n FROM login_attempts WHERE email = ? AND ts >= ?",
        (idx_email, limite)).fetchone()["n"]
    contas_ip = conn.execute(
        "SELECT COUNT(DISTINCT email) AS n FROM login_attempts"
        " WHERE ip = ? AND ip <> '' AND ts >= ?",
        (idx_ip, limite)).fetchone()["n"] if ip else 0
    return int(por_email), int(contas_ip)


# Hash descartável usado quando a conta não existe, para que o tempo de resposta
# do login não revele se um e-mail está cadastrado (enumeração por tempo).
_SALT_FANTASMA = b"\x00" * 16
_HASH_FANTASMA = hash_senha("conta-inexistente", _SALT_FANTASMA)


# --------------------------------------------------------------- sessões
def autenticar(email: str, senha: str, ip: str = "") -> tuple[User, str, datetime]:
    """Valida credenciais e abre sessão. Retorna (usuário, token, expiração).

    O registro da tentativa falha é confirmado em transação própria, **antes**
    de a exceção ser levantada: se ele compartilhasse a transação do erro,
    o rollback do sqlite3 o desfaria e a proteção ficaria inerte.
    """
    email = email.strip().lower()

    with transacao() as conn:
        falhas_email, contas_ip = _tentativas_recentes(conn, email, ip)
        row = conn.execute(
            "SELECT id, email_enc, full_name_enc, professional_id_enc,"
            " password_hash, salt, role, active FROM users WHERE email_idx = ?",
            (indice_cego(email),)).fetchone()
        dados = dict(row) if row else None

    if contas_ip >= MAX_CONTAS_DISTINTAS_IP:
        raise AuthError(
            "Muitas tentativas de acesso a contas distintas a partir deste "
            "dispositivo. Aguarde 15 minutos e tente novamente.")

    # A verificação de senha roda sempre, inclusive contra um hash descartável
    # quando a conta não existe: sem isso o tempo de resposta denunciaria quais
    # e-mails estão cadastrados.
    if dados:
        senha_ok = conferir_senha(senha, dados["salt"], dados["password_hash"])
    else:
        conferir_senha(senha, _SALT_FANTASMA, _HASH_FANTASMA)
        senha_ok = False

    # Mensagem idêntica para conta inexistente, senha errada e conta inativa:
    # distinguir os casos permitiria enumerar contas válidas.
    if not dados or not senha_ok or not dados["active"]:
        with transacao() as conn:
            _registrar_tentativa(conn, email, ip)
        # Atraso progressivo: encarece a força bruta sem jamais negar acesso a
        # quem tem a senha correta.
        if falhas_email:
            time.sleep(min(0.25 * (2 ** min(falhas_email, 5)), MAX_ATRASO_S))
        raise AuthError("E-mail ou senha inválidos.")

    token = gerar_token()
    expira = _agora() + timedelta(hours=SESSION_TTL_HOURS)
    with transacao() as conn:
        conn.execute("DELETE FROM login_attempts WHERE email = ?",
                     (indice_cego(email),))
        # Expurgo das sessões vencidas fora do caminho quente de leitura.
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(_agora()),))
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at)"
            " VALUES (?,?,?,?)",
            (hash_token(token), dados["id"], _iso(_agora()), _iso(expira)))
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?",
                     (_iso(_agora()), dados["id"]))

    user = User(int(dados["id"]), decifrar(dados["email_enc"]),
                decifrar(dados["full_name_enc"]),
                decifrar(dados["professional_id_enc"]), dados["role"])
    return user, token, expira


def usuario_por_token(token: str | None) -> User | None:
    """Resolve a sessão. Somente leitura: o filtro `expires_at >= ?` já garante
    a correção, e apagar sessões vencidas aqui transformaria cada requisição
    autenticada numa escrita, criando contenção no SQLite. O expurgo acontece
    ao abrir uma nova sessão."""
    if not token:
        return None
    with transacao() as conn:
        row = conn.execute(
            "SELECT u.id, u.email_enc, u.full_name_enc, u.professional_id_enc, u.role"
            "  FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token_hash = ? AND s.expires_at >= ? AND u.active = 1",
            (hash_token(token), _iso(_agora()))).fetchone()
    if not row:
        return None
    return User(int(row["id"]), decifrar(row["email_enc"]),
                decifrar(row["full_name_enc"]),
                decifrar(row["professional_id_enc"]), row["role"])


def encerrar_sessao(token: str | None) -> None:
    if not token:
        return
    with transacao() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))


# --------------------------------------------------------------- laudos
def salvar_analise(analysis_id: str, user_id: int, payload: dict) -> None:
    with transacao() as conn:
        conn.execute(
            "INSERT INTO analyses (id, user_id, created_at, payload_enc)"
            " VALUES (?,?,?,?)",
            (analysis_id, user_id, _iso(_agora()),
             cifrar(json.dumps(payload, ensure_ascii=False, default=str))))


def obter_analise(analysis_id: str, user_id: int) -> dict | None:
    """Só devolve o laudo ao usuário que o gerou.

    A verificação de posse é feita na consulta: mesmo de posse do identificador,
    outro usuário não recupera o exame — o identificador não é credencial.
    """
    with transacao() as conn:
        row = conn.execute(
            "SELECT payload_enc FROM analyses WHERE id = ? AND user_id = ?",
            (analysis_id, user_id)).fetchone()
    return json.loads(decifrar(row["payload_enc"])) if row else None


def listar_analises(user_id: int, limite: int = 50) -> list[dict]:
    with transacao() as conn:
        rows = conn.execute(
            "SELECT id, created_at, payload_enc FROM analyses WHERE user_id = ?"
            " ORDER BY created_at DESC LIMIT ?", (user_id, limite)).fetchall()
    saida = []
    for r in rows:
        p = json.loads(decifrar(r["payload_enc"]))
        saida.append({"analysis_id": r["id"], "created_at": r["created_at"],
                      "filename": (p.get("source") or {}).get("filename"),
                      "summary": p.get("summary")})
    return saida

"""Banco de dados (SQLite) de usuários, sessões e laudos.

Escolha de SQLite: o projeto precisa de persistência com controle de acesso sem
introduzir dependência de servidor externo. Para uso multi-instituição em
produção, migrar para PostgreSQL — o esquema é portável.

Cada análise é gravada com o `user_id` de quem a executou: sem esse vínculo não
há como impedir que um usuário baixe o laudo de outro, nem como atender ao
requisito de rastreabilidade de SaMD (quem gerou, quando, sobre qual arquivo).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "cardiolaudo.db"

SCHEMA = """
-- Colunas terminadas em _enc guardam AES-256-GCM (ver auth/crypto.py).
-- email_idx é um HMAC determinístico: permite localizar a conta no login sem
-- que o e-mail em si fique legível no arquivo do banco.
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    email_idx           TEXT    NOT NULL UNIQUE,
    email_enc           TEXT    NOT NULL,
    full_name_enc       TEXT    NOT NULL,
    professional_id_enc TEXT,
    password_hash       TEXT    NOT NULL,
    salt                BLOB    NOT NULL,
    role                TEXT    NOT NULL DEFAULT 'medico',
    active              INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT    NOT NULL,
    last_login_at       TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL,
    expires_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- payload_enc contém o laudo completo (medidas, achados, dados do paciente).
-- id, user_id e created_at ficam legíveis porque são necessários para a busca
-- e para o controle de posse: quem obtiver o arquivo saberá quantos exames
-- existem e quando, mas não de quem nem o conteúdo clínico.
CREATE TABLE IF NOT EXISTS analyses (
    id                TEXT    PRIMARY KEY,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at        TEXT    NOT NULL,
    payload_enc       TEXT    NOT NULL,
    legal_hold        INTEGER NOT NULL DEFAULT 0,
    legal_hold_motivo TEXT
);
CREATE INDEX IF NOT EXISTS idx_analyses_user ON analyses(user_id);

-- Registro das exclusões por retenção. Guarda apenas metadados — nunca o
-- conteúdo clínico, que é justamente o que se está eliminando. Serve para
-- demonstrar à auditoria o que foi apagado, quando e sob qual política.
CREATE TABLE IF NOT EXISTS deletions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    categoria    TEXT NOT NULL,
    referencia   TEXT,
    criado_em    TEXT,
    excluido_em  TEXT NOT NULL,
    politica_dias INTEGER NOT NULL,
    executor     TEXT
);
CREATE INDEX IF NOT EXISTS idx_deletions_cat ON deletions(categoria, excluido_em);

CREATE TABLE IF NOT EXISTS login_attempts (
    email TEXT NOT NULL,
    ts    REAL NOT NULL,
    ip    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_attempts_email ON login_attempts(email, ts);
"""

# Colunas acrescentadas depois da primeira versão do esquema. `CREATE TABLE IF
# NOT EXISTS` não altera tabelas já existentes, então bancos antigos precisam
# do ALTER — e ele tem de rodar ANTES de qualquer índice que use a coluna nova.
MIGRACOES = [
    ("login_attempts", "ip",
     "ALTER TABLE login_attempts ADD COLUMN ip TEXT NOT NULL DEFAULT ''"),
    # Retenção legal: quando 1, o laudo é imune ao expurgo por prazo.
    ("analyses", "legal_hold",
     "ALTER TABLE analyses ADD COLUMN legal_hold INTEGER NOT NULL DEFAULT 0"),
    ("analyses", "legal_hold_motivo",
     "ALTER TABLE analyses ADD COLUMN legal_hold_motivo TEXT"),
]

# Índices sobre colunas introduzidas por migração: criados depois delas.
INDICES_POS_MIGRACAO = [
    "CREATE INDEX IF NOT EXISTS idx_attempts_ip ON login_attempts(ip, ts)",
]

# Bancos anteriores à cifragem guardam e-mail, nome e laudo em texto puro.
# Trocar isso exige reconstruir as tabelas (o SQLite não remove colunas nem
# a restrição UNIQUE de `email`), o que é feito por scripts/migrate_encrypt.py
# — deliberadamente um passo explícito, com backup, e não algo que aconteça
# sozinho ao subir o servidor.
COLUNAS_LEGADAS = {"users": "email", "analyses": "payload"}


def precisa_migrar_cifragem(conn) -> list[str]:
    """Tabelas que ainda têm colunas em texto puro."""
    pendentes = []
    for tabela, coluna in COLUNAS_LEGADAS.items():
        try:
            cols = {r["name"] for r in
                    conn.execute(f"PRAGMA table_info({tabela})").fetchall()}
        except Exception:
            continue
        if coluna in cols:
            pendentes.append(tabela)
    return pendentes


def connect() -> sqlite3.Connection:
    """Conexão por requisição. SQLite não compartilha conexão entre threads."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # Sem isto, o conteúdo apagado permanece nas páginas liberadas do arquivo:
    # excluir uma conta ou um laudo não removeria o dado do disco, apenas o
    # tornaria invisível às consultas.
    conn.execute("PRAGMA secure_delete = ON")
    return conn


@contextmanager
def transacao():
    """Confirma ao sair, desfaz em erro e **fecha** a conexão.

    `with sqlite3.connect(...)` sozinho não fecha a conexão — apenas encerra a
    transação — o que vaza descritores a cada requisição.
    """
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with transacao() as conn:
        conn.executescript(SCHEMA)
        for tabela, coluna, ddl in MIGRACOES:
            existentes = {r["name"] for r in
                          conn.execute(f"PRAGMA table_info({tabela})").fetchall()}
            if coluna not in existentes:
                conn.execute(ddl)
        for ddl in INDICES_POS_MIGRACAO:
            conn.execute(ddl)

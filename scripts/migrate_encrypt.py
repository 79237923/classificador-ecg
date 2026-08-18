"""Migra um banco em texto puro para o esquema cifrado.

Reconstrói `users` e `analyses`: o SQLite não remove colunas nem a restrição
UNIQUE de `email`, então não basta esvaziar os campos — a coluna em texto puro
precisa deixar de existir, senão o dado continuaria recuperável no arquivo.

Passo explícito e não automático: exige backup e depende da chave de cifragem
estar configurada. Se a chave mudar depois, os dados migrados ficam ilegíveis.

Uso:
    python scripts/manage_keys.py conferir      # confirma a chave em uso
    python scripts/migrate_encrypt.py --backup  # migra, salvando cópia antes
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.auth import crypto  # noqa: E402
from backend.app.auth.db import DB_PATH, connect, init_db, transacao  # noqa: E402
from backend.app.reporting.report import AUDIT_DIR  # noqa: E402


def _colunas(conn, tabela: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({tabela})").fetchall()}


def migrar_users(conn) -> int:
    cols = _colunas(conn, "users")
    if "email" not in cols:
        print("  users     : já migrada")
        return 0

    linhas = conn.execute(
        "SELECT id, email, full_name, professional_id, password_hash, salt,"
        " role, active, created_at, last_login_at FROM users").fetchall()

    conn.execute("""
        CREATE TABLE users_novo (
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
        )""")
    for r in linhas:
        conn.execute(
            "INSERT INTO users_novo (id, email_idx, email_enc, full_name_enc,"
            " professional_id_enc, password_hash, salt, role, active,"
            " created_at, last_login_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (r["id"], crypto.indice_cego(r["email"]), crypto.cifrar(r["email"]),
             crypto.cifrar(r["full_name"]), crypto.cifrar(r["professional_id"]),
             r["password_hash"], r["salt"], r["role"], r["active"],
             r["created_at"], r["last_login_at"]))

    # As sessões referenciam users(id) por chave estrangeira; os ids são
    # preservados, então os vínculos continuam válidos após a troca.
    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users_novo RENAME TO users")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_idx"
                 " ON users(email_idx)")
    print(f"  users     : {len(linhas)} conta(s) cifrada(s)")
    return len(linhas)


def migrar_analyses(conn) -> int:
    cols = _colunas(conn, "analyses")
    if "payload" not in cols:
        print("  analyses  : já migrada")
        return 0

    linhas = conn.execute(
        "SELECT id, user_id, created_at, payload FROM analyses").fetchall()
    conn.execute("""
        CREATE TABLE analyses_novo (
            id          TEXT    PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at  TEXT    NOT NULL,
            payload_enc TEXT    NOT NULL
        )""")
    for r in linhas:
        conn.execute(
            "INSERT INTO analyses_novo (id, user_id, created_at, payload_enc)"
            " VALUES (?,?,?,?)",
            (r["id"], r["user_id"], r["created_at"], crypto.cifrar(r["payload"])))
    conn.execute("DROP TABLE analyses")
    conn.execute("ALTER TABLE analyses_novo RENAME TO analyses")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_analyses_user ON analyses(user_id)")
    print(f"  analyses  : {len(linhas)} laudo(s) cifrado(s)")
    return len(linhas)


def migrar_auditoria() -> int:
    """Reescreve a trilha de auditoria, cifrando as linhas em texto puro."""
    if not AUDIT_DIR.exists():
        return 0
    total = 0
    for arq in sorted(AUDIT_DIR.glob("*.jsonl")):
        saida = []
        mudou = False
        for linha in arq.read_text(encoding="utf-8").splitlines():
            if not linha.strip():
                continue
            try:
                obj = json.loads(linha)
            except json.JSONDecodeError:
                saida.append(linha)
                continue
            if "dados" in obj:  # já cifrada
                saida.append(linha)
                continue
            envelope = {
                "ts": obj.get("created_at") or datetime.now(timezone.utc)
                      .isoformat(timespec="seconds"),
                "analysis_id": obj.get("analysis_id"),
                "dados": crypto.cifrar(json.dumps(obj, ensure_ascii=False, default=str)),
            }
            saida.append(json.dumps(envelope, ensure_ascii=False))
            mudou = True
            total += 1
        if mudou:
            arq.write_text("\n".join(saida) + "\n", encoding="utf-8")
    if total:
        print(f"  auditoria : {total} registro(s) cifrado(s)")
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", action="store_true",
                    help="salva uma cópia do banco antes de migrar")
    args = ap.parse_args()

    try:
        crypto.carregar_chave()
    except crypto.CryptoError as exc:
        print(f"Chave de cifragem indisponível: {exc}")
        return 1

    if not DB_PATH.exists():
        print(f"Banco não encontrado em {DB_PATH}; nada a migrar.")
        return 0

    if args.backup:
        destino = DB_PATH.with_suffix(
            f".pre-cifragem-{datetime.now(timezone.utc):%Y%m%d%H%M%S}.db")
        shutil.copy2(DB_PATH, destino)
        print(f"Backup: {destino}")
        print("ATENÇÃO: este backup está EM TEXTO PURO. Guarde-o cifrado ou "
              "apague-o assim que confirmar que a migração deu certo.\n")

    init_db()
    print("Migrando para o esquema cifrado:")
    with transacao() as conn:
        migrar_users(conn)
        migrar_analyses(conn)
    migrar_auditoria()

    # Passo indispensável: DROP TABLE não apaga o conteúdo do arquivo — as
    # páginas antigas ficam na lista de livres, e o texto puro continua legível
    # com um editor hexadecimal. VACUUM reescreve o banco do zero.
    # Não pode rodar dentro de uma transação.
    print("  vacuum    : reescrevendo o arquivo para descartar páginas antigas")
    conn = connect()
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.isolation_level = None
        conn.execute("VACUUM")
    finally:
        conn.close()

    residuos = _residuo_texto_puro()
    if residuos:
        print(f"\nATENÇÃO: ainda há vestígio legível no arquivo ({residuos}).")
        return 1

    print("\nConcluído. Confira com: python scripts/manage_keys.py conferir")
    return 0


def _residuo_texto_puro() -> str | None:
    """Confere se sobrou e-mail legível no arquivo.

    A busca é por um padrão de e-mail completo, não por caracteres isolados: o
    próprio cabeçalho do SQLite contém bytes que coincidem com '@'.
    """
    padrao = re.compile(rb"[A-Za-z0-9._%+-]{3,}@[A-Za-z0-9.-]{3,}\.[A-Za-z]{2,}")
    achado = padrao.search(DB_PATH.read_bytes())
    return achado.group(0).decode("latin-1") if achado else None


if __name__ == "__main__":
    sys.exit(main())

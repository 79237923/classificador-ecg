"""Administração de contas do CardioLaudo.

A senha é lida do terminal sem eco (getpass) e nunca é aceita por argumento de
linha de comando — argumentos ficam no histórico do shell e na lista de
processos do sistema.

Uso:
    .venv\\Scripts\\python scripts\\manage_users.py criar
    .venv\\Scripts\\python scripts\\manage_users.py listar
    .venv\\Scripts\\python scripts\\manage_users.py desativar <email>
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.auth import service  # noqa: E402
from backend.app.auth.crypto import decifrar  # noqa: E402
from backend.app.auth.db import init_db, transacao  # noqa: E402


def cmd_criar() -> int:
    init_db()
    email = input("E-mail: ").strip()
    nome = input("Nome completo: ").strip()
    registro = input("Registro profissional (CRM, opcional): ").strip()

    senha = getpass.getpass("Senha (mín. 12 caracteres, letras e números): ")
    if senha != getpass.getpass("Confirme a senha: "):
        print("As senhas não conferem.")
        return 1

    try:
        user = service.criar_usuario(email, nome, senha, registro or None)
    except service.AuthError as exc:
        print(f"Erro: {exc}")
        return 1
    print(f"Conta criada: {user.full_name} <{user.email}> (id={user.id})")
    return 0


def cmd_listar() -> int:
    init_db()
    with transacao() as conn:
        rows = conn.execute(
            "SELECT email_enc, full_name_enc, professional_id_enc, role, active,"
            " last_login_at FROM users ORDER BY created_at").fetchall()
    if not rows:
        print("Nenhuma conta cadastrada. Use: manage_users.py criar")
        return 0
    print(f"{'e-mail':<38} {'nome':<24} {'papel':<7} {'ativo':<6} último acesso")
    for r in rows:
        email = decifrar(r["email_enc"]) or "—"
        nome = (decifrar(r["full_name_enc"]) or "—")[:23]
        print(f"{email:<38} {nome:<24} {r['role']:<7} "
              f"{'sim' if r['active'] else 'não':<6} {r['last_login_at'] or '—'}")
    return 0


def cmd_desativar(email: str) -> int:
    init_db()
    if service.desativar_usuario(email):
        print(f"Conta {email} desativada e sessões encerradas.")
        return 0
    print(f"Conta não encontrada: {email}")
    return 1


def cmd_senha(email: str) -> int:
    """Redefine a senha de uma conta (reset de administrador)."""
    init_db()
    senha = getpass.getpass("Nova senha (mín. 12 caracteres, letras e números): ")
    if senha != getpass.getpass("Confirme a nova senha: "):
        print("As senhas não conferem.")
        return 1
    try:
        ok = service.resetar_senha(email, senha)
    except service.AuthError as exc:
        print(f"Erro: {exc}")
        return 1
    if not ok:
        print(f"Conta não encontrada: {email}")
        return 1
    print(f"Senha de {email} redefinida; as sessões da conta foram encerradas.")
    return 0


def cmd_promover(email: str, role: str) -> int:
    """Define o papel de uma conta — usado para criar o primeiro administrador."""
    init_db()
    try:
        ok = service.definir_papel(email, role)
    except service.AuthError as exc:
        print(f"Erro: {exc}")
        return 1
    if not ok:
        print(f"Conta não encontrada: {email}")
        return 1
    print(f"Conta {email} agora tem papel '{role}'.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Administração de contas do CardioLaudo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("criar", help="cria uma conta de profissional")
    sub.add_parser("listar", help="lista as contas cadastradas")
    p_des = sub.add_parser("desativar", help="desativa uma conta e encerra suas sessões")
    p_des.add_argument("email")
    p_sen = sub.add_parser("senha", help="redefine a senha de uma conta (reset de admin)")
    p_sen.add_argument("email")
    p_pro = sub.add_parser("papel", help="define o papel de uma conta (medico/admin)")
    p_pro.add_argument("email")
    p_pro.add_argument("role", choices=["medico", "admin"])

    args = ap.parse_args()
    if args.cmd == "criar":
        return cmd_criar()
    if args.cmd == "listar":
        return cmd_listar()
    if args.cmd == "desativar":
        return cmd_desativar(args.email)
    if args.cmd == "senha":
        return cmd_senha(args.email)
    return cmd_promover(args.email, args.role)


if __name__ == "__main__":
    sys.exit(main())

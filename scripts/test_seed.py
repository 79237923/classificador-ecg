"""Testa a semeadura da conta administradora em banco vazio.

Isto é o que torna utilizável uma hospedagem de disco efêmero: se a semeadura
falhar, o sistema sobe sem nenhuma conta e ninguém consegue entrar — inclusive
o administrador. O teste roda contra um banco temporário, sem tocar no real.

Uso: .venv\\Scripts\\python scripts\\test_seed.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

falhas: list[str] = []


def checar(cond: bool, desc: str) -> None:
    print(f"  [{'OK ' if cond else 'FALHA'}] {desc}")
    if not cond:
        falhas.append(desc)


def main() -> int:
    from backend.app.auth import db as dbmod

    tmp = Path(tempfile.mkdtemp(prefix="cardiolaudo_seed_"))
    dbmod.DB_PATH = tmp / "teste.db"          # isola do banco real

    from backend.app.auth import service
    from backend.app.seed import semear_admin

    email = f"admin.seed.{uuid.uuid4().hex[:6]}@teste.local"
    senha = "SenhaSemeada2026"

    print("\n1. Banco vazio + variáveis definidas → cria a conta")
    os.environ["CARDIOLAUDO_ADMIN_EMAIL"] = email
    os.environ["CARDIOLAUDO_ADMIN_SENHA"] = senha
    os.environ["CARDIOLAUDO_ADMIN_NOME"] = "Admin Semeado"
    dbmod.init_db()
    semear_admin()

    contas = service.listar_usuarios()
    checar(len(contas) == 1, f"exatamente uma conta criada (obtido {len(contas)})")
    if contas:
        checar(contas[0]["email"] == email, "e-mail confere")
        checar(contas[0]["role"] == "admin", "papel é 'admin'")
        checar(contas[0]["active"], "conta está ativa")

    print("\n2. A conta semeada consegue autenticar")
    try:
        user, token, _ = service.autenticar(email, senha, ip="127.0.0.1")
        checar(user.email == email and bool(token), "login funciona com a senha do ambiente")
    except service.AuthError as exc:
        checar(False, f"login falhou: {exc}")

    print("\n3. Rodar de novo NÃO duplica nem sobrescreve")
    semear_admin()
    checar(len(service.listar_usuarios()) == 1, "continua com uma única conta")

    print("\n4. Com contas existentes, não interfere")
    outro = f"medico.{uuid.uuid4().hex[:6]}@teste.local"
    service.criar_usuario(outro, "Dr. Outro", "OutraSenha2026x")
    os.environ["CARDIOLAUDO_ADMIN_EMAIL"] = f"novo.{uuid.uuid4().hex[:6]}@teste.local"
    semear_admin()
    emails = {u["email"] for u in service.listar_usuarios()}
    checar(len(emails) == 2 and outro in emails,
           "não cria conta nova quando já existem contas")

    print("\n5. Sem variáveis definidas, não faz nada (banco vazio)")
    dbmod.DB_PATH = tmp / "vazio.db"
    for v in ("CARDIOLAUDO_ADMIN_EMAIL", "CARDIOLAUDO_ADMIN_SENHA"):
        os.environ.pop(v, None)
    dbmod.init_db()
    semear_admin()
    checar(len(service.listar_usuarios()) == 0, "sem variáveis, nenhuma conta é criada")

    print("\n6. Senha fraca é recusada (não cria conta insegura em silêncio)")
    dbmod.DB_PATH = tmp / "fraca.db"
    os.environ["CARDIOLAUDO_ADMIN_EMAIL"] = f"fraco.{uuid.uuid4().hex[:6]}@teste.local"
    os.environ["CARDIOLAUDO_ADMIN_SENHA"] = "123"
    dbmod.init_db()
    semear_admin()
    checar(len(service.listar_usuarios()) == 0, "senha fraca não vira conta")

    for v in ("CARDIOLAUDO_ADMIN_EMAIL", "CARDIOLAUDO_ADMIN_SENHA",
              "CARDIOLAUDO_ADMIN_NOME"):
        os.environ.pop(v, None)

    print(f"\n{'TODOS OS TESTES DE SEMEADURA PASSARAM' if not falhas else f'{len(falhas)} FALHA(S): ' + '; '.join(falhas)}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

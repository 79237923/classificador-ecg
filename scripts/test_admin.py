"""Testes de troca de senha e administração de contas.

O que precisa ser provado: um médico comum NÃO acessa rotas de admin; a troca de
senha exige a senha atual e derruba as outras sessões; o reset de admin funciona
sem a senha atual. Erros aqui abririam acesso indevido a dado de saúde.

Uso (com a API em :8000): .venv\\Scripts\\python scripts\\test_admin.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.auth import service  # noqa: E402
from backend.app.auth.db import init_db  # noqa: E402

BASE = "http://127.0.0.1:8000"
falhas: list[str] = []


def checar(cond: bool, desc: str) -> None:
    print(f"  [{'OK ' if cond else 'FALHA'}] {desc}")
    if not cond:
        falhas.append(desc)


def cliente():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()))


def pedir(op, caminho, dados=None, metodo=None):
    corpo = json.dumps(dados).encode() if dados is not None else None
    req = urllib.request.Request(BASE + caminho, data=corpo, method=metodo)
    if corpo:
        req.add_header("Content-Type", "application/json")
    try:
        with op.open(req, timeout=60) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt else {})
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(txt)
        except json.JSONDecodeError:
            return e.code, {"detail": txt[:200]}


def login(op, email, senha):
    return pedir(op, "/api/auth/login", {"email": email, "senha": senha})


def main() -> int:
    init_db()
    suf = uuid.uuid4().hex[:8]
    admin_email = f"admin.{suf}@teste.local"
    medico_email = f"medico.{suf}@teste.local"
    senha = "SenhaForte2026aa"

    service.criar_usuario(admin_email, "Admin Teste", senha, role="admin")
    service.criar_usuario(medico_email, "Dr. Medico", senha, professional_id="CRM-1")

    print("\n1. Controle de acesso às rotas de admin")
    anon = cliente()
    st, _ = pedir(anon, "/api/admin/usuarios")
    checar(st == 401, f"sem sessão -> 401 (obtido {st})")

    med = cliente()
    login(med, medico_email, senha)
    st, _ = pedir(med, "/api/admin/usuarios")
    checar(st == 403, f"médico comum -> 403 (obtido {st})")
    st, _ = pedir(med, "/api/admin/usuarios",
                  {"email": f"x.{suf}@t.local", "full_name": "X", "senha": senha})
    checar(st == 403, f"médico não cria usuário -> 403 (obtido {st})")

    adm = cliente()
    login(adm, admin_email, senha)
    st, body = pedir(adm, "/api/admin/usuarios")
    checar(st == 200 and "usuarios" in body, f"admin lista usuários -> 200 (obtido {st})")

    print("\n2. Admin cria e gerencia contas")
    novo = f"novo.{suf}@teste.local"
    st, _ = pedir(adm, "/api/admin/usuarios",
                  {"email": novo, "full_name": "Nova Conta", "senha": senha,
                   "professional_id": "CRM-9"})
    checar(st == 200, f"admin cria conta -> 200 (obtido {st})")
    st_login, _ = login(cliente(), novo, senha)
    checar(st_login == 200, "conta criada consegue entrar")

    st, _ = pedir(adm, f"/api/admin/usuarios/{novo}/desativar", {}, "POST")
    checar(st == 200, f"admin desativa conta -> 200 (obtido {st})")
    st_login, _ = login(cliente(), novo, senha)
    checar(st_login == 401, "conta desativada não entra mais")

    print("\n3. Admin não desativa a própria conta")
    st, _ = pedir(adm, f"/api/admin/usuarios/{admin_email}/desativar", {}, "POST")
    checar(st == 400, f"auto-desativação bloqueada -> 400 (obtido {st})")

    print("\n4. Troca de senha pelo próprio usuário")
    u = cliente()
    login(u, medico_email, senha)
    # segunda sessão do mesmo usuário, para provar que a troca a encerra
    u2 = cliente()
    login(u2, medico_email, senha)
    st, _ = pedir(u2, "/api/auth/me")
    checar(st == 200, "segunda sessão ativa antes da troca")

    st, _ = pedir(u, "/api/auth/senha",
                  {"senha_atual": "errada-de-proposito", "senha_nova": "OutraSenha2026x"})
    checar(st == 400, f"senha atual errada -> 400 (obtido {st})")
    st, _ = pedir(u, "/api/auth/senha",
                  {"senha_atual": senha, "senha_nova": "curta"})
    checar(st == 400, f"senha nova fraca -> 400 (obtido {st})")
    st, _ = pedir(u, "/api/auth/senha",
                  {"senha_atual": senha, "senha_nova": senha})
    checar(st == 400, f"senha nova igual à atual -> 400 (obtido {st})")

    nova = "NovaSenhaBoa2026"
    st, _ = pedir(u, "/api/auth/senha", {"senha_atual": senha, "senha_nova": nova})
    checar(st == 200, f"troca válida -> 200 (obtido {st})")
    st, _ = pedir(u, "/api/auth/me")
    checar(st == 200, "sessão que trocou a senha continua válida")
    st, _ = pedir(u2, "/api/auth/me")
    checar(st == 401, "as OUTRAS sessões foram encerradas pela troca")
    checar(login(cliente(), medico_email, senha)[0] == 401, "senha antiga não entra mais")
    checar(login(cliente(), medico_email, nova)[0] == 200, "senha nova entra")

    print("\n5. Reset de senha pelo admin (sem a senha atual)")
    st, _ = pedir(adm, "/api/admin/usuarios/reset-senha",
                  {"email": medico_email, "senha_nova": "ResetPeloAdmin26"})
    checar(st == 200, f"admin redefine senha -> 200 (obtido {st})")
    checar(login(cliente(), medico_email, "ResetPeloAdmin26")[0] == 200,
           "conta entra com a senha redefinida pelo admin")

    for e in (admin_email, medico_email, novo):
        service.desativar_usuario(e)
    # As tentativas falhas propositais deste teste ficariam contando contra a
    # origem nas execuções seguintes.
    from backend.app.auth.db import transacao
    with transacao() as conn:
        conn.execute("DELETE FROM login_attempts")

    print(f"\n{'TODOS OS TESTES DE ADMINISTRAÇÃO PASSARAM' if not falhas else f'{len(falhas)} FALHA(S): ' + '; '.join(falhas)}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

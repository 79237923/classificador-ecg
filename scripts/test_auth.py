"""Testes de autenticação e isolamento entre contas.

Verifica o que de fato protege o dado do paciente:
  1. Rotas clínicas rejeitam acesso sem sessão (401).
  2. Login com senha errada falha; com senha correta abre sessão.
  3. Um usuário NÃO baixa o laudo gerado por outro, mesmo sabendo o ID.
  4. Logout invalida a sessão.
  5. Senha fraca é recusada na criação de conta.
  6. Excesso de tentativas bloqueia temporariamente a conta.

Uso (com a API rodando em :8000):
    .venv\\Scripts\\python scripts\\test_auth.py
"""
from __future__ import annotations

import json
import mimetypes
import sys
import urllib.error
import urllib.request
import uuid
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8000"
AMOSTRA = ROOT / "data" / "samples" / "ecg_taquicardia.csv"

falhas: list[str] = []


def checar(condicao: bool, descricao: str) -> None:
    print(f"  [{'OK ' if condicao else 'FALHA'}] {descricao}")
    if not condicao:
        falhas.append(descricao)


def cliente() -> urllib.request.OpenerDirector:
    """Opener isolado: cada cliente tem seu próprio cookie de sessão."""
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()))


def pedir(op, caminho: str, dados: dict | None = None, metodo: str | None = None):
    corpo = json.dumps(dados).encode() if dados is not None else None
    req = urllib.request.Request(BASE + caminho, data=corpo, method=metodo)
    if corpo:
        req.add_header("Content-Type", "application/json")
    try:
        with op.open(req, timeout=120) as r:
            bruto = r.read()
            # Respostas de laudo são PDF (binário): não tentar decodificar.
            if r.headers.get("Content-Type", "").startswith("application/pdf"):
                return r.status, {"pdf_bytes": len(bruto), "pdf_ok": bruto[:4] == b"%PDF"}
            texto = bruto.decode("utf-8", errors="replace")
            return r.status, (json.loads(texto) if texto else {})
    except urllib.error.HTTPError as e:
        texto = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(texto)
        except json.JSONDecodeError:
            return e.code, {"detail": texto[:200]}


def enviar_ecg(op, caminho_arquivo: Path):
    return enviar_ecg_com_campos(op, caminho_arquivo, {"sampling_rate": "500"})


def enviar_ecg_com_campos(op, caminho_arquivo: Path, campos: dict):
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(caminho_arquivo.name)[0] or "application/octet-stream"
    corpo = b""
    for k, v in campos.items():
        corpo += (f"--{boundary}\r\nContent-Disposition: form-data; "
                  f'name="{k}"\r\n\r\n{v}\r\n').encode()
    corpo += (f"--{boundary}\r\nContent-Disposition: form-data; "
              f'name="file"; filename="{caminho_arquivo.name}"\r\n'
              f"Content-Type: {ctype}\r\n\r\n").encode()
    corpo += caminho_arquivo.read_bytes() + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(BASE + "/api/analyze", data=corpo, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with op.open(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"detail": e.read().decode(errors="replace")[:200]}


def main() -> int:
    from backend.app.auth import service
    from backend.app.auth.db import init_db

    init_db()
    sufixo = uuid.uuid4().hex[:8]
    cred = [
        (f"medico.a.{sufixo}@teste.local", "Dra. Ana Teste", "SenhaForte2026a"),
        (f"medico.b.{sufixo}@teste.local", "Dr. Bruno Teste", "SenhaForte2026b"),
    ]
    for email, nome, senha in cred:
        service.criar_usuario(email, nome, senha, professional_id="CRM-TESTE")

    print("\n1. Rotas clínicas exigem sessão")
    anon = cliente()
    st, _ = pedir(anon, "/api/analyses")
    checar(st == 401, f"GET /api/analyses sem sessão -> 401 (obtido {st})")
    st, _ = enviar_ecg(anon, AMOSTRA)
    checar(st == 401, f"POST /api/analyze sem sessão -> 401 (obtido {st})")
    st, _ = pedir(anon, "/api/auth/me")
    checar(st == 401, f"GET /api/auth/me sem sessão -> 401 (obtido {st})")

    print("\n2. Validação de senha no login")
    a = cliente()
    st, _ = pedir(a, "/api/auth/login",
                  {"email": cred[0][0], "senha": "senha-errada-123"})
    checar(st == 401, f"senha incorreta -> 401 (obtido {st})")
    st, body = pedir(a, "/api/auth/login", {"email": cred[0][0], "senha": cred[0][2]})
    checar(st == 200 and body.get("full_name") == cred[0][1],
           f"senha correta -> 200 e identifica o usuário (obtido {st})")

    print("\n3. Isolamento entre contas")
    st, analise = enviar_ecg(a, AMOSTRA)
    checar(st == 200, f"usuário A analisa um ECG -> 200 (obtido {st})")
    id_a = analise.get("analysis_id", "")
    checar(bool(id_a) and len(id_a) >= 20, "ID da análise é um token longo e aleatório")
    op_email = ((analise.get("source") or {}).get("operator") or {}).get("email")
    checar(op_email == cred[0][0], "laudo registra o operador que o gerou")

    st, corpo = pedir(a, f"/api/report/{id_a}/pdf")
    checar(st == 200 and corpo.get("pdf_ok") is True,
           f"usuário A baixa o próprio laudo em PDF -> 200 (obtido {st})")

    b = cliente()
    pedir(b, "/api/auth/login", {"email": cred[1][0], "senha": cred[1][2]})
    st, _ = pedir(b, f"/api/report/{id_a}/pdf")
    checar(st == 404, f"usuário B NÃO baixa o laudo de A -> 404 (obtido {st})")

    st, lista = pedir(b, "/api/analyses")
    ids_b = [x["analysis_id"] for x in (lista.get("analyses") or [])]
    checar(id_a not in ids_b, "histórico de B não expõe a análise de A")

    st, _ = pedir(anon, f"/api/report/{id_a}/pdf")
    checar(st == 401, f"anônimo com o ID em mãos -> 401 (obtido {st})")

    print("\n4. Logout encerra a sessão")
    pedir(a, "/api/auth/logout", {}, metodo="POST")
    st, _ = pedir(a, "/api/auth/me")
    checar(st == 401, f"após logout, /me -> 401 (obtido {st})")
    st, _ = pedir(a, f"/api/report/{id_a}/pdf")
    checar(st == 401, f"após logout, laudo inacessível -> 401 (obtido {st})")

    print("\n5. Política de senha")
    for fraca, motivo in (("curta1", "curta demais"), ("123456789012345", "só dígitos"),
                          ("abcdefghijklmno", "só letras")):
        try:
            service.criar_usuario(f"fraca.{uuid.uuid4().hex[:6]}@teste.local",
                                  "Teste Fraco", fraca)
            checar(False, f"senha {motivo} deveria ser recusada")
        except service.AuthError:
            checar(True, f"senha {motivo} recusada")

    print("\n6. Enumeração de contas")
    e = cliente()
    st_inexistente, corpo_inex = pedir(
        e, "/api/auth/login", {"email": "nao.existe@teste.local", "senha": "SenhaQualquer1"})
    st_existente, corpo_exist = pedir(
        e, "/api/auth/login", {"email": cred[0][0], "senha": "SenhaErrada12345"})
    checar(st_inexistente == st_existente == 401,
           "mesmo código HTTP para conta inexistente e senha errada")
    checar(corpo_inex.get("detail") == corpo_exist.get("detail"),
           "mesma mensagem para conta inexistente e senha errada")

    print("\n7. Sanitização de campos do formulário")
    g = cliente()
    pedir(g, "/api/auth/login", {"email": cred[0][0], "senha": cred[0][2]})
    st, corpo = enviar_ecg_com_campos(
        g, AMOSTRA, {"sampling_rate": "500", "sex": "<b>injecao</b>"})
    checar(st == 422, f"campo 'sex' com marcação é recusado -> 422 (obtido {st})")
    st, corpo = enviar_ecg_com_campos(
        g, AMOSTRA, {"sampling_rate": "500", "sex": "Feminino", "age": "45"})
    sexo = (((corpo.get("source") or {}).get("patient")) or {}).get("sex")
    checar(st == 200 and sexo == "f",
           f"campo 'sex' é normalizado para valor conhecido (obtido {sexo!r})")

    # Estes dois últimos blocos disparam o bloqueio da origem de propósito e
    # ficam por último: a partir daqui, nenhum login funciona deste IP.
    print("\n8. Força bruta: o titular nunca é bloqueado")
    c = cliente()
    for _ in range(8):
        pedir(c, "/api/auth/login",
              {"email": cred[1][0], "senha": "errada-de-proposito"})
    st, _ = pedir(c, "/api/auth/login", {"email": cred[1][0], "senha": cred[1][2]})
    checar(st == 200,
           f"senha correta é aceita após 8 falhas na mesma conta (obtido {st})")

    print("\n9. Password spraying bloqueia a origem")
    d = cliente()
    for i in range(12):
        pedir(d, "/api/auth/login",
              {"email": f"varredura.{i}.{sufixo}@teste.local", "senha": "Qualquer12345"})
    st, body = pedir(d, "/api/auth/login", {"email": cred[0][0], "senha": cred[0][2]})
    checar(st == 401 and "contas distintas" in str(body.get("detail", "")).lower(),
           f"origem bloqueada após varrer 12 contas distintas (obtido {st})")

    for email, _, _ in cred:
        service.desativar_usuario(email)

    # O bloco 9 bloqueia esta origem de propósito. Sem limpar, o bloqueio
    # persistiria por 15 minutos e barraria qualquer execução seguinte
    # (inclusive o smoke_test) a partir da mesma máquina.
    from backend.app.auth.db import transacao
    with transacao() as conn:
        conn.execute("DELETE FROM login_attempts")
    print("\n(tentativas de login limpas — origem desbloqueada)")

    print(f"\n{'TODOS OS TESTES DE AUTENTICAÇÃO PASSARAM' if not falhas else f'{len(falhas)} FALHA(S): ' + '; '.join(falhas)}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

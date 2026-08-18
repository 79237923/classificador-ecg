"""Teste de fumaça da API: envia as amostras e resume as respostas.

Uso: .venv\\Scripts\\python scripts\\smoke_test.py
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
API = f"{BASE}/api/analyze"

# As rotas clínicas exigem sessão: o teste cria uma conta temporária própria.
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(CookieJar()))


def abrir_sessao() -> str:
    from backend.app.auth import service
    from backend.app.auth.db import init_db

    init_db()
    email = f"smoke.{uuid.uuid4().hex[:8]}@teste.local"
    senha = "SmokeTeste2026x"
    service.criar_usuario(email, "Conta de teste automatizado", senha,
                          professional_id="CRM-SMOKE")
    corpo = json.dumps({"email": email, "senha": senha}).encode()
    req = urllib.request.Request(f"{BASE}/api/auth/login", data=corpo,
                                 headers={"Content-Type": "application/json"})
    with _opener.open(req, timeout=60) as r:
        r.read()
    return email

SAMPLES = [
    ("ecg_normal_12d.csv", {"sampling_rate": "500", "age": "45", "sex": "m"}),
    ("ecg_taquicardia.csv", {"sampling_rate": "500"}),
    ("ecg_irregular.csv", {"sampling_rate": "500"}),
    ("ecg_normal_imagem.png", {}),
    # Laudos de 12 derivações no formato clínico real (três linhas de quatro
    # derivações mais a tira de ritmo), gerados de sinal do PTB-XL por
    # scripts/make_ecg_sheet.py. A versão de baixa resolução reproduz o caso
    # em que a grade de 1 mm ocupa poucos pixels — o layout e a resolução que
    # antes quebravam a digitalização.
    ("ecg12_laudo_alta.png", {}),
    ("ecg12_laudo_baixa.png", {}),
]


def post_multipart(url: str, filepath: Path, fields: dict) -> dict:
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                 f'name="{k}"\r\n\r\n{v}\r\n').encode()
    ctype = mimetypes.guess_type(filepath.name)[0] or "application/octet-stream"
    body += (f"--{boundary}\r\nContent-Disposition: form-data; "
             f'name="file"; filename="{filepath.name}"\r\n'
             f"Content-Type: {ctype}\r\n\r\n").encode()
    body += filepath.read_bytes() + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with _opener.open(req, timeout=300) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"erro": e.code, "detail": e.read().decode(errors="replace")}


def main():
    from backend.app.auth import service

    email = abrir_sessao()
    print(f"sessão de teste: {email}")
    failures = 0
    for name, fields in SAMPLES:
        path = ROOT / "data" / "samples" / name
        print(f"\n=== {name} ===")
        r = post_multipart(API, path, fields)
        if "erro" in r:
            failures += 1
            print(f"  FALHOU: HTTP {r['erro']} — {r['detail'][:300]}")
            continue
        m = r["measurements"]
        print(f"  resumo : {r['summary']}")
        print(f"  FC={m['heart_rate_bpm']}, PR={m['pr_ms']}, QRS={m['qrs_ms']}, "
              f"QTcF={m['qtc_fridericia_ms']}, eixo={m['axis_degrees']}")
        print(f"  achados: {[f['label'] for f in r['findings']]}")
        for w in r["quality"]["warnings"]:
            print(f"  aviso  : {w}")
        pdf_url = f"{BASE}/api/report/{r['analysis_id']}/pdf"
        with _opener.open(pdf_url, timeout=60) as resp:
            pdf = resp.read()
        print(f"  PDF    : {len(pdf)} bytes {'OK' if pdf[:4] == b'%PDF' else 'INVÁLIDO'}")

    service.desativar_usuario(email)  # não deixar conta de teste ativa
    print(f"\n{'TODOS OS TESTES PASSARAM' if not failures else f'{failures} FALHA(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

"""Envia uma imagem à API autenticada e mostra o laudo, como o usuário veria.

Uso: .venv\\Scripts\\python scripts\\analisar_via_api.py <imagem> <email> <senha>
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

BASE = "http://127.0.0.1:8000"


def main(imagem: str, email: str, senha: str) -> int:
    op = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()))

    corpo = json.dumps({"email": email, "senha": senha}).encode()
    req = urllib.request.Request(f"{BASE}/api/auth/login", data=corpo,
                                 headers={"Content-Type": "application/json"})
    try:
        with op.open(req, timeout=60) as r:
            r.read()
    except urllib.error.HTTPError as e:
        print(f"Falha no login: {e.read().decode(errors='replace')[:200]}")
        return 1

    caminho = Path(imagem)
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(caminho.name)[0] or "application/octet-stream"
    dados = (f"--{boundary}\r\nContent-Disposition: form-data; "
             f'name="file"; filename="{caminho.name}"\r\n'
             f"Content-Type: {ctype}\r\n\r\n").encode()
    dados += caminho.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{BASE}/api/analyze", data=dados, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

    try:
        with op.open(req, timeout=300) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"Falha na análise: {e.read().decode(errors='replace')[:300]}")
        return 1

    m = res["measurements"]
    def f(v, s=""):
        return f"{v:.0f}{s}" if isinstance(v, (int, float)) else "—"

    print(f"\n{'=' * 62}\n{res['summary']}\n{'=' * 62}")
    print(f"\nMEDIDAS")
    print(f"  FC {f(m['heart_rate_bpm'], ' bpm')}   PR {f(m['pr_ms'], ' ms')}   "
          f"QRS {f(m['qrs_ms'], ' ms')}   QTcF {f(m['qtc_fridericia_ms'], ' ms')}")
    print(f"  batimentos: {m['n_beats']}   duração: {f(m['duration_s'], ' s')}")

    print(f"\nACHADOS")
    for a in res["findings"]:
        print(f"  [{a['severity'].upper():9s}] {a['label']}")
        print(f"              {a['criteria']}")

    if res.get("deep_learning"):
        print(f"\nDEEP LEARNING (PTB-XL)")
        for p in res["deep_learning"]:
            print(f"  {p['label']:38s} {p['probability'] * 100:5.1f}%")

    print(f"\nOBSERVAÇÕES TÉCNICAS")
    for w in res["quality"]["warnings"]:
        print(f"  · {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))

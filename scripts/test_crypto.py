"""Testes da cifragem em repouso.

Não basta o sistema funcionar com cifragem ligada: é preciso provar que o dado
clínico não está legível no arquivo. Estes testes olham os bytes do banco e da
trilha de auditoria diretamente.

Uso (com a API rodando em :8000):
    .venv\\Scripts\\python scripts\\test_crypto.py
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.auth import crypto, service  # noqa: E402
from backend.app.auth.db import DB_PATH, init_db, transacao  # noqa: E402
from backend.app.reporting.report import AUDIT_DIR  # noqa: E402
from scripts.test_auth import (AMOSTRA, cliente, enviar_ecg,  # noqa: E402
                               pedir)

falhas: list[str] = []


def checar(condicao: bool, descricao: str) -> None:
    print(f"  [{'OK ' if condicao else 'FALHA'}] {descricao}")
    if not condicao:
        falhas.append(descricao)


def bytes_do_banco() -> bytes:
    dados = DB_PATH.read_bytes()
    for extra in (DB_PATH.with_suffix(".db-wal"), DB_PATH.with_suffix(".db-shm")):
        if extra.exists():
            dados += extra.read_bytes()
    return dados


def main() -> int:
    init_db()
    sufixo = uuid.uuid4().hex[:8]
    email = f"cripto.{sufixo}@teste.local"
    nome = f"Dra. Cripto {sufixo}"
    senha = "CifragemTeste2026"
    registro = f"CRM-{sufixo}"

    print("\n1. Envelope de cifragem")
    claro = "achado clínico sensível"
    cifrado = crypto.cifrar(claro)
    checar(cifrado != claro and claro not in cifrado,
           "texto cifrado não contém o original")
    checar(crypto.decifrar(cifrado) == claro, "decifragem devolve o original")
    checar(crypto.cifrar(claro) != crypto.cifrar(claro),
           "duas cifragens do mesmo texto diferem (nonce aleatório)")
    checar(crypto.parece_cifrado(cifrado) and not crypto.parece_cifrado(claro),
           "detecção de registro cifrado funciona")

    print("\n2. Cifragem autenticada detecta adulteração")
    import base64
    bruto = bytearray(base64.b64decode(cifrado))
    bruto[-1] ^= 0x01  # altera um bit do texto cifrado
    adulterado = base64.b64encode(bytes(bruto)).decode()
    try:
        crypto.decifrar(adulterado)
        checar(False, "registro adulterado deveria ser recusado")
    except crypto.CryptoError:
        checar(True, "registro adulterado é recusado, não aceito silenciosamente")

    print("\n3. Chave errada não decifra")
    original = crypto._chave
    try:
        crypto._chave = bytes(32)
        try:
            crypto.decifrar(cifrado)
            checar(False, "chave errada deveria falhar")
        except crypto.CryptoError:
            checar(True, "chave errada não recupera o conteúdo")
    finally:
        crypto._chave = original

    print("\n4. Índice cego permite busca sem expor o e-mail")
    idx = crypto.indice_cego(email)
    checar(idx == crypto.indice_cego(email.upper()),
           "índice é estável para o mesmo e-mail (normalizado)")
    checar(idx != crypto.indice_cego("outro@teste.local"),
           "índices de e-mails distintos diferem")
    checar("@" not in idx and email.split("@")[0] not in idx,
           "índice não contém o e-mail")

    print("\n5. Conta gravada não aparece em texto puro no arquivo")
    service.criar_usuario(email, nome, senha, professional_id=registro)
    with transacao() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    disco = bytes_do_banco()
    checar(email.encode() not in disco, "e-mail ausente do arquivo do banco")
    checar(nome.encode() not in disco, "nome ausente do arquivo do banco")
    checar(registro.encode() not in disco, "registro profissional ausente do arquivo")
    checar(senha.encode() not in disco, "senha ausente do arquivo")
    checar(not re.search(rb"[A-Za-z0-9._%+-]{3,}@[A-Za-z0-9.-]{3,}\.[A-Za-z]{2,}", disco),
           "nenhum e-mail legível em todo o arquivo do banco")

    print("\n6. Laudo gravado não aparece em texto puro")
    op = cliente()
    st, _ = pedir(op, "/api/auth/login", {"email": email, "senha": senha})
    checar(st == 200, f"login com conta cifrada funciona (obtido {st})")
    st, analise = enviar_ecg(op, AMOSTRA)
    checar(st == 200, f"análise gravada com sucesso (obtido {st})")
    resumo = analise.get("summary", "")
    with transacao() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    disco = bytes_do_banco()
    checar(resumo.encode() not in disco,
           "conclusão do laudo ausente do arquivo do banco")
    checar(b"fibrila" not in disco.lower() and b"taquicardia" not in disco.lower(),
           "nenhum termo de achado clínico legível no arquivo")

    print("\n7. Laudo continua recuperável pelo dono")
    st, corpo = pedir(op, f"/api/report/{analise['analysis_id']}/pdf")
    checar(st == 200 and corpo.get("pdf_ok") is True,
           f"PDF gerado a partir do registro cifrado (obtido {st})")
    st, lista = pedir(op, "/api/analyses")
    ids = [x["analysis_id"] for x in (lista.get("analyses") or [])]
    checar(analise["analysis_id"] in ids, "histórico lista a análise decifrada")

    print("\n8. Trilha de auditoria cifrada")
    arquivos = sorted(AUDIT_DIR.glob("*.jsonl")) if AUDIT_DIR.exists() else []
    checar(bool(arquivos), "trilha de auditoria existe")
    if arquivos:
        conteudo = arquivos[-1].read_text(encoding="utf-8")
        ultima = json.loads(conteudo.strip().splitlines()[-1])
        checar(set(ultima) == {"ts", "analysis_id", "dados"},
               "linha expõe apenas carimbo de tempo e identificador")
        checar(email not in conteudo and resumo not in conteudo,
               "nenhum dado clínico legível na trilha")
        recuperado = json.loads(crypto.decifrar(ultima["dados"]))
        checar(recuperado.get("operator_email") == email,
               "trilha é recuperável com a chave (rastreabilidade preservada)")

    service.desativar_usuario(email)
    with transacao() as conn:
        conn.execute("DELETE FROM login_attempts")

    print(f"\n{'TODOS OS TESTES DE CIFRAGEM PASSARAM' if not falhas else f'{len(falhas)} FALHA(S): ' + '; '.join(falhas)}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

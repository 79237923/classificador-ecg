"""Administração da chave de cifragem e leitura da trilha de auditoria.

Uso:
    python scripts/manage_keys.py gerar
        Imprime uma chave nova em base64 para colocar em CARDIOLAUDO_KEY.
        NÃO altera nada: trocar a chave de um banco já em uso torna os exames
        gravados ilegíveis. Para trocar, use `rodar`.

    python scripts/manage_keys.py conferir
        Diz qual chave está em uso e se o banco está integralmente cifrado.

    python scripts/manage_keys.py ler-auditoria [--mes 202608] [--limite 20]
        Decifra e exibe a trilha de auditoria.

    python scripts/manage_keys.py rodar --nova-chave <base64>
        Rotação: decifra tudo com a chave atual e regrava com a nova —
        contas, laudos e trilha de auditoria. Faça backup do banco antes.
"""
from __future__ import annotations

import argparse
import base64
import json
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.auth import crypto  # noqa: E402
from backend.app.auth.db import init_db, precisa_migrar_cifragem, transacao  # noqa: E402
from backend.app.reporting.report import AUDIT_DIR  # noqa: E402


def cmd_gerar() -> int:
    chave = base64.b64encode(secrets.token_bytes(crypto.KEY_BYTES)).decode()
    print("Chave de cifragem (base64, 32 bytes):\n")
    print(f"  {chave}\n")
    print("Configure-a como variável de ambiente antes de subir o servidor:")
    print(f"  PowerShell : $env:CARDIOLAUDO_KEY = '{chave}'")
    print(f"  Linux/macOS: export CARDIOLAUDO_KEY='{chave}'\n")
    print("Guarde-a em cofre de segredos, com backup separado do banco.")
    print("Sem esta chave, os exames já gravados não podem ser recuperados —")
    print("não existe caminho de recuperação, por definição.")
    return 0


def cmd_conferir() -> int:
    import os
    origem = ("variável de ambiente CARDIOLAUDO_KEY" if os.getenv(crypto.ENV_KEY)
              else f"arquivo {crypto.KEY_FILE}" if crypto.KEY_FILE.exists()
              else "nenhuma")
    print(f"Origem da chave : {origem}")
    try:
        crypto.carregar_chave()
        print("Chave carregada : sim")
    except crypto.CryptoError as exc:
        print(f"Chave carregada : NÃO — {exc}")
        return 1

    init_db()
    with transacao() as conn:
        pendentes = precisa_migrar_cifragem(conn)
        n_users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        n_analises = conn.execute("SELECT COUNT(*) AS n FROM analyses").fetchone()["n"]
        amostra = conn.execute(
            "SELECT email_enc FROM users LIMIT 1").fetchone()

    print(f"Contas          : {n_users}")
    print(f"Análises        : {n_analises}")
    if pendentes:
        print(f"ATENÇÃO         : colunas em texto puro ainda presentes em "
              f"{', '.join(pendentes)}.")
        print("                  Rode: python scripts/migrate_encrypt.py")
        return 1

    if amostra:
        try:
            crypto.decifrar(amostra["email_enc"])
            print("Decifragem      : ok (a chave corresponde aos dados gravados)")
        except crypto.CryptoError as exc:
            print(f"Decifragem      : FALHOU — {exc}")
            return 1
    print("Banco integralmente cifrado.")
    return 0


def cmd_ler_auditoria(mes: str | None, limite: int) -> int:
    arquivos = sorted(AUDIT_DIR.glob(f"{mes or ''}*.jsonl")) if AUDIT_DIR.exists() else []
    if not arquivos:
        print(f"Nenhum arquivo de auditoria em {AUDIT_DIR}.")
        return 0

    linhas = []
    for arq in arquivos:
        for linha in arq.read_text(encoding="utf-8").splitlines():
            if linha.strip():
                linhas.append((arq.name, linha))

    for nome, linha in linhas[-limite:]:
        try:
            env = json.loads(linha)
        except json.JSONDecodeError:
            print(f"[{nome}] linha ilegível")
            continue
        if "dados" not in env:
            print(f"[{nome}] {env.get('ts', '?')} — registro em texto puro (anterior à cifragem)")
            continue
        try:
            dados = json.loads(crypto.decifrar(env["dados"]))
        except crypto.CryptoError as exc:
            print(f"[{nome}] {env.get('ts', '?')} — não decifrado: {exc}")
            continue
        origem = dados.get("source") or {}
        print(f"[{env['ts']}] {dados.get('analysis_id', '?')} · "
              f"operador {dados.get('operator_email', '?')} · "
              f"arquivo {origem.get('filename', '?')}")
        print(f"    {dados.get('summary', '')}")
    return 0


def cmd_rodar(nova_b64: str) -> int:
    try:
        nova = base64.b64decode(nova_b64, validate=True)
    except Exception:
        print("A nova chave não é base64 válido.")
        return 1
    if len(nova) != crypto.KEY_BYTES:
        print(f"A nova chave deve ter {crypto.KEY_BYTES} bytes.")
        return 1

    try:
        crypto.carregar_chave()
    except crypto.CryptoError as exc:
        print(f"Chave atual indisponível: {exc}")
        return 1

    init_db()
    with transacao() as conn:
        if precisa_migrar_cifragem(conn):
            print("Há dados em texto puro. Rode migrate_encrypt.py primeiro.")
            return 1
        users = conn.execute(
            "SELECT id, email_enc, full_name_enc, professional_id_enc FROM users"
        ).fetchall()
        analises = conn.execute("SELECT id, payload_enc FROM analyses").fetchall()

        # Decifra tudo com a chave atual antes de trocar.
        claros_u = [(r["id"], crypto.decifrar(r["email_enc"]),
                     crypto.decifrar(r["full_name_enc"]),
                     crypto.decifrar(r["professional_id_enc"])) for r in users]
        claros_a = [(r["id"], crypto.decifrar(r["payload_enc"])) for r in analises]

    # A trilha de auditoria é decifrada com a chave antiga ANTES da troca.
    # Sem isto ela ficaria ilegível após a rotação, e a rastreabilidade exigida
    # para SaMD dependeria de guardar indefinidamente todas as chaves antigas.
    claros_aud: list[tuple[Path, list[str]]] = []
    ilegiveis = 0
    if AUDIT_DIR.exists():
        for arq in sorted(AUDIT_DIR.glob("*.jsonl")):
            registros = []
            for linha in arq.read_text(encoding="utf-8").splitlines():
                if not linha.strip():
                    continue
                obj = json.loads(linha)
                if "dados" in obj:
                    try:
                        obj["_claro"] = crypto.decifrar(obj["dados"])
                    except crypto.CryptoError:
                        # Registro de uma chave anterior: preservado como está.
                        # Abortar a rotação por causa dele deixaria o sistema
                        # sem poder rodar a chave nunca mais.
                        ilegiveis += 1
                registros.append(json.dumps(obj, ensure_ascii=False))
            claros_aud.append((arq, registros))
    if ilegiveis:
        print(f"Aviso: {ilegiveis} registro(s) de auditoria não puderam ser "
              "decifrados com a chave atual (provavelmente de uma chave "
              "anterior). Serão mantidos intactos e continuarão ilegíveis.")

    with transacao() as conn:
        crypto._chave = nova  # troca em memória para regravar
        for uid, email, nome, registro in claros_u:
            conn.execute(
                "UPDATE users SET email_idx = ?, email_enc = ?, full_name_enc = ?,"
                " professional_id_enc = ? WHERE id = ?",
                (crypto.indice_cego(email), crypto.cifrar(email),
                 crypto.cifrar(nome), crypto.cifrar(registro), uid))
        for aid, payload in claros_a:
            conn.execute("UPDATE analyses SET payload_enc = ? WHERE id = ?",
                         (crypto.cifrar(payload), aid))
        # Sessões viram inválidas: os tokens continuam válidos, mas é mais
        # seguro exigir novo login após a troca da chave mestra.
        conn.execute("DELETE FROM sessions")

    n_aud = 0
    for arq, registros in claros_aud:
        saida = []
        for linha in registros:
            obj = json.loads(linha)
            if "_claro" in obj:
                obj["dados"] = crypto.cifrar(obj.pop("_claro"))
                n_aud += 1
            saida.append(json.dumps(obj, ensure_ascii=False))
        arq.write_text("\n".join(saida) + "\n", encoding="utf-8")

    print(f"Rotação concluída: {len(claros_u)} contas, {len(claros_a)} análises e "
          f"{n_aud} registros de auditoria regravados com a nova chave.")
    print("Todas as sessões foram encerradas — os usuários precisarão entrar de novo.")

    # A chave só é publicada DEPOIS de a regravação ter dado certo. Atualizá-la
    # antes (ou fora deste script) é o caminho mais curto para perder o
    # histórico clínico: se a rotação falhar no meio, chave e dados divergem e
    # não há como voltar.
    import os
    if os.getenv(crypto.ENV_KEY):
        print("\nA chave em uso vem da variável de ambiente. Atualize-a AGORA,")
        print("antes de reiniciar o servidor:\n")
        print(f"  {nova_b64}\n")
        print("Guarde a chave anterior até confirmar que o sistema subiu bem.")
    else:
        anterior = crypto.KEY_FILE.with_suffix(
            f".key.anterior-{datetime.now(timezone.utc):%Y%m%d%H%M%S}")
        if crypto.KEY_FILE.exists():
            shutil.copy2(crypto.KEY_FILE, anterior)
        crypto.KEY_FILE.write_text(nova_b64, encoding="ascii")
        print(f"\nArquivo de chave atualizado: {crypto.KEY_FILE}")
        print(f"Cópia da chave anterior em : {anterior}")
        print("Apague a cópia anterior assim que confirmar que o sistema subiu bem.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Chave de cifragem do CardioLaudo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gerar", help="gera uma chave nova (não altera nada)")
    sub.add_parser("conferir", help="verifica chave e estado da cifragem")
    p_aud = sub.add_parser("ler-auditoria", help="decifra a trilha de auditoria")
    p_aud.add_argument("--mes", help="filtro AAAAMM, ex.: 202608")
    p_aud.add_argument("--limite", type=int, default=20)
    p_rot = sub.add_parser("rodar", help="troca a chave mestra")
    p_rot.add_argument("--nova-chave", required=True)

    args = ap.parse_args()
    if args.cmd == "gerar":
        return cmd_gerar()
    if args.cmd == "conferir":
        return cmd_conferir()
    if args.cmd == "ler-auditoria":
        return cmd_ler_auditoria(args.mes, args.limite)
    return cmd_rodar(args.nova_chave)


if __name__ == "__main__":
    sys.exit(main())

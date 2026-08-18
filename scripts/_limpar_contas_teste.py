"""Remove contas de teste deixadas pelas suítes automatizadas.

Apaga definitivamente as contas cujo e-mail termina em domínios de teste
conhecidos. NÃO toca em contas reais. Uso pontual de limpeza.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.auth.crypto import decifrar  # noqa: E402
from backend.app.auth.db import init_db, transacao  # noqa: E402

DOMINIOS_TESTE = ("@teste.local", "@clinica.local", "@t.local")
PREFIXOS_TESTE = ("smoke.", "medico.a.", "medico.b.", "cripto.", "retencao.",
                  "agenda.", "admin.", "medico.", "novo.", "ui.teste.",
                  "varredura.", "fraca.")


def e_teste(email: str) -> bool:
    e = (email or "").lower()
    return e.endswith(DOMINIOS_TESTE) or any(e.startswith(p) for p in PREFIXOS_TESTE)


def main() -> int:
    init_db()
    with transacao() as conn:
        rows = conn.execute("SELECT id, email_enc FROM users").fetchall()
        alvo = [(r["id"], decifrar(r["email_enc"])) for r in rows]
        alvo = [(i, e) for i, e in alvo if e_teste(e)]
        for i, e in alvo:
            conn.execute("DELETE FROM analyses WHERE user_id = ?", (i,))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (i,))
            conn.execute("DELETE FROM users WHERE id = ?", (i,))
    print(f"Removidas {len(alvo)} conta(s) de teste.")
    for _, e in alvo[:50]:
        print(f"  - {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

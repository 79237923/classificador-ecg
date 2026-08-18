"""Resume o resultado da pesquisa de hospedagem gratuita."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(path: str) -> int:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    r = d.get("result", d)

    conf = r.get("confirmadas", [])
    ref = r.get("refutadas", [])
    print(f"CONFIRMADAS: {len(conf)}   REFUTADAS: {len(ref)}\n")
    for c in sorted(conf, key=lambda x: {"excelente": 0, "boa": 1, "limitada": 2}.get(x.get("adequacao"), 9)):
        print(f"[{c.get('adequacao','?').upper():<9}] {c.get('nome')}")
        print(f"    recursos : {c.get('recursos','')[:110]}")
        print(f"    persist. : {c.get('disco_persistente')}   dorme: {str(c.get('dorme'))[:50]}")
        print(f"    cartão   : {str(c.get('exige_cartao'))[:60]}")
        print(f"    pegadinha: {str(c.get('pegadinhas',''))[:160]}")
        print()
    print("--- REFUTADAS ---")
    for x in ref:
        print(f"  · {x.get('nome')}: {str(x.get('motivo',''))[:130]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

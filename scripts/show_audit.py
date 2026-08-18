"""Resume o relatório da auditoria do workflow em texto legível."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(path: str):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    r = data.get("result", data)
    conf = r.get("confirmados", [])
    ref = r.get("refutados_resumo", [])

    print(f"CONFIRMADOS: {len(conf)}   REFUTADOS: {len(ref)}\n")
    for i, c in enumerate(conf, 1):
        loc = c.get("arquivo", "")
        if c.get("linha"):
            loc += f":{c['linha']}"
        print(f"{i:2d}. [{c.get('gravidade', '?').upper()}] ({c.get('dimensao', '')}) {c.get('titulo', '')}")
        print(f"    local  : {loc}")
        print(f"    correção: {c.get('correcao_sugerida', '')[:300]}")
        print()

    print("\n--- REFUTADOS (descartados pelo revisor cético) ---")
    for x in ref:
        print(f"  · {x.get('titulo', '')}")


if __name__ == "__main__":
    main(sys.argv[1])

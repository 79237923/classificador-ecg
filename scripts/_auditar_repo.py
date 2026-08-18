"""Auditoria de segurança antes de publicar o repositório.

Verifica o que ficaria exposto num repositório público: arquivos sensíveis no
histórico do git e segredos escritos dentro dos arquivos versionados. Publicar
é irreversível na prática — código público pode ser clonado e indexado antes de
qualquer correção.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARQUIVOS_PROIBIDOS = re.compile(
    r"(^|/)(secrets/|\.env$)|\.key$|cardiolaudo\.db|/audit/|\.pt$|node_modules/")

# Padrões de segredo que não podem aparecer no conteúdo dos arquivos.
SEGREDOS = [
    ("senha de demonstração", re.compile(r"CardioLaudo2026")),
    ("token do GitHub", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("chave privada", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")),
    ("chave de cifragem embutida", re.compile(
        r"CARDIOLAUDO_KEY\s*[:=]\s*[\"'][A-Za-z0-9+/=]{30,}")),
    ("credencial AWS", re.compile(r"AKIA[0-9A-Z]{16}")),
]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def main() -> int:
    problemas: list[str] = []

    print("1. Arquivos sensíveis no histórico do git")
    historico = {l.strip() for l in git("log", "--all", "--pretty=format:",
                                        "--name-only").splitlines() if l.strip()}
    vazados = sorted(f for f in historico if ARQUIVOS_PROIBIDOS.search(f))
    if vazados:
        problemas.append(f"{len(vazados)} arquivo(s) sensível(is) no histórico")
        for f in vazados[:20]:
            print(f"   VAZOU: {f}")
    else:
        print(f"   nenhum ({len(historico)} arquivos no histórico)")

    print("\n2. Segredos dentro dos arquivos versionados")
    versionados = [f for f in git("ls-files").splitlines() if f.strip()]
    achados = 0
    for arquivo in versionados:
        caminho = ROOT / arquivo
        if not caminho.exists() or caminho.stat().st_size > 2_000_000:
            continue
        try:
            texto = caminho.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for rotulo, padrao in SEGREDOS:
            for m in padrao.finditer(texto):
                linha = texto[:m.start()].count("\n") + 1
                print(f"   ENCONTRADO ({rotulo}): {arquivo}:{linha}")
                achados += 1
    if achados:
        problemas.append(f"{achados} segredo(s) no conteúdo dos arquivos")
    else:
        print(f"   nenhum ({len(versionados)} arquivos verificados)")

    print("\n3. Dados pessoais em arquivos versionados")
    pessoais = 0
    for arquivo in versionados:
        caminho = ROOT / arquivo
        if not caminho.exists() or caminho.stat().st_size > 2_000_000:
            continue
        try:
            texto = caminho.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "henrique.rezends.fotografia" in texto:
            print(f"   e-mail pessoal em: {arquivo}")
            pessoais += 1
    if pessoais:
        print("   (aceitável se for autoria; revise se não for intencional)")
    else:
        print("   nenhum")

    print("\n4. Tamanho do que será enviado")
    total = sum((ROOT / f).stat().st_size for f in versionados if (ROOT / f).exists())
    print(f"   {len(versionados)} arquivos, {total / 1024 / 1024:.1f} MB")
    maiores = sorted(((ROOT / f).stat().st_size, f) for f in versionados
                     if (ROOT / f).exists())[-5:]
    for tam, f in reversed(maiores):
        print(f"     {tam / 1024:>8.0f} KB  {f}")

    print()
    if problemas:
        print("BLOQUEADO — resolva antes de publicar: " + "; ".join(problemas))
        return 1
    print("SEGURO PARA PUBLICAR: nenhum segredo ou dado sensível seria exposto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

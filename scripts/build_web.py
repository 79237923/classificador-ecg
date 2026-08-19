"""Monta o pacote da versão que roda no navegador (GitHub Pages).

O motor de análise é o MESMO código Python do servidor — é isso que preserva
toda a validação feita contra o PTB-XL. Aqui ele apenas é empacotado num .zip
que o Pyodide descompacta no sistema de arquivos virtual do navegador.

O NeuroKit2 vai junto porque não é instalável pelo micropip: não publica wheel
puro no PyPI e exige `pandas<3.0.0`, enquanto o Pyodide traz o 3.0.2. Copiar os
.py contorna as duas coisas (verificado: as três funções que usamos rodam
normalmente sobre o pandas 3).

Uso:
    .venv\\Scripts\\python scripts\\build_web.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
BUNDLE = WEB / "cardiolaudo.zip"
PONTE = WEB / "analise.py"
VERSAO = WEB / "versao.json"

# Só os módulos de análise. Ficam de fora: auth (não há servidor), main.py
# (API), retention/purge/scheduler (persistência) — nada disso faz sentido
# quando o exame nunca sai do computador de quem acessa.
MODULOS = [
    "backend/__init__.py",
    "backend/app/__init__.py",
    "backend/app/schemas.py",
    "backend/app/ingestion",
    "backend/app/processing",
    "backend/app/classification",
    "backend/app/reporting",
]

AMOSTRAS = [
    ("ecg12_laudo_baixa.png", "Laudo de 12 derivações (imagem)"),
    ("ecg_normal_imagem.png", "Traçado de ritmo (imagem)"),
    ("ecg_normal_12d.csv", "Sinal digital de 12 derivações"),
    ("ecg_taquicardia.csv", "Taquicardia (sinal digital)"),
    ("ecg_irregular.csv", "Ritmo irregular (sinal digital)"),
]


def adicionar(zf: zipfile.ZipFile, origem: Path, destino: str) -> int:
    """Grava um arquivo .py no zip, ignorando cache."""
    if origem.name == "__pycache__" or origem.suffix != ".py":
        return 0
    zf.write(origem, destino)
    return 1


def adicionar_arvore(zf: zipfile.ZipFile, raiz: Path, prefixo: str) -> int:
    n = 0
    for item in sorted(raiz.rglob("*.py")):
        if "__pycache__" in item.parts:
            continue
        n += adicionar(zf, item, f"{prefixo}/{item.relative_to(raiz).as_posix()}")
    return n


def impressao(caminho: Path) -> str:
    """Hash curto do conteúdo, usado para invalidar o cache do navegador."""
    return hashlib.sha256(caminho.read_bytes()).hexdigest()[:12]


def gravar_versao() -> dict[str, str]:
    """Publica a impressão digital do motor.

    O navegador guarda o zip (680 kB) e a ponte Python em cache entre visitas,
    porque baixá-los a cada acesso seria desperdício. O preço disso é que, sem
    trocar a URL, uma atualização nunca chega a quem já visitou o site. Com o
    hash do conteúdo na query, conteúdo novo vira URL nova — e conteúdo igual
    continua vindo do cache, de graça.
    """
    versao = {"motor": impressao(BUNDLE), "ponte": impressao(PONTE)}
    VERSAO.write_text(json.dumps(versao, indent=2) + "\n", encoding="utf-8")
    return versao


def localizar_neurokit() -> Path | None:
    """Encontra o NeuroKit2 instalado, seja qual for o sistema.

    Não assume o caminho da venv do Windows: no CI do GitHub (Linux) o pacote
    fica noutro lugar, e um caminho fixo quebraria a publicação automática.
    """
    try:
        import neurokit2
        return Path(neurokit2.__file__).parent
    except ImportError:
        return None


def main() -> int:
    WEB.mkdir(parents=True, exist_ok=True)

    # Sem a ponte o site não analisa nada. Falhar aqui é melhor que publicar um
    # versao.json incompleto, que faria o navegador cair no cache sem validade.
    if not PONTE.exists():
        print(f"Ponte de análise não encontrada: {PONTE}")
        return 1

    nk_origem = localizar_neurokit()
    if nk_origem is None or not nk_origem.exists():
        print("NeuroKit2 não encontrado no ambiente atual.")
        print("Instale as dependências primeiro: pip install -r requirements.txt")
        return 1
    print(f"NeuroKit2 localizado em: {nk_origem}")

    print("Empacotando o motor de análise…")
    with zipfile.ZipFile(BUNDLE, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        n_app = 0
        for alvo in MODULOS:
            caminho = ROOT / alvo
            if caminho.is_dir():
                n_app += adicionar_arvore(zf, caminho, alvo)
            elif caminho.exists():
                n_app += adicionar(zf, caminho, alvo)
            else:
                # backend/__init__.py não existe no repositório; o pacote
                # precisa dele para ser importável.
                if alvo.endswith("__init__.py"):
                    zf.writestr(alvo, "")
                    n_app += 1
        print(f"  motor CardioLaudo : {n_app} arquivos")

        n_nk = adicionar_arvore(zf, nk_origem, "neurokit2")
        print(f"  NeuroKit2         : {n_nk} arquivos")

    tam = BUNDLE.stat().st_size / 1024
    print(f"  → {BUNDLE.name}: {tam:.0f} KB")

    # Amostras para quem quiser testar sem ter um ECG à mão
    destino_amostras = WEB / "exemplos"
    destino_amostras.mkdir(exist_ok=True)
    copiadas = []
    for nome, rotulo in AMOSTRAS:
        origem = ROOT / "data" / "samples" / nome
        if origem.exists():
            shutil.copy2(origem, destino_amostras / nome)
            copiadas.append((nome, rotulo, origem.stat().st_size))
    print(f"\nExemplos copiados: {len(copiadas)}")
    for nome, rotulo, tam in copiadas:
        print(f"  {nome:<28} {tam / 1024:>7.0f} KB  {rotulo}")

    versao = gravar_versao()
    print(f"\nImpressão do motor: {versao['motor']}  "
          f"ponte: {versao.get('ponte', '—')}")
    print("  (é o que faz uma publicação nova chegar a quem já visitou o site)")

    total = sum(f.stat().st_size for f in WEB.rglob("*") if f.is_file())
    print(f"\nTamanho total do site: {total / 1024 / 1024:.1f} MB")
    print("(o Pyodide e as bibliotecas vêm do CDN, não do repositório)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

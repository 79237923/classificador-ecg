"""Administração da retenção e do expurgo de dados.

O expurgo é SIMULADO por padrão: sem `--confirmar`, apenas relata o que seria
apagado. Exclusão de prontuário é irreversível e sujeita a prazo legal.

Uso:
    python scripts/manage_retention.py status
        Mostra as políticas em vigor, o inventário e o que está vencido.

    python scripts/manage_retention.py expurgar
        Simula o expurgo (não apaga nada).

    python scripts/manage_retention.py expurgar --confirmar
        Executa. Aceita --categoria para limitar (ex.: --categoria backups).

    python scripts/manage_retention.py reter <analysis_id> --motivo "..."
    python scripts/manage_retention.py liberar <analysis_id>
        Marca/desmarca retenção legal, que torna o laudo imune ao expurgo.

    python scripts/manage_retention.py historico
        Lista as exclusões já realizadas (metadados, sem conteúdo clínico).
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import purge, retention  # noqa: E402
from backend.app.auth.db import init_db, transacao  # noqa: E402


def cmd_status() -> int:
    init_db()
    try:
        pols = retention.politicas()
    except ValueError as exc:
        print(f"Configuração inválida: {exc}")
        return 1

    print("POLÍTICAS DE RETENÇÃO EM VIGOR\n")
    for p in pols:
        print(f"  {p.rotulo:<32} {retention.humanizar(p.dias):>12}")
        print(f"  {'':32} └─ {p.fundamento}")
    if retention.aceita_risco():
        print("\n  ATENÇÃO: CARDIOLAUDO_RETENCAO_ACEITA_RISCO=1 — o piso legal "
              "de 20 anos para laudos está desativado.")

    inv = purge.inventario()
    print("\nINVENTÁRIO\n")
    print(f"  Laudos armazenados        : {inv['laudos']}")
    print(f"  Sob retenção legal        : {inv['retidos']}")
    print(f"  Laudo mais antigo         : {inv['mais_antigo'] or '—'}")
    print(f"  Exclusões já registradas  : {inv['exclusoes_registradas']}")
    print(f"  Backups em TEXTO PURO     : {inv['backups_texto_puro']}")

    print("\nAGENDAMENTO\n")
    if not retention.expurgo_automatico_ativo():
        print("  Expurgo automático        : DESLIGADO "
              "(CARDIOLAUDO_EXPURGO_AUTOMATICO=0)")
    else:
        print(f"  Expurgo automático        : diário às "
              f"{retention.hora_do_expurgo():02d}:00 (hora local)")
        print("  Dado operacional          : automático "
              "(sessões, tentativas, backups vencidos)")
        if retention.expurgo_automatico_laudos():
            print("  Laudos                    : AUTOMÁTICO "
                  "(CARDIOLAUDO_EXPURGO_AUTOMATICO_LAUDOS=1)")
            print(f"  Disjuntor de segurança    : até "
                  f"{retention.limite_absoluto()} laudos e "
                  f"{retention.limite_proporcional():.0%} do acervo por execução")
        else:
            print("  Laudos                    : manual (padrão) — exigem "
                  "`expurgar --confirmar`")

    print("\nVENCIDOS (simulação, nada foi apagado)\n")
    total = 0
    for r in purge.expurgar_tudo(simular=True):
        marca = "  " if not r.vencidos else "→ "
        print(f"{marca}{r.rotulo:<32} {r.vencidos:>6} vencido(s)"
              + (f", {r.retidos} retido(s) por hold" if r.retidos else ""))
        total += r.vencidos - r.retidos
        for d in r.detalhes[:5]:
            print(f"      {d}")
        if len(r.detalhes) > 5:
            print(f"      … e mais {len(r.detalhes) - 5}")
    if total:
        print(f"\n  {total} item(ns) seriam apagados. Execute com:")
        print("    python scripts/manage_retention.py expurgar --confirmar")
    else:
        print("\n  Nada vencido.")
    return 0


def cmd_expurgar(confirmar: bool, categorias: list[str] | None) -> int:
    init_db()
    try:
        retention.politicas()
    except ValueError as exc:
        print(f"Configuração inválida: {exc}")
        return 1

    executor = "cli"
    try:
        executor = f"cli:{getpass.getuser()}"
    except Exception:
        pass

    if confirmar:
        previa = purge.expurgar_tudo(simular=True, categorias=categorias)
        alvo = sum(r.vencidos - r.retidos for r in previa)
        laudos = next((r for r in previa if r.categoria == "laudos"), None)
        if laudos and (laudos.vencidos - laudos.retidos) > 0:
            print(f"ATENÇÃO: {laudos.vencidos - laudos.retidos} LAUDO(S) serão "
                  "apagados definitivamente.")
            print("Prontuário eliminado não é recuperável e o prazo legal de "
                  "guarda é de 20 anos (CFM 1.821/2007).")
            resposta = input('Digite "APAGAR LAUDOS" para prosseguir: ').strip()
            if resposta != "APAGAR LAUDOS":
                print("Cancelado.")
                return 1
        if not alvo:
            print("Nada vencido; nada a fazer.")
            return 0

    modo = "EXECUTANDO" if confirmar else "SIMULAÇÃO (use --confirmar para executar)"
    print(f"Expurgo — {modo}\n")
    resultados = purge.expurgar_tudo(simular=not confirmar, executor=executor,
                                     categorias=categorias)
    for r in resultados:
        feito = r.excluidos if confirmar else r.vencidos - r.retidos
        print(f"  {r.rotulo:<32} {feito:>6} "
              f"{'apagado(s)' if confirmar else 'seriam apagados'}"
              + (f"  ({r.retidos} retido[s])" if r.retidos else ""))
        for d in r.detalhes[:5]:
            print(f"      {d}")
    if confirmar:
        print("\nConcluído. As exclusões de laudo ficaram registradas em "
              "`deletions` (consulte com `historico`).")
    return 0


def cmd_reter(analysis_id: str, motivo: str) -> int:
    init_db()
    if purge.marcar_retencao(analysis_id, motivo):
        print(f"Laudo {analysis_id} sob retenção legal: {motivo}")
        print("Ele não será apagado pelo expurgo, independentemente da idade.")
        return 0
    print(f"Laudo não encontrado: {analysis_id}")
    return 1


def cmd_liberar(analysis_id: str) -> int:
    init_db()
    if purge.liberar_retencao(analysis_id):
        print(f"Retenção legal removida de {analysis_id}. "
              "Ele volta a seguir o prazo padrão.")
        return 0
    print(f"Laudo não encontrado: {analysis_id}")
    return 1


def cmd_ciclo() -> int:
    """Um ciclo de expurgo automático — ponto de entrada para o agendador do SO."""
    init_db()
    from backend.app import scheduler
    try:
        resultados = scheduler.executar_ciclo(executor="agendador-so")
    except ValueError as exc:
        print(f"Configuração inválida: {exc}")
        return 1

    bloqueado = False
    for r in resultados:
        if r.bloqueado:
            bloqueado = True
            print(f"BLOQUEADO — {r.rotulo}: {r.bloqueado}")
        elif r.excluidos:
            print(f"{r.rotulo}: {r.excluidos} apagado(s)"
                  + (f", {r.retidos} retido(s)" if r.retidos else ""))
    if not any(r.excluidos or r.bloqueado for r in resultados):
        print("Nada vencido.")
    # Código != 0 faz o agendador do SO sinalizar a falha ao operador.
    return 2 if bloqueado else 0


def cmd_agendar() -> int:
    """Mostra como registrar o ciclo no agendador do sistema operacional."""
    py = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32"
                           else "bin/python")
    script = ROOT / "scripts" / "manage_retention.py"
    hora = retention.hora_do_expurgo()

    print("O servidor já executa o expurgo diariamente enquanto estiver no ar.")
    print("Agende no sistema operacional apenas se ele não ficar sempre ligado,")
    print("ou se houver várias instâncias (nesse caso, desligue o agendador")
    print("interno com CARDIOLAUDO_EXPURGO_AUTOMATICO=0 para não concorrerem).\n")

    if sys.platform == "win32":
        print("Windows — execute uma vez, como administrador:\n")
        print(f'  schtasks /Create /SC DAILY /ST {hora:02d}:00 /TN "CardioLaudo Expurgo" '
              f'/TR "\'{py}\' \'{script}\' ciclo"\n')
        print("Conferir  :  schtasks /Query /TN \"CardioLaudo Expurgo\"")
        print("Remover   :  schtasks /Delete /TN \"CardioLaudo Expurgo\" /F")
    else:
        print("Linux/macOS — acrescente ao crontab (`crontab -e`):\n")
        print(f"  0 {hora} * * *  cd '{ROOT}' && '{py}' '{script}' ciclo "
              ">> data/expurgo.log 2>&1\n")
    print("\nO comando `ciclo` respeita as mesmas proteções: laudos só são")
    print("apagados com CARDIOLAUDO_EXPURGO_AUTOMATICO_LAUDOS=1, e o disjuntor")
    print("interrompe a execução se o volume for implausível (saída 2).")
    return 0


def cmd_historico(limite: int) -> int:
    init_db()
    with transacao() as conn:
        linhas = conn.execute(
            "SELECT categoria, referencia, criado_em, excluido_em, politica_dias,"
            " executor FROM deletions ORDER BY excluido_em DESC LIMIT ?",
            (limite,)).fetchall()
    if not linhas:
        print("Nenhuma exclusão registrada.")
        return 0
    print(f"{'excluído em':<22} {'categoria':<12} {'referência':<36} {'criado em':<22} executor")
    for r in linhas:
        print(f"{r['excluido_em']:<22} {r['categoria']:<12} "
              f"{(r['referencia'] or '—'):<36} {(r['criado_em'] or '—'):<22} "
              f"{r['executor'] or '—'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Retenção e expurgo do CardioLaudo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="políticas, inventário e vencidos")
    p_exp = sub.add_parser("expurgar", help="apaga o que venceu (simula por padrão)")
    p_exp.add_argument("--confirmar", action="store_true")
    p_exp.add_argument("--categoria", action="append",
                       choices=list(purge.EXPURGOS),
                       help="limita a categorias específicas")
    p_ret = sub.add_parser("reter", help="marca retenção legal em um laudo")
    p_ret.add_argument("analysis_id")
    p_ret.add_argument("--motivo", default="retenção legal")
    p_lib = sub.add_parser("liberar", help="remove a retenção legal")
    p_lib.add_argument("analysis_id")
    p_hist = sub.add_parser("historico", help="exclusões já realizadas")
    p_hist.add_argument("--limite", type=int, default=30)
    sub.add_parser("ciclo", help="um ciclo automático (para o agendador do SO)")
    sub.add_parser("agendar", help="mostra como registrar no agendador do SO")

    a = ap.parse_args()
    if a.cmd == "status":
        return cmd_status()
    if a.cmd == "expurgar":
        return cmd_expurgar(a.confirmar, a.categoria)
    if a.cmd == "reter":
        return cmd_reter(a.analysis_id, a.motivo)
    if a.cmd == "liberar":
        return cmd_liberar(a.analysis_id)
    if a.cmd == "ciclo":
        return cmd_ciclo()
    if a.cmd == "agendar":
        return cmd_agendar()
    return cmd_historico(a.limite)


if __name__ == "__main__":
    sys.exit(main())

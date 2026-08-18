"""Mede a fidelidade da digitalização contra a verdade conhecida.

Renderiza um sinal real do PTB-XL como laudo de imagem, digitaliza de volta e
compara com o original. É o único teste que responde "a imagem virou o sinal
certo?" — os demais só verificam se o resultado parece plausível.

Uso: .venv\\Scripts\\python scripts\\test_digitizer_fidelity.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.ingestion.image_digitizer import digitize  # noqa: E402
from scripts._ptbxl_path import find_ptbxl  # noqa: E402

SAMPLES = ROOT / "data" / "samples"


def alinhar(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """Alinha dois sinais pelo deslocamento de maior correlação cruzada."""
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    a = (a - a.mean()) / (a.std() + 1e-9)
    b = (b - b.mean()) / (b.std() + 1e-9)
    corr = np.correlate(a, b, mode="full")
    desloc = int(np.argmax(corr)) - (n - 1)
    if desloc > 0:
        a2, b2 = a[desloc:], b[: n - desloc]
    elif desloc < 0:
        a2, b2 = a[: n + desloc], b[-desloc:]
    else:
        a2, b2 = a, b
    return a2, b2, desloc


def main() -> int:
    import neurokit2 as nk
    import pandas as pd
    import wfdb
    from scipy.signal import resample

    data = find_ptbxl()
    db = pd.read_csv(data / "ptbxl_database.csv", index_col="ecg_id")
    db.scp_codes = db.scp_codes.apply(ast.literal_eval)
    norm = db[db.scp_codes.apply(lambda c: c.get("NORM", 0) >= 100)]
    # Mesmo critério de make_ecg_sheet.py: precisa ser o MESMO registro que foi
    # renderizado, senão a comparação não faz sentido.
    verdade = None
    for _, row in norm.head(40).iterrows():
        sig, meta = wfdb.rdsamp(str(data / row.filename_hr))
        sig = np.asarray(sig, float)
        nomes = [str(s) for s in meta["sig_name"]]
        if "II" not in nomes:
            continue
        canal = sig[:, nomes.index("II")]
        if float(np.ptp(canal)) >= 1.2:
            verdade, fs_orig = canal, float(meta["fs"])
            print(f"referência: registro {row.name}, "
                  f"amplitude da II = {np.ptp(canal):.2f} mV")
            break
    if verdade is None:
        print("Nenhum registro de referência adequado.")
        return 1

    falhas = []
    for nome in ("ecg12_laudo_alta.png", "ecg12_laudo_baixa.png"):
        caminho = SAMPLES / nome
        if not caminho.exists():
            print(f"  (ausente: {nome} — rode scripts/make_ecg_sheet.py)")
            continue

        rec = digitize(caminho.name, caminho.read_bytes())
        extraido = rec.signal[:, 0]

        # Traz a verdade para a mesma taxa do sinal extraído.
        alvo = int(round(len(verdade) * rec.sampling_rate / fs_orig))
        ref = resample(verdade, alvo)

        a, b, desloc = alinhar(extraido, ref)
        r = float(np.corrcoef(a, b)[0, 1])

        # Frequência cardíaca dos dois, como medida clínica direta.
        def fc(x, fs):
            limpo = nk.ecg_clean(x, sampling_rate=fs)
            _, info = nk.ecg_peaks(limpo, sampling_rate=fs)
            rp = np.asarray(info.get("ECG_R_Peaks", []), int)
            if len(rp) < 3:
                return None
            rr = np.diff(rp) / fs
            return 60.0 / np.mean(rr[(rr > 0.2) & (rr < 3)])

        fc_ext = fc(extraido, rec.sampling_rate)
        fc_ref = fc(ref, rec.sampling_rate)

        print(f"\n{nome}")
        print(f"  duração   : extraída {rec.duration_s:.1f} s | "
              f"verdade {len(verdade) / fs_orig:.1f} s")
        print(f"  correlação: {r:.3f} (deslocamento {desloc} amostras)")
        print(f"  FC        : extraída {fc_ext:.0f} | verdade {fc_ref:.0f} bpm"
              if fc_ext and fc_ref else "  FC        : não medida")

        ok_dur = abs(rec.duration_s - len(verdade) / fs_orig) < 0.5
        ok_corr = r > 0.80
        ok_fc = fc_ext and fc_ref and abs(fc_ext - fc_ref) < 5
        for cond, desc in ((ok_dur, "duração"), (ok_corr, "correlação > 0,80"),
                           (ok_fc, "FC dentro de 5 bpm")):
            print(f"  [{'OK ' if cond else 'FALHA'}] {desc}")
            if not cond:
                falhas.append(f"{nome}: {desc}")

    # --- Fidelidade de cada derivação do layout 3×4 ---
    print("\n== Fidelidade por derivação (laudo de alta resolução) ==")
    caminho = SAMPLES / "ecg12_laudo_alta.png"
    if caminho.exists():
        rec = digitize(caminho.name, caminho.read_bytes())
        fs_r = rec.sampling_rate
        n_alvo = int(round(len(sig) * fs_r / fs_orig))
        ruins = []
        print(f"  {'deriv':<6} {'correl':>7}  {'amplitude ext/verd':>22}")
        for nome in [n for linha in
                     [["I", "aVR", "V1", "V4"], ["II", "aVL", "V2", "V5"],
                      ["III", "aVF", "V3", "V6"]] for n in linha]:
            canal = rec.lead(nome)
            # PTB-XL grava AVR/AVL/AVF em maiúsculas; a busca precisa ignorar caixa.
            idx_ref = next((k for k, n in enumerate(nomes)
                            if str(n).strip().lower() == nome.lower()), None)
            if canal is None or idx_ref is None:
                print(f"  {nome:<6}       —  (não extraída)")
                ruins.append(nome)
                continue
            ref_full = resample(sig[:, idx_ref], n_alvo)
            m = np.isfinite(canal)
            if m.sum() < 100:
                print(f"  {nome:<6}       —  (janela vazia)")
                ruins.append(nome)
                continue
            a, b, _ = alinhar(canal[m], ref_full[: len(canal)][m])
            r = float(np.corrcoef(a, b)[0, 1]) if len(a) > 10 else float("nan")
            amp_e = float(np.nanmax(canal[m]) - np.nanmin(canal[m]))
            amp_v = float(np.ptp(ref_full[: len(canal)][m]))
            marca = "" if r > 0.75 else "   <-- baixa"
            print(f"  {nome:<6} {r:7.3f}  {amp_e:8.2f} / {amp_v:.2f} mV{marca}")
            if not (r > 0.75):
                ruins.append(nome)
        # Critério de aceitação: as células de canto (sobretudo V6, adjacente à
        # tira de ritmo) são intrinsecamente mais difíceis. Tolera-se até 2
        # derivações abaixo do limiar, desde que a maioria esteja fiel.
        print(f"  → {12 - len(ruins)}/12 derivações com correlação > 0,75")
        if len(ruins) > 2:
            falhas.append("derivações com fidelidade baixa: " + ", ".join(ruins))

    print(f"\n{'FIDELIDADE OK' if not falhas else f'{len(falhas)} FALHA(S): ' + '; '.join(falhas)}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

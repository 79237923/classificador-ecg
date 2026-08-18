// Carrega o NeuroKit2 da instalação local direto no sistema de arquivos do
// Pyodide, contornando o micropip (que recusa por falta de wheel puro e pelo
// limite pandas<3). O teste responde: o código do NeuroKit2 roda de fato sobre
// as bibliotecas do Pyodide, com pandas 3?
import { loadPyodide } from "pyodide";
import fs from "node:fs";
import path from "node:path";

const ORIGEM = path.resolve("../.venv/Lib/site-packages/neurokit2");

function copiar(py, dirOrigem, dirDestino) {
  let arquivos = 0, bytes = 0;
  py.FS.mkdirTree(dirDestino);
  for (const item of fs.readdirSync(dirOrigem, { withFileTypes: true })) {
    const o = path.join(dirOrigem, item.name);
    const d = `${dirDestino}/${item.name}`;
    if (item.isDirectory()) {
      if (item.name === "__pycache__") continue;
      const r = copiar(py, o, d);
      arquivos += r.arquivos; bytes += r.bytes;
    } else if (item.name.endsWith(".py")) {
      const dados = fs.readFileSync(o);
      py.FS.writeFile(d, dados);
      arquivos++; bytes += dados.length;
    }
  }
  return { arquivos, bytes };
}

const py = await loadPyodide();
await py.loadPackage(["numpy", "scipy", "pandas", "scikit-learn", "matplotlib"]);

// PyWavelets: usado pela delineação de ondas por wavelet (início da P, fim da T).
// requests: o NeuroKit2 importa no topo do pacote para ler arquivos remotos —
// função que não usamos, mas o import precisa resolver.
for (const p of ["pywavelets", "requests"]) {
  try {
    await py.loadPackage(p);
    console.log(`${p}: disponível`);
  } catch (e) {
    console.log(`${p}: NÃO disponível — ${String(e).slice(0, 120)}`);
  }
}

const destino = "/lib/python3.14/site-packages/neurokit2";
const { arquivos, bytes } = copiar(py, ORIGEM, destino);
console.log(`NeuroKit2 copiado: ${arquivos} arquivos, ${(bytes / 1024 / 1024).toFixed(1)} MB\n`);

const r = py.runPython(`
import json
saida = {}
try:
    import neurokit2 as nk
    import numpy as np
    saida["versao"] = nk.__version__
except Exception as e:
    saida["import"] = f"FALHOU: {type(e).__name__}: {e}"
    print(json.dumps(saida)); raise SystemExit

# As três funções que o CardioLaudo realmente usa
try:
    ecg = nk.ecg_simulate(duration=10, sampling_rate=500, heart_rate=70, random_state=42)
    saida["ecg_simulate"] = "ok"
except Exception as e:
    saida["ecg_simulate"] = f"FALHOU: {type(e).__name__}: {e}"
    ecg = None

if ecg is not None:
    try:
        limpo = nk.ecg_clean(ecg, sampling_rate=500)
        saida["ecg_clean"] = f"ok — {len(limpo)} amostras"
    except Exception as e:
        saida["ecg_clean"] = f"FALHOU: {type(e).__name__}: {e}"
        limpo = None

    if limpo is not None:
        try:
            _, info = nk.ecg_peaks(limpo, sampling_rate=500)
            rp = np.asarray(info.get("ECG_R_Peaks", []), dtype=int)
            saida["ecg_peaks"] = f"ok — {len(rp)} batimentos"
        except Exception as e:
            saida["ecg_peaks"] = f"FALHOU: {type(e).__name__}: {e}"
            rp = None

        if rp is not None and len(rp) > 2:
            try:
                _, w = nk.ecg_delineate(limpo, rp, sampling_rate=500, method="dwt")
                marcos = [k for k in w if "Onsets" in k or "Offsets" in k]
                saida["ecg_delineate"] = f"ok — {len(marcos)} marcos delineados"
            except Exception as e:
                saida["ecg_delineate"] = f"FALHOU: {type(e).__name__}: {e}"

json.dumps(saida)
`);

for (const [k, v] of Object.entries(JSON.parse(r))) {
  const marca = String(v).startsWith("FALHOU") ? "[FALHA]" : "[OK ]";
  console.log(`  ${marca} ${k.padEnd(14)} ${v}`);
}

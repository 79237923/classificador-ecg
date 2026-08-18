// Investiga por que o NeuroKit2 não instala e testa alternativas.
import { loadPyodide } from "pyodide";

const py = await loadPyodide();
await py.loadPackage(["numpy", "scipy", "pandas", "scikit-learn", "matplotlib", "micropip"]);
const micropip = py.pyimport("micropip");

async function tentar(rotulo, fn) {
  try {
    await fn();
    console.log(`  [OK ] ${rotulo}`);
    return true;
  } catch (e) {
    const msg = String(e.message || e);
    console.log(`  [FALHA] ${rotulo}`);
    console.log(`         ${msg.split("\n").filter(l => l.trim()).slice(-4).join("\n         ").slice(0, 700)}`);
    return false;
  }
}

console.log("1. instalação padrão");
let ok = await tentar("micropip.install('neurokit2')", () => micropip.install("neurokit2"));

if (!ok) {
  console.log("\n2. sem resolver dependências (já estão carregadas)");
  ok = await tentar("deps=False", () => micropip.install("neurokit2", { deps: false }));
}

if (ok) {
  console.log("\n3. teste funcional");
  const r = py.runPython(`
import json
try:
    import neurokit2 as nk
    import numpy as np
    ecg = nk.ecg_simulate(duration=10, sampling_rate=250, heart_rate=70)
    limpo = nk.ecg_clean(ecg, sampling_rate=250)
    _, info = nk.ecg_peaks(limpo, sampling_rate=250)
    rp = np.asarray(info.get("ECG_R_Peaks", []), dtype=int)
    res = {"versao": nk.__version__, "batimentos": int(len(rp))}
    try:
        _, w = nk.ecg_delineate(limpo, rp, sampling_rate=250, method="dwt")
        res["delineate"] = f"ok — {len([k for k in w if 'Onsets' in k or 'Offsets' in k])} marcos"
    except Exception as e:
        res["delineate"] = f"FALHOU: {type(e).__name__}: {e}"
    json.dumps(res)
except Exception as e:
    json.dumps({"erro": f"{type(e).__name__}: {e}"})
`);
  console.log("  " + r);
}

// Verifica empiricamente quais bibliotecas do CardioLaudo funcionam no Pyodide.
// Roda em Node, mas usa o mesmo runtime WebAssembly do navegador — se instala
// aqui, instala lá.

import { loadPyodide } from "pyodide";

const t0 = Date.now();
console.log("carregando Pyodide…");
const py = await loadPyodide();
console.log(`  núcleo carregado em ${((Date.now() - t0) / 1000).toFixed(1)}s`);
console.log(`  versão do Python: ${py.runPython("import sys; sys.version.split()[0]")}`);

// Pacotes portados oficialmente (vêm do CDN do Pyodide)
const oficiais = ["numpy", "scipy", "pandas", "scikit-learn", "matplotlib", "micropip"];
for (const p of oficiais) {
  const t = Date.now();
  try {
    await py.loadPackage(p);
    console.log(`  [OK ] ${p.padEnd(14)} ${((Date.now() - t) / 1000).toFixed(1)}s`);
  } catch (e) {
    console.log(`  [FALHA] ${p}: ${String(e).slice(0, 120)}`);
  }
}

// Pacotes que precisam vir do PyPI via micropip (Python puro)
const micropip = py.pyimport("micropip");
for (const p of ["neurokit2", "reportlab"]) {
  const t = Date.now();
  try {
    await micropip.install(p);
    console.log(`  [OK ] ${p.padEnd(14)} ${((Date.now() - t) / 1000).toFixed(1)}s (micropip)`);
  } catch (e) {
    console.log(`  [FALHA] ${p}: ${String(e).slice(0, 200)}`);
  }
}

// Teste funcional: as bibliotecas realmente executam o que precisamos?
console.log("\n--- teste funcional ---");
const resultado = py.runPython(`
import json
saida = {}

try:
    import numpy as np, scipy.signal as sg, scipy.ndimage as ndi
    x = np.linspace(0, 10, 5000)
    sinal = np.sin(2 * np.pi * 1.2 * x)
    picos, _ = sg.find_peaks(sinal, distance=100)
    # operacoes que substituiriam o OpenCV
    img = np.random.rand(200, 300)
    suave = ndi.uniform_filter(img, size=15)
    fechada = ndi.binary_closing(img > 0.5, structure=np.ones((3, 3)))
    saida["numpy_scipy"] = f"ok — {len(picos)} picos, filtros e morfologia funcionam"
except Exception as e:
    saida["numpy_scipy"] = f"FALHOU: {e}"

try:
    import neurokit2 as nk
    ecg = nk.ecg_simulate(duration=10, sampling_rate=250, heart_rate=70)
    limpo = nk.ecg_clean(ecg, sampling_rate=250)
    _, info = nk.ecg_peaks(limpo, sampling_rate=250)
    n = len(info.get("ECG_R_Peaks", []))
    saida["neurokit2"] = f"ok — detectou {n} batimentos"
except Exception as e:
    saida["neurokit2"] = f"FALHOU: {type(e).__name__}: {e}"

try:
    from reportlab.pdfgen import canvas
    from io import BytesIO
    buf = BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 700, "teste")
    c.save()
    saida["reportlab"] = f"ok — PDF de {len(buf.getvalue())} bytes"
except Exception as e:
    saida["reportlab"] = f"FALHOU: {type(e).__name__}: {e}"

json.dumps(saida)
`);

for (const [k, v] of Object.entries(JSON.parse(resultado))) {
  console.log(`  ${k.padEnd(12)}: ${v}`);
}
console.log(`\ntempo total: ${((Date.now() - t0) / 1000).toFixed(1)}s`);

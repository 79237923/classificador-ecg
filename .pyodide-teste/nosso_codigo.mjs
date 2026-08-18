// Roda o motor de análise do CardioLaudo dentro do Pyodide e compara com o
// resultado do servidor. É o teste que decide a migração: se os números
// divergirem, toda a validação feita contra o PTB-XL deixa de valer.
import { loadPyodide } from "pyodide";
import fs from "node:fs";
import path from "node:path";

function copiarPy(py, origem, destino) {
  py.FS.mkdirTree(destino);
  let n = 0;
  for (const item of fs.readdirSync(origem, { withFileTypes: true })) {
    if (item.name === "__pycache__") continue;
    const o = path.join(origem, item.name);
    const d = `${destino}/${item.name}`;
    if (item.isDirectory()) n += copiarPy(py, o, d);
    else if (item.name.endsWith(".py")) { py.FS.writeFile(d, fs.readFileSync(o)); n++; }
  }
  return n;
}

const py = await loadPyodide();
// pydantic é usado por schemas.py (Finding). Está na distribuição do Pyodide.
await py.loadPackage(["numpy", "scipy", "pandas", "scikit-learn", "matplotlib",
                      "pywavelets", "requests", "pydantic"]);

copiarPy(py, path.resolve("../.venv/Lib/site-packages/neurokit2"),
         "/lib/python3.14/site-packages/neurokit2");

// Nosso código: só os módulos de análise (sem auth, sem web).
py.FS.mkdirTree("/app/backend/app");
py.FS.writeFile("/app/backend/__init__.py", "");
for (const sub of ["", "processing", "classification", "ingestion"]) {
  const dir = sub ? `backend/app/${sub}` : "backend/app";
  copiarPy(py, path.resolve(`../${dir}`), `/app/${dir}`);
}

// O sinal de teste, gerado pelo servidor
const csv = fs.readFileSync(path.resolve("../data/samples/ecg_normal_12d.csv"), "utf8");
py.FS.writeFile("/app/ecg.csv", csv);

const r = py.runPython(`
import sys, json
sys.path.insert(0, "/app")

from backend.app.ingestion.loaders import load_digital
from backend.app.processing.analysis import analyze
from backend.app.classification.rules import classify, summarize

dados = open("/app/ecg.csv", "rb").read()
rec = load_digital("ecg.csv", dados, sampling_rate=500.0)
a = analyze(rec)
achados = classify(a)

json.dumps({
    "fc": a.heart_rate_bpm, "pr": a.pr_ms, "qrs": a.qrs_ms,
    "qt": a.qt_ms, "qtcf": a.qtc_fridericia_ms, "eixo": a.axis_degrees,
    "batimentos": a.n_beats,
    "achados": [f.code for f in achados],
    "resumo": summarize(achados, a),
})
`);

console.log("\n=== RESULTADO NO NAVEGADOR (Pyodide) ===");
const res = JSON.parse(r);
for (const [k, v] of Object.entries(res)) {
  const valor = typeof v === "number" ? v.toFixed(2) : JSON.stringify(v);
  console.log(`  ${k.padEnd(12)}: ${valor}`);
}
fs.writeFileSync("resultado_pyodide.json", JSON.stringify(res, null, 1));
console.log("\n(salvo em resultado_pyodide.json para comparação)");

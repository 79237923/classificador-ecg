// Teste definitivo: digitaliza e analisa uma imagem REAL de ECG dentro do
// Pyodide, e compara com o que o servidor produz para a mesma imagem.
import { loadPyodide } from "pyodide";
import fs from "node:fs";
import os from "node:os";
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

const t0 = Date.now();
const py = await loadPyodide();
await py.loadPackage(["numpy", "scipy", "pandas", "scikit-learn", "matplotlib",
                      "pywavelets", "requests", "pydantic", "opencv-python"]);
console.log(`bibliotecas carregadas em ${((Date.now() - t0) / 1000).toFixed(1)}s`);

copiarPy(py, path.resolve("../.venv/Lib/site-packages/neurokit2"),
         "/lib/python3.14/site-packages/neurokit2");
py.FS.mkdirTree("/app/backend/app");
py.FS.writeFile("/app/backend/__init__.py", "");
for (const sub of ["", "processing", "classification", "ingestion"]) {
  const dir = sub ? `backend/app/${sub}` : "backend/app";
  copiarPy(py, path.resolve(`../${dir}`), `/app/${dir}`);
}

// A imagem real de ECG (infarto com supra de ST)
const imgPath = path.join(os.homedir(), "Downloads", "SUPRA2-1024x728.jpg");
if (!fs.existsSync(imgPath)) { console.log("imagem não encontrada"); process.exit(1); }
py.FS.writeFile("/app/ecg.jpg", fs.readFileSync(imgPath));

const t1 = Date.now();
const r = py.runPython(`
import sys, json
sys.path.insert(0, "/app")
from backend.app.ingestion.image_digitizer import digitize
from backend.app.processing.analysis import analyze
from backend.app.classification.rules import classify, summarize

rec = digitize("ecg.jpg", open("/app/ecg.jpg", "rb").read())
a = analyze(rec)
ach = classify(a)
json.dumps({
    "derivacoes": len(rec.lead_names), "duracao": round(rec.duration_s, 1),
    "fc": a.heart_rate_bpm, "pr": a.pr_ms, "qrs": a.qrs_ms,
    "qtcf": a.qtc_fridericia_ms, "batimentos": a.n_beats,
    "st": {k: round(v * 1000) for k, v in a.st_deviation_mv.items()},
    "achados": [f.code for f in ach],
    "resumo": summarize(ach, a),
}, ensure_ascii=False)
`);
console.log(`análise da imagem em ${((Date.now() - t1) / 1000).toFixed(1)}s\n`);

const res = JSON.parse(r);
console.log("=== IMAGEM REAL ANALISADA NO NAVEGADOR ===");
console.log(`  derivações extraídas : ${res.derivacoes}`);
console.log(`  duração              : ${res.duracao}s`);
console.log(`  FC / PR / QRS / QTcF : ${res.fc?.toFixed(1)} / ${res.pr} / ${res.qrs} / ${res.qtcf?.toFixed(0)}`);
console.log(`  batimentos           : ${res.batimentos}`);
console.log(`  achados              : ${res.achados.join(", ")}`);
console.log(`  resumo               : ${res.resumo}`);
fs.writeFileSync("resultado_imagem_pyodide.json", JSON.stringify(res, null, 1));

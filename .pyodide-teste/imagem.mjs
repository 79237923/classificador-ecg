// Testa o digitalizador de imagem no Pyodide. É a peça de maior risco: usa
// OpenCV, que não existe para WebAssembly. Verifica se o cv2 realmente falta e
// mede quanto do módulo depende dele.
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
await py.loadPackage(["numpy", "scipy", "pandas", "scikit-learn", "matplotlib",
                      "pywavelets", "requests", "pydantic", "micropip"]);
try {
  await py.loadPackage("opencv-python");
  console.log("  [OK ] opencv-python está na distribuição do Pyodide");
} catch (e) {
  console.log("  [--] opencv-python não está na distribuição oficial");
}

const micropip = py.pyimport("micropip");
for (const nome of ["opencv-python-headless", "opencv-python"]) {
  try {
    await micropip.install(nome);
    console.log(`  [OK ] ${nome} instalou via micropip`);
    break;
  } catch (e) {
    console.log(`  [FALHA] ${nome}: ${String(e.message || e).split("\n").pop().slice(0, 130)}`);
  }
}

copiarPy(py, path.resolve("../.venv/Lib/site-packages/neurokit2"),
         "/lib/python3.14/site-packages/neurokit2");
py.FS.mkdirTree("/app/backend/app");
py.FS.writeFile("/app/backend/__init__.py", "");
for (const sub of ["", "processing", "classification", "ingestion"]) {
  const dir = sub ? `backend/app/${sub}` : "backend/app";
  copiarPy(py, path.resolve(`../${dir}`), `/app/${dir}`);
}

const r = py.runPython(`
import sys, json
sys.path.insert(0, "/app")
saida = {}

try:
    import cv2
    saida["cv2"] = f"disponível ({cv2.__version__})"
except ImportError as e:
    saida["cv2"] = f"AUSENTE: {e}"

try:
    from backend.app.ingestion import image_digitizer
    saida["import_digitalizador"] = "ok (o módulo importa; cv2 é importado dentro das funções)"
except Exception as e:
    saida["import_digitalizador"] = f"FALHOU: {type(e).__name__}: {e}"

# Quais funções do digitalizador NÃO dependem de cv2 (já são NumPy puro)?
import inspect
try:
    from backend.app.ingestion import image_digitizer as dig
    puras, com_cv2 = [], []
    for nome, fn in inspect.getmembers(dig, inspect.isfunction):
        try:
            src = inspect.getsource(fn)
        except Exception:
            continue
        (com_cv2 if "cv2." in src else puras).append(nome)
    saida["funcoes_sem_cv2"] = puras
    saida["funcoes_com_cv2"] = com_cv2
except Exception as e:
    saida["analise_funcoes"] = f"falhou: {e}"

json.dumps(saida, ensure_ascii=False)
`);

const res = JSON.parse(r);
console.log("\n=== ESTADO DO DIGITALIZADOR NO NAVEGADOR ===");
console.log(`  cv2: ${res.cv2}`);
console.log(`  import do módulo: ${res.import_digitalizador}`);
console.log(`\n  funções que JÁ funcionam (NumPy puro): ${(res.funcoes_sem_cv2 || []).length}`);
console.log(`    ${(res.funcoes_sem_cv2 || []).join(", ")}`);
console.log(`\n  funções que precisam ser portadas: ${(res.funcoes_com_cv2 || []).length}`);
console.log(`    ${(res.funcoes_com_cv2 || []).join(", ")}`);

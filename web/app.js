"use strict";

// CardioLaudo — versão que roda inteiramente no navegador.
// O exame nunca é enviado a servidor: o Pyodide carrega o mesmo motor Python do
// servidor e a análise acontece na máquina de quem acessa.

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let py = null;              // instância do Pyodide
let arquivoAtual = null;    // {nome, base64}
let ultimoResultado = null;
let pdfDisponivel = false;  // o ReportLab carregou?

// ---------------------------------------------------------------- carregamento
// Pacotes na ordem em que são carregados, com peso aproximado para a barra de
// progresso refletir o download real (numpy e scipy dominam).
const PACOTES = [
  ["numpy", 8], ["scipy", 20], ["pandas", 12], ["scikit-learn", 8],
  ["matplotlib", 8], ["pywavelets", 2], ["requests", 1], ["pydantic", 3],
  ["opencv-python", 12], ["micropip", 1],
];

function progresso(pct, texto) {
  $("barra-progresso").style.width = `${Math.min(100, Math.round(pct))}%`;
  if (texto) $("carregando-status").textContent = texto;
}

// Busca um arquivo do motor pela impressão digital do seu conteúdo.
//
// Com hash: a URL identifica aquela versão exata, então guardá-la em cache para
// sempre é seguro — conteúdo novo tem URL nova. Sem hash: não há como saber se
// o que está em cache ainda vale, então revalida na rede. Melhor lento que
// desatualizado, e a decisão é por arquivo: um hash faltando não pode arrastar
// os outros de volta ao cache eterno.
function buscar(arquivo, hash) {
  if (!hash) console.warn(`sem impressão digital de ${arquivo}: revalidando na rede`);
  return fetch(hash ? `${arquivo}?v=${hash}` : arquivo,
               { cache: hash ? "force-cache" : "no-cache" });
}

// Impressão digital do motor publicado. É um arquivo minúsculo e é buscado sem
// cache justamente para ser a única coisa que precisa estar sempre em dia.
async function versaoDoMotor() {
  try {
    const r = await fetch("versao.json", { cache: "no-store" });
    if (r.ok) return await r.json();
  } catch (e) {
    console.warn("versao.json indisponível; o motor será revalidado na rede:", e);
  }
  return null;
}

async function iniciar() {
  try {
    progresso(3, "carregando o interpretador Python…");
    py = await loadPyodide({ indexURL: "https://cdn.jsdelivr.net/pyodide/v314.0.5/full/" });

    const total = PACOTES.reduce((s, [, p]) => s + p, 0);
    let feito = 0;
    for (const [nome, peso] of PACOTES) {
      progresso(5 + (feito / total) * 75, `baixando ${nome}…`);
      try {
        await py.loadPackage(nome);
      } catch (e) {
        console.warn(`pacote ${nome} falhou:`, e);
      }
      feito += peso;
    }

    // ReportLab (laudo em PDF) não faz parte da distribuição do Pyodide: vem do
    // PyPI pelo micropip. Falha aqui não impede a análise — só o PDF.
    progresso(82, "carregando o gerador de laudo…");
    try {
      const micropip = py.pyimport("micropip");
      await micropip.install("reportlab");
      pdfDisponivel = true;
    } catch (e) {
      console.warn("ReportLab indisponível — o laudo em PDF ficará desativado:", e);
    }

    progresso(85, "carregando o motor de análise…");
    // O zip do motor (680 kB) e a ponte Python ficam em cache entre visitas,
    // senão seriam rebaixados a cada acesso. Mas guardar sob a URL fixa prende
    // quem já visitou o site à versão antiga para sempre: o cache nunca mais
    // consulta a rede. Por isso a URL leva o hash do conteúdo — motor novo é
    // URL nova, motor igual continua vindo do cache.
    const versao = await versaoDoMotor();

    const zip = await (await buscar("cardiolaudo.zip", versao?.motor)).arrayBuffer();
    py.FS.mkdirTree("/cardiolaudo");
    await py.unpackArchive(zip, "zip", { extractDir: "/cardiolaudo" });
    // O NeuroKit2 vai no zip junto com o motor; precisa estar no path de módulos.
    py.runPython(`import sys; sys.path.insert(0, "/cardiolaudo")`);

    progresso(93, "preparando a análise…");
    const ponte = await (await buscar("analise.py", versao?.ponte)).text();
    py.runPython(ponte);

    const diag = JSON.parse(py.runPython("diagnostico()"));
    diag.motor = versao?.motor ?? "sem impressão (revalidado na rede)";
    console.log("CardioLaudo — ambiente:", diag);
    $("engine-badge").textContent = `Python ${diag.python} · no navegador`;
    $("ver").textContent = `NumPy ${diag.numpy} · NeuroKit2 ${diag.neurokit2} · OpenCV ${diag.opencv}`;

    // Ponto de inspeção: como tudo roda no cliente, quem usa consegue auditar o
    // que o sistema faz — e ajuda a diagnosticar problemas relatados.
    window.CardioLaudo = {
      py,
      diagnostico: diag,
      get resultado() { return ultimoResultado; },
      analisar: (nome, b64) => {
        py.globals.set("_arquivo", nome); py.globals.set("_dados", b64);
        py.globals.set("_idade", null); py.globals.set("_sexo", null);
        return JSON.parse(py.runPython("analisar(_arquivo, _dados, _idade, _sexo)"));
      },
    };

    progresso(100, "pronto");
    setTimeout(() => {
      $("carregando-card").hidden = true;
      $("upload-card").hidden = false;
    }, 350);
  } catch (err) {
    console.error(err);
    $("carregando-status").innerHTML =
      `<strong>Falha ao carregar:</strong> ${esc(err.message || err)}<br>` +
      "Recarregue a página. Se persistir, tente outro navegador (é preciso suporte a WebAssembly).";
    $("barra-progresso").style.background = "var(--bad)";
  }
}

iniciar();

// ---------------------------------------------------------------- envio do arquivo
const dz = $("dropzone"), input = $("file-input");
dz.addEventListener("click", () => input.click());
dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
dz.addEventListener("drop", (e) => {
  e.preventDefault(); dz.classList.remove("drag");
  if (e.dataTransfer.files.length) receber(e.dataTransfer.files[0]);
});
input.addEventListener("change", () => { if (input.files.length) receber(input.files[0]); });

function paraBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let bin = "";
  const passo = 0x8000;   // em blocos: String.fromCharCode estoura com arrays grandes
  for (let i = 0; i < bytes.length; i += passo) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + passo));
  }
  return btoa(bin);
}

async function receber(file) {
  arquivoAtual = { nome: file.name, base64: paraBase64(await file.arrayBuffer()) };
  $("file-label").textContent = `Selecionado: ${file.name} (${(file.size / 1024).toFixed(0)} kB)`;
  $("analyze-btn").disabled = false;
}

document.querySelectorAll("[data-exemplo]").forEach((b) =>
  b.addEventListener("click", async () => {
    const nome = b.dataset.exemplo;
    $("file-label").textContent = `carregando exemplo ${nome}…`;
    const buf = await (await fetch(`exemplos/${nome}`)).arrayBuffer();
    arquivoAtual = { nome, base64: paraBase64(buf) };
    $("file-label").textContent = `Exemplo: ${nome} (${(buf.byteLength / 1024).toFixed(0)} kB)`;
    $("analyze-btn").disabled = false;
  }));

// ---------------------------------------------------------------- análise
$("analyze-btn").addEventListener("click", async () => {
  if (!arquivoAtual || !py) return;
  const st = $("status");
  st.hidden = false; st.className = "status";
  st.textContent = "Analisando no seu navegador… (pode levar alguns segundos)";
  $("analyze-btn").disabled = true;

  // Deixa o navegador desenhar a mensagem antes de bloquear na análise.
  await new Promise((r) => setTimeout(r, 30));

  try {
    const idade = $("idade").value ? parseInt($("idade").value, 10) : null;
    const sexo = $("sexo").value || null;
    py.globals.set("_arquivo", arquivoAtual.nome);
    py.globals.set("_dados", arquivoAtual.base64);
    py.globals.set("_idade", idade);
    py.globals.set("_sexo", sexo);
    const bruto = py.runPython("analisar(_arquivo, _dados, _idade, _sexo)");
    const r = JSON.parse(bruto);
    if (r.erro) throw new Error(r.erro);
    ultimoResultado = r;
    render(r);
    st.hidden = true;
  } catch (err) {
    st.textContent = `Falha na análise: ${err.message || err}`;
    st.classList.add("error");
  } finally {
    $("analyze-btn").disabled = false;
  }
});

// ---------------------------------------------------------------- exibição
const SEV = { normal: "Normal", limitrofe: "Limítrofe", anormal: "Anormal", critico: "Crítico" };
const f = (v, dec = 0, suf = "") =>
  (v === null || v === undefined) ? "—" : v.toFixed(dec) + suf;

function render(r) {
  $("results").hidden = false;
  $("analysis-meta").textContent =
    `${r.arquivo} · ${r.origem} · ${r.derivacoes.length} derivação(ões) · ${r.duracao_s}s`;

  const critico = r.achados.some((a) => a.severity === "critico");
  const sum = $("summary");
  sum.textContent = r.resumo;
  sum.classList.toggle("critical", critico);

  // Conclusão narrativa, no formato de um laudo eletrocardiográfico.
  const n = r.narrativa;
  $("laudo").hidden = !n;
  if (n) {
    $("laudo-secoes").innerHTML = n.secoes.map((s) =>
      `<p class="laudo-secao"><span class="rotulo">${esc(s.titulo)}</span>${esc(s.texto)}</p>`
    ).join("");
    const conc = $("laudo-conclusao");
    conc.textContent = n.conclusao;
    conc.classList.toggle("critical", critico);
  }

  const m = r.medidas;
  const cartoes = [
    ["Freq. cardíaca", f(m.fc), "bpm"], ["RR médio", f(m.rr), "ms"],
    ["PR", f(m.pr), "ms"], ["QRS", f(m.qrs), "ms"], ["QT", f(m.qt), "ms"],
    ["QTc Bazett", f(m.qtc_bazett), "ms"], ["QTc Fridericia", f(m.qtc_fridericia), "ms"],
    ["Eixo elétrico", f(m.eixo), "°"], ["Sokolow-Lyon", f(m.sokolow, 2), "mV"],
    ["Batimentos", m.batimentos ?? "—", ""], ["Duração", f(r.duracao_s, 1), "s"],
  ];
  $("measurements").innerHTML = cartoes.map(([k, v, u]) =>
    `<div class="metric"><div class="k">${esc(k)}</div>
       <div class="v">${esc(v)} <small>${esc(u)}</small></div></div>`).join("");

  $("findings").innerHTML = r.achados.map((a) =>
    `<li class="${esc(a.severity)}">
       <span class="sev">${esc(SEV[a.severity] || a.severity)}</span> — <strong>${esc(a.label)}</strong>
       <div class="crit">Critério: ${esc(a.criteria)}${a.detail ? " · " + esc(a.detail) : ""}</div>
     </li>`).join("");

  const st = Object.entries(r.st || {});
  $("st-section").hidden = st.length === 0;
  if (st.length) {
    $("st-grid").innerHTML = st.map(([lead, uv]) => {
      const cor = uv >= 100 ? "critico" : uv <= -100 ? "anormal" : "";
      return `<div class="metric ${cor}"><div class="k">${esc(lead)}</div>
        <div class="v">${uv > 0 ? "+" : ""}${esc(uv)} <small>µV</small></div></div>`;
    }).join("");
  }

  const avisos = r.avisos || [];
  $("quality-details").hidden = avisos.length === 0;
  $("quality-list").innerHTML = avisos.map((a) => `<li>${esc(a)}</li>`).join("");

  if (r.preview && r.preview.length) {
    $("lead-name").textContent = r.preview[0].lead_name;
    desenhar(r.preview[0]);
  }

  const btnPdf = $("pdf-btn");
  btnPdf.hidden = !pdfDisponivel;
  if (!pdfDisponivel) {
    console.info("PDF indisponível nesta sessão (ReportLab não carregou).");
  }

  $("results").scrollIntoView({ behavior: "smooth" });
}

// Copiar o texto do laudo — o uso mais provável é colar num prontuário.
$("copiar-btn").addEventListener("click", async () => {
  if (!ultimoResultado?.narrativa) return;
  const n = ultimoResultado.narrativa;
  const texto = [
    `Análise automatizada de ECG`,
    `Arquivo: ${ultimoResultado.arquivo}`,
    "",
    ...n.secoes.map((s) => `${s.titulo}: ${s.texto}`),
    "",
    n.conclusao,
    "",
    "Gerado por software de apoio à decisão clínica. Não substitui laudo médico.",
  ].join("\n");
  try {
    await navigator.clipboard.writeText(texto);
    const ok = $("copiar-ok");
    ok.hidden = false;
    setTimeout(() => { ok.hidden = true; }, 2000);
  } catch {
    // clipboard bloqueado (contexto sem HTTPS, por exemplo): mostra para copiar à mão
    window.prompt("Copie o texto do laudo:", texto);
  }
});

// ---------------------------------------------------------------- laudo em PDF
$("pdf-btn").addEventListener("click", async () => {
  if (!ultimoResultado || !py) return;
  const btn = $("pdf-btn");
  const rotulo = btn.textContent;
  btn.disabled = true; btn.textContent = "gerando…";
  try {
    py.globals.set("_res", JSON.stringify(ultimoResultado));
    const b64 = py.runPython("gerar_pdf(_res)");
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `laudo_${(ultimoResultado.arquivo || "ecg").replace(/\.[^.]+$/, "")}.pdf`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (err) {
    alert(`Não foi possível gerar o PDF: ${err.message || err}`);
  } finally {
    btn.disabled = false; btn.textContent = rotulo;
  }
});

// ---------------------------------------------------------------- traçado
function desenhar(preview) {
  const canvas = $("ecg-canvas");
  const amostras = preview.samples;
  const fs = preview.sampling_rate_hz;
  const pxPorSeg = 120;                 // ~25 mm/s visualmente
  const larg = Math.max(760, Math.ceil((amostras.length / fs) * pxPorSeg));
  const alt = canvas.height;
  canvas.width = larg;

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, larg, alt);

  const mm = pxPorSeg / 25;
  ctx.lineWidth = 1;
  for (let x = 0; x < larg; x += mm) {
    ctx.strokeStyle = (Math.round(x / mm) % 5 === 0) ? "#f3c1bb" : "#fbe3e0";
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, alt); ctx.stroke();
  }
  for (let y = 0; y < alt; y += mm) {
    ctx.strokeStyle = (Math.round(y / mm) % 5 === 0) ? "#f3c1bb" : "#fbe3e0";
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(larg, y); ctx.stroke();
  }

  const meio = alt / 2, escalaY = 10 * mm;   // 10 mm/mV
  ctx.strokeStyle = "#1d2b36"; ctx.lineWidth = 1.4;
  ctx.beginPath();
  amostras.forEach((v, i) => {
    const x = (i / fs) * pxPorSeg;
    const y = meio - v * escalaY;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();
}

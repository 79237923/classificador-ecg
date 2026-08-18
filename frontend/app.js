"use strict";

const $ = (id) => document.getElementById(id);
let selectedFile = null;

// ---------- estado inicial ----------
fetch("/api/health").then(r => r.json()).then(h => {
  $("engine-badge").textContent = `motor v${h.engine_version}` +
    (h.deep_learning_available ? " · DL ativo" : " · DL inativo");
  $("ver").textContent = h.engine_version;
}).catch(() => { $("engine-badge").textContent = "API indisponível"; });

// ---------- sessão ----------
let usuarioAtual = null;

function mostrarApp(user) {
  usuarioAtual = user;
  $("login-card").hidden = true;
  $("upload-card").hidden = false;
  $("user-chip").hidden = false;
  $("logout-btn").hidden = false;
  $("senha-btn").hidden = false;
  $("admin-btn").hidden = user.role !== "admin";
  $("user-chip").textContent = user.full_name +
    (user.professional_id ? ` · ${user.professional_id}` : "") +
    (user.role === "admin" ? " · admin" : "");
}

function mostrarLogin() {
  usuarioAtual = null;
  $("login-card").hidden = false;
  $("upload-card").hidden = true;
  $("results").hidden = true;
  $("user-chip").hidden = true;
  $("logout-btn").hidden = true;
  $("senha-btn").hidden = true;
  $("admin-btn").hidden = true;
  fecharModais();
}

// ---------- modais ----------
function abrirModal(id) { $(id).hidden = false; }
function fecharModais() {
  $("modal-senha").hidden = true;
  $("modal-admin").hidden = true;
}
document.querySelectorAll("[data-close]").forEach((b) =>
  b.addEventListener("click", () => { $(b.dataset.close).hidden = true; }));
document.querySelectorAll(".modal").forEach((m) =>
  m.addEventListener("click", (e) => { if (e.target === m) m.hidden = true; }));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") fecharModais(); });

// ---------- alterar senha ----------
$("senha-btn").addEventListener("click", () => {
  $("senha-form").reset();
  $("senha-status").hidden = true;
  abrirModal("modal-senha");
});

$("senha-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const st = $("senha-status");
  st.hidden = true; st.className = "status";
  if ($("senha-nova").value !== $("senha-conf").value) {
    st.textContent = "A confirmação não confere com a nova senha.";
    st.classList.add("error"); st.hidden = false;
    return;
  }
  try {
    const resp = await fetch("/api/auth/senha", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        senha_atual: $("senha-atual").value, senha_nova: $("senha-nova").value }),
    });
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || "Falha ao alterar a senha.");
    st.textContent = body.mensagem || "Senha alterada.";
    st.classList.add("ok"); st.hidden = false;
    $("senha-form").reset();
  } catch (err) {
    st.textContent = err.message; st.classList.add("error"); st.hidden = false;
  }
});

// ---------- administração de contas ----------
$("admin-btn").addEventListener("click", () => {
  $("admin-status").hidden = true;
  abrirModal("modal-admin");
  carregarUsuarios();
});

async function carregarUsuarios() {
  const tbody = document.querySelector("#admin-tabela tbody");
  tbody.innerHTML = `<tr><td colspan="6">Carregando…</td></tr>`;
  try {
    const resp = await fetch("/api/admin/usuarios");
    if (!resp.ok) throw new Error("Sem permissão ou sessão expirada.");
    const { usuarios } = await resp.json();
    tbody.innerHTML = usuarios.map((u) => {
      const eu = usuarioAtual && u.email === usuarioAtual.email;
      const acoes = eu ? '<span class="hint">(você)</span>' : `
        <button class="btn-mini" data-reset="${esc(u.email)}">Redefinir senha</button>
        ${u.active
          ? `<button class="btn-mini perigo" data-desativar="${esc(u.email)}">Desativar</button>`
          : `<button class="btn-mini" data-reativar="${esc(u.email)}">Reativar</button>`}`;
      return `<tr class="${u.active ? "" : "inativa"}">
        <td>${esc(u.email)}</td>
        <td>${esc(u.full_name)}${u.professional_id ? " · " + esc(u.professional_id) : ""}</td>
        <td><span class="chip-papel ${u.role === "admin" ? "admin" : ""}">${esc(u.role)}</span></td>
        <td>${u.active ? "sim" : "não"}</td>
        <td>${u.last_login_at ? esc(u.last_login_at.replace("T", " ").slice(0, 16)) : "—"}</td>
        <td><div class="acoes">${acoes}</div></td>
      </tr>`;
    }).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6">${esc(err.message)}</td></tr>`;
  }
}

async function acaoAdmin(url, confirmar) {
  if (confirmar && !window.confirm(confirmar)) return;
  const resp = await fetch(url, { method: "POST" });
  const body = await resp.json().catch(() => ({}));
  const st = $("admin-status");
  st.className = "status"; st.hidden = false;
  if (resp.ok) { st.textContent = body.mensagem || "Feito."; st.classList.add("ok"); }
  else { st.textContent = body.detail || "Falha na operação."; st.classList.add("error"); }
  carregarUsuarios();
}

document.querySelector("#admin-tabela").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  const enc = encodeURIComponent;
  if (b.dataset.desativar)
    acaoAdmin(`/api/admin/usuarios/${enc(b.dataset.desativar)}/desativar`,
              `Desativar ${b.dataset.desativar}? As sessões da conta serão encerradas.`);
  else if (b.dataset.reativar)
    acaoAdmin(`/api/admin/usuarios/${enc(b.dataset.reativar)}/reativar`);
  else if (b.dataset.reset)
    resetarSenha(b.dataset.reset);
});

async function resetarSenha(email) {
  const nova = window.prompt(
    `Nova senha para ${email} (mín. 12 caracteres, letras e números):`);
  if (!nova) return;
  const resp = await fetch("/api/admin/usuarios/reset-senha", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, senha_nova: nova }),
  });
  const body = await resp.json().catch(() => ({}));
  const st = $("admin-status");
  st.className = "status"; st.hidden = false;
  if (resp.ok) { st.textContent = body.mensagem || "Senha redefinida."; st.classList.add("ok"); }
  else { st.textContent = body.detail || "Falha ao redefinir."; st.classList.add("error"); }
}

$("admin-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const st = $("admin-status");
  st.className = "status"; st.hidden = true;
  try {
    const resp = await fetch("/api/admin/usuarios", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: $("admin-email").value, full_name: $("admin-nome").value,
        professional_id: $("admin-crm").value || null,
        role: $("admin-papel").value, senha: $("admin-senha").value }),
    });
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || "Falha ao criar a conta.");
    st.textContent = `Conta criada: ${body.email}`; st.classList.add("ok"); st.hidden = false;
    $("admin-form").reset();
    carregarUsuarios();
  } catch (err) {
    st.textContent = err.message; st.classList.add("error"); st.hidden = false;
  }
});

// A sessão vive num cookie httponly: o JavaScript não a lê, apenas pergunta
// ao servidor se ela ainda é válida.
fetch("/api/auth/me")
  .then(r => (r.ok ? r.json() : Promise.reject()))
  .then(mostrarApp)
  .catch(mostrarLogin);

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const st = $("login-status");
  st.hidden = true;
  $("login-btn").disabled = true;
  try {
    const resp = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("login-email").value, senha: $("login-senha").value }),
    });
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || "Falha no acesso.");
    $("login-senha").value = "";
    mostrarApp(body);
  } catch (err) {
    st.textContent = err.message;
    st.hidden = false;
  } finally {
    $("login-btn").disabled = false;
  }
});

$("logout-btn").addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" }).catch(() => {});
  selectedFile = null;
  $("file-label").textContent = "";
  $("analyze-btn").disabled = true;
  mostrarLogin();
});

// ---------- upload ----------
const dz = $("dropzone"), input = $("file-input");
dz.addEventListener("click", () => input.click());
dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
dz.addEventListener("drop", (e) => {
  e.preventDefault(); dz.classList.remove("drag");
  if (e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);
});
input.addEventListener("change", () => { if (input.files.length) setFile(input.files[0]); });

function setFile(f) {
  selectedFile = f;
  $("file-label").textContent = `Selecionado: ${f.name} (${(f.size / 1024).toFixed(0)} kB)`;
  $("analyze-btn").disabled = false;
}

// ---------- análise ----------
$("analyze-btn").addEventListener("click", async () => {
  if (!selectedFile) return;
  const st = $("status");
  st.hidden = false; st.classList.remove("error");
  st.textContent = "Processando o exame…";
  $("analyze-btn").disabled = true;

  const fd = new FormData();
  fd.append("file", selectedFile);
  fd.append("sampling_rate", $("fs").value || "500");
  if ($("age").value) fd.append("age", $("age").value);
  if ($("sex").value) fd.append("sex", $("sex").value);

  try {
    const resp = await fetch("/api/analyze", { method: "POST", body: fd });
    if (resp.status === 401) {
      mostrarLogin();
      throw new Error("Sua sessão expirou. Faça login novamente.");
    }
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || `Erro ${resp.status}`);
    render(body);
    st.hidden = true;
  } catch (err) {
    st.textContent = "Falha na análise: " + err.message;
    st.classList.add("error");
  } finally {
    $("analyze-btn").disabled = false;
  }
});

// ---------- renderização ----------
const SEV = { normal: "Normal", limitrofe: "Limítrofe", anormal: "Anormal", critico: "Crítico" };

// Todo texto vindo da API (inclusive derivado do nome do arquivo enviado) é
// escapado antes de entrar no DOM via innerHTML.
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmt(v, dec = 0, suffix = "") {
  return (v === null || v === undefined) ? "—" : v.toFixed(dec) + suffix;
}

function render(r) {
  $("results").hidden = false;
  $("analysis-meta").textContent =
    `ID ${r.analysis_id} · ${r.source.filename} · ${r.source.format}`;
  $("pdf-btn").href = `/api/report/${r.analysis_id}/pdf`;

  const critical = r.findings.some(f => f.severity === "critico");
  const sum = $("summary");
  sum.textContent = r.summary;
  sum.classList.toggle("critical", critical);

  const m = r.measurements;
  const cards = [
    ["Freq. cardíaca", fmt(m.heart_rate_bpm), "bpm"],
    ["RR médio", fmt(m.rr_mean_ms), "ms"],
    ["PR", fmt(m.pr_ms), "ms"],
    ["QRS", fmt(m.qrs_ms), "ms"],
    ["QT", fmt(m.qt_ms), "ms"],
    ["QTc Bazett", fmt(m.qtc_bazett_ms), "ms"],
    ["QTc Fridericia", fmt(m.qtc_fridericia_ms), "ms"],
    ["Eixo elétrico", fmt(m.axis_degrees), "°"],
    ["Sokolow-Lyon", fmt(m.sokolow_lyon_mv, 2), "mV"],
    ["Batimentos", m.n_beats ?? "—", ""],
    ["Duração", fmt(m.duration_s, 1), "s"],
  ];
  $("measurements").innerHTML = cards.map(([k, v, u]) =>
    `<div class="metric"><div class="k">${esc(k)}</div><div class="v">${esc(v)} <small>${esc(u)}</small></div></div>`).join("");

  $("findings").innerHTML = r.findings.map(f =>
    `<li class="${esc(f.severity)}">
       <span class="sev">${esc(SEV[f.severity] || f.severity)}</span> — <strong>${esc(f.label)}</strong>
       <div class="crit">Critério: ${esc(f.criteria)}${f.detail ? " · " + esc(f.detail) : ""}</div>
     </li>`).join("");

  const dl = r.deep_learning;
  $("dl-section").hidden = !dl;
  if (dl) {
    $("dl-bars").innerHTML = dl.map(p =>
      `<div class="dl-row"><span>${esc(p.label)}</span>
         <div class="dl-bar"><div style="width:${(p.probability * 100).toFixed(0)}%"></div></div>
         <span>${(p.probability * 100).toFixed(0)}%</span></div>`).join("");
  }

  const notes = (r.quality && r.quality.warnings) || [];
  $("quality-details").hidden = notes.length === 0;
  $("quality-list").innerHTML = notes.map(n => `<li>${esc(n)}</li>`).join("");

  $("disclaimer-text").textContent = r.disclaimer;

  if (r.preview && r.preview.length) {
    $("lead-name").textContent = r.preview[0].lead_name;
    drawECG(r.preview[0]);
  }
  $("results").scrollIntoView({ behavior: "smooth" });
}

// ---------- traçado em canvas com grade estilo papel de ECG ----------
function drawECG(preview) {
  const canvas = $("ecg-canvas");
  const samples = preview.samples;
  const fs = preview.sampling_rate_hz;
  const pxPerSec = 120;                       // ~25 mm/s visual
  const width = Math.max(760, Math.ceil(samples.length / fs * pxPerSec));
  const height = canvas.height;
  canvas.width = width;

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);

  // grade: 1 "mm" = pxPerSec/25 px
  const mm = pxPerSec / 25;
  ctx.lineWidth = 1;
  for (let x = 0; x < width; x += mm) {
    ctx.strokeStyle = (Math.round(x / mm) % 5 === 0) ? "#f3c1bb" : "#fbe3e0";
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke();
  }
  for (let y = 0; y < height; y += mm) {
    ctx.strokeStyle = (Math.round(y / mm) % 5 === 0) ? "#f3c1bb" : "#fbe3e0";
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke();
  }

  // traçado: 10 mm/mV
  const mid = height / 2, scaleY = 10 * mm;
  ctx.strokeStyle = "#1d2b36"; ctx.lineWidth = 1.4;
  ctx.beginPath();
  samples.forEach((v, i) => {
    const x = (i / fs) * pxPerSec;
    const y = mid - v * scaleY;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();
}

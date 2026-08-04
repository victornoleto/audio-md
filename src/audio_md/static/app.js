/* audio-md web — reorderable dropzone → job → result. Vanilla JS, no build step. */

const $ = (s) => document.querySelector(s);

let files = []; // File[], in the order chosen by the user

// ---------------------------------------------------------------- helpers

const fmtSize = (n) =>
  n < 1048576 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1048576).toFixed(1)} MB`;

const fmtDate = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString("pt-BR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
};

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

const BARS = '<span class="bars" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></span>';

// ---------------------------------------------------------------- theme

const themeBtn = $("#theme-toggle");

const effectiveTheme = () =>
  document.documentElement.dataset.theme ||
  (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");

const renderThemeBtn = () => { themeBtn.textContent = effectiveTheme() === "dark" ? "☀" : "☾"; };

themeBtn.addEventListener("click", () => {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.theme = next;
  renderThemeBtn();
});
renderThemeBtn();

// ---------------------------------------------------------------- dropzone

const dropzone = $("#dropzone");
const input = $("#file-input");

const isAudio = (f) =>
  f.type.startsWith("audio/") || /\.(opus|ogg|oga|m4a|mp3|wav|aac|flac)$/i.test(f.name);

function addFiles(list) {
  const audios = [...list].filter(isAudio);
  files.push(...audios);
  renderQueue();
}

dropzone.addEventListener("click", () => input.click());
dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
});
input.addEventListener("change", () => { addFiles(input.files); input.value = ""; });

dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("drag"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("drag");
  addFiles(e.dataTransfer.files);
});

// ---------------------------------------------------------------- queue

function renderQueue() {
  $("#queue").hidden = files.length === 0;
  const ol = $("#file-list");
  ol.innerHTML = "";
  files.forEach((f, i) => {
    const li = el("li", "msg");
    li.innerHTML = `<span class="idx">${i + 1}</span>${BARS}`;
    const meta = el("span", "meta");
    meta.append(el("span", "name", f.name), el("span", "sub", fmtSize(f.size)));
    const ctl = el("span", "ctl");
    for (const [act, label, disabled] of [
      ["up", "↑", i === 0],
      ["down", "↓", i === files.length - 1],
      ["rm", "×", false],
    ]) {
      const b = el("button", null, label);
      b.dataset.act = act;
      b.dataset.i = i;
      b.disabled = disabled;
      b.setAttribute("aria-label", { up: "Mover para cima", down: "Mover para baixo", rm: "Remover" }[act]);
      ctl.append(b);
    }
    li.append(meta, ctl);
    ol.append(li);
  });
}

$("#file-list").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const i = +b.dataset.i;
  if (b.dataset.act === "rm") files.splice(i, 1);
  else {
    const j = b.dataset.act === "up" ? i - 1 : i + 1;
    [files[i], files[j]] = [files[j], files[i]];
  }
  renderQueue();
});

$("#submit").addEventListener("click", async () => {
  const btn = $("#submit");
  btn.disabled = true;
  btn.textContent = "Enviando…";
  try {
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name)); // list order = group order
    const res = await fetch("/api/jobs", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    files = [];
    location.hash = `g/${data.id}`;
  } catch (err) {
    alert(`Falha ao enviar: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Transcrever e resumir";
  }
});

// ---------------------------------------------------------------- history

async function loadHistory() {
  const items = await fetch("/api/history").then((r) => r.json()).catch(() => []);
  $("#history").hidden = items.length === 0;
  const ol = $("#history-list");
  ol.innerHTML = "";
  for (const it of items) {
    const li = el("li");
    const a = el("a");
    a.href = `#g/${it.id}`;
    a.append(el("span", "h-files", it.files.join(" + ")), el("span", "h-date", fmtDate(it.created_at)));
    li.append(a);
    ol.append(li);
  }
}

// ---------------------------------------------------------------- job

const STATUS_LABEL = { pending: "aguardando", cached: "já transcrito", done: "ok" };
const PHASE_LABEL = { queued: "Na fila…", summarizing: "Gerando resumo…" };

async function pollJob(id) {
  if (location.hash !== `#g/${id}`) return; // user navigated away; stop polling
  let data;
  try {
    const res = await fetch(`/api/jobs/${id}`);
    data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
  } catch (err) {
    return showError(err.message);
  }

  if (data.status === "error") return showError(data.error || "erro desconhecido");
  if (data.status === "done") return renderResult(data);

  renderProgress(data);
  setTimeout(() => pollJob(id), 1000);
}

function renderProgress(data) {
  $("#job-progress").hidden = false;
  $("#job-result").hidden = true;
  $("#job-error").hidden = true;

  let phase = PHASE_LABEL[data.status];
  if (!phase) {
    const cur = data.files.filter((f) => ["done", "cached"].includes(f.status)).length;
    phase = `Transcrevendo ${Math.min(cur + 1, data.files.length)}/${data.files.length}…`;
  }
  $("#phase").textContent = phase;

  const ol = $("#job-files");
  ol.innerHTML = "";
  data.files.forEach((f, i) => {
    const li = el("li", `msg ${f.status}`);
    const fill = el("span", "fill");
    fill.style.width = `${Math.round((f.progress || 0) * 100)}%`;
    li.append(fill);
    li.insertAdjacentHTML("beforeend", `<span class="idx">${i + 1}</span>${BARS}`);
    const meta = el("span", "meta");
    meta.append(el("span", "name", f.name));
    const label = f.status === "transcribing"
      ? `${Math.round((f.progress || 0) * 100)}%`
      : STATUS_LABEL[f.status] || f.status;
    li.append(meta, el("span", "status-label", label));
    ol.append(li);
  });
}

function renderResult(data) {
  $("#job-progress").hidden = true;
  $("#job-error").hidden = true;
  $("#job-result").hidden = false;

  const meta = $("#result-meta");
  meta.innerHTML = "";
  (data.meta.files || []).forEach((f, i) => meta.append(el("span", "chip", `${i + 1} · ${f.name}`)));
  meta.append(el("span", "chip plain", fmtDate(data.meta.created_at)));

  // the summary markdown comes from an LLM over arbitrary audio — sanitize it
  const summary = $("#summary");
  if (data.summary) {
    summary.innerHTML = DOMPurify.sanitize(marked.parse(data.summary));
  } else {
    summary.innerHTML = "";
    summary.append(el("p", null, data.meta.summary_error
      ? `O resumo falhou: ${data.meta.summary_error}. Envie os mesmos áudios de novo para tentar outra vez (a transcrição já fica em cache).`
      : "Sem resumo."));
  }
  const box = $("#transcript");
  box.innerHTML = "";
  const sentences = (data.transcript || "").split(/(?<=[.!?…])\s+/).filter(Boolean);
  if (!sentences.length) box.textContent = "(transcrição vazia)";
  else sentences.forEach((s) => box.append(el("p", null, s)));
}

function showError(msg) {
  $("#job-progress").hidden = true;
  $("#job-result").hidden = true;
  const box = $("#job-error");
  box.hidden = false;
  box.textContent = `Algo deu errado: ${msg}. Volte e envie os áudios de novo para tentar outra vez.`;
}

// ---------------------------------------------------------------- router

function route() {
  const m = location.hash.match(/^#g\/([0-9a-f]+)/i);
  $("#view-home").hidden = !!m;
  $("#view-job").hidden = !m;
  if (m) {
    $("#job-progress").hidden = true;
    $("#job-result").hidden = true;
    $("#job-error").hidden = true;
    pollJob(m[1]);
  } else {
    renderQueue();
    loadHistory();
  }
}

window.addEventListener("hashchange", route);
route();

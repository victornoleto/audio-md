/* audio-md web — reorderable dropzone → job → result. Vanilla JS, no build step. */

const $ = (s) => document.querySelector(s);

let files = []; // Ordered entries: {type, file?, url?, name}

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

// ---------------------------------------------------------------- content composer

const dropzone = $("#dropzone");
const input = $("#file-input");
const urlInput = $("#url-input");
const urlBtn = $("#url-go");
const MAX_UPLOAD = 1 << 30;
let sending = false;

const isMedia = (f) =>
  /^(audio|video)\//.test(f.type) || /\.(opus|ogg|oga|m4a|mp3|wav|aac|flac|mp4|mov|mkv|webm|avi|m4v)$/i.test(f.name);
const mediaLabel = (f) => f.type === "youtube" ? "YouTube"
  : /^video\//.test(f.file.type) || /\.(mp4|mov|mkv|webm|avi|m4v)$/i.test(f.name) ? "Vídeo" : "Áudio";
const totalBytes = () => files.reduce((total, f) => total + (f.file?.size || 0), 0);

function message(id, text) {
  const box = $(id);
  box.textContent = text;
  box.hidden = !text;
}

function updateComposer() {
  const bytes = totalBytes();
  const hasDraft = !!urlInput.value.trim();
  $("#queue-total").textContent = `${files.length} ${files.length === 1 ? "item" : "itens"} na lista · ${bytes ? fmtSize(bytes) : "0 MB"} em arquivos`;
  message("#size-error", bytes > MAX_UPLOAD ? "Os arquivos ultrapassam 1 GiB. Remova algum item para enviar." : "");
  $("#submit").disabled = sending || bytes > MAX_UPLOAD || (!files.length && !hasDraft);
  $("#submit").textContent = sending ? "Enviando…" : "Transcrever e resumir";
  $("#clear-queue").disabled = sending || (!files.length && !hasDraft);
  urlBtn.disabled = sending || !hasDraft;
  urlInput.disabled = input.disabled = $("#select-files").disabled = sending;
  dropzone.setAttribute("aria-busy", String(sending));
}

function addFiles(list) {
  if (sending) return;
  const accepted = [...list].filter(isMedia);
  const rejected = [...list].filter((f) => !isMedia(f));
  files.push(...accepted.map((file) => ({ type: "file", file, name: file.name })));
  message("#file-error", rejected.length ? `Arquivos não aceitos: ${rejected.map((f) => f.name).join(", ")}` : "");
  message("#send-error", "");
  renderQueue();
}

$("#select-files").addEventListener("click", () => input.click());
input.addEventListener("change", () => { addFiles(input.files); input.value = ""; });
dropzone.addEventListener("dragover", (e) => {
  if (!Array.from(e.dataTransfer.types).includes("Files")) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = sending ? "none" : "copy";
  if (!sending) dropzone.classList.add("drag");
});
dropzone.addEventListener("dragleave", (e) => {
  if (!dropzone.contains(e.relatedTarget)) dropzone.classList.remove("drag");
});
dropzone.addEventListener("drop", (e) => {
  if (!Array.from(e.dataTransfer.types).includes("Files")) return;
  e.preventDefault();
  dropzone.classList.remove("drag");
  addFiles(e.dataTransfer.files);
});

function renderQueue() {
  $("#queue").hidden = files.length === 0;
  const ol = $("#file-list");
  ol.innerHTML = "";
  files.forEach((f, i) => {
    const li = el("li", "msg");
    li.append(el("span", "idx", String(i + 1)));
    const meta = el("span", "meta");
    meta.append(el("span", "name", f.name), el("span", "sub",
      `${mediaLabel(f)}${f.type === "file" ? ` · ${fmtSize(f.file.size)}` : ""}`));
    const ctl = el("span", "ctl");
    for (const [act, label, disabled] of [
      ["up", "↑", i === 0], ["down", "↓", i === files.length - 1], ["rm", "×", false],
    ]) {
      const b = el("button", null, label);
      b.type = "button";
      b.dataset.act = act;
      b.dataset.i = i;
      b.disabled = sending || disabled;
      b.setAttribute("aria-label", `${{ up: "Mover para cima", down: "Mover para baixo", rm: "Remover" }[act]}: ${f.name}`);
      ctl.append(b);
    }
    li.append(meta, ctl);
    ol.append(li);
  });
  updateComposer();
}

$("#file-list").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b || b.disabled || sending) return;
  const i = +b.dataset.i;
  const name = files[i].name;
  let next = i;
  if (b.dataset.act === "rm") files.splice(i, 1);
  else {
    next = b.dataset.act === "up" ? i - 1 : i + 1;
    [files[i], files[next]] = [files[next], files[i]];
  }
  message("#send-error", "");
  renderQueue();
  next = Math.min(next, files.length - 1);
  const row = $("#file-list").children[next];
  const same = row?.querySelector(`button[data-act="${b.dataset.act}"]:not(:disabled)`);
  (same || row?.querySelector("button:not(:disabled)") || $("#select-files")).focus();
  $("#queue-announcement").textContent = b.dataset.act === "rm"
    ? `${name} removido.` : `${name} movido para a posição ${next + 1}.`;
});

$("#clear-queue").addEventListener("click", () => {
  if (sending) return;
  files = [];
  urlInput.value = "";
  urlInput.removeAttribute("aria-invalid");
  for (const id of ["#file-error", "#url-error", "#send-error"]) message(id, "");
  renderQueue();
  $("#select-files").focus();
});

// Mirrors youtube.video_id_of; the server remains authoritative.
function youtubeId(value) {
  const id = /^[A-Za-z0-9_-]{11}$/;
  if (id.test(value)) return value;
  try {
    const url = new URL(value.includes("//") ? value : `https://${value}`);
    if (!["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"].includes(url.hostname)) return null;
    let candidate;
    if (url.hostname === "youtu.be") candidate = url.pathname.replace(/^\/+/, "").split("/")[0];
    else if (url.pathname === "/watch") candidate = url.searchParams.getAll("v").find(Boolean);
    else if (/^\/(shorts|live|embed)\//.test(url.pathname)) candidate = url.pathname.split("/")[2];
    return candidate && id.test(candidate) ? candidate : null;
  } catch { return null; }
}

function submitUrl() {
  if (sending) return false;
  const lines = urlInput.value.split(/\r?\n/).map((s) => s.trim());
  const invalid = lines.flatMap((s, i) => s && !youtubeId(s) ? [i + 1] : []);
  if (invalid.length) {
    message("#url-error", `Confira os links do YouTube nas linhas: ${invalid.join(", ")}. Nenhum link foi adicionado.`);
    urlInput.setAttribute("aria-invalid", "true");
    $("#url-error").focus();
    return false;
  }
  files.push(...lines.filter(Boolean).map((url) => ({ type: "youtube", url, name: url })));
  urlInput.value = "";
  urlInput.removeAttribute("aria-invalid");
  message("#url-error", "");
  message("#send-error", "");
  renderQueue();
  return true;
}

urlBtn.addEventListener("click", () => { if (submitUrl()) urlInput.focus(); });
urlInput.addEventListener("input", () => {
  message("#url-error", "");
  message("#send-error", "");
  urlInput.removeAttribute("aria-invalid");
  updateComposer();
});
urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submitUrl(); }
});

$("#submit").addEventListener("click", async () => {
  if (sending || totalBytes() > MAX_UPLOAD || !submitUrl() || !files.length) return;
  sending = true;
  renderQueue();
  try {
    const form = new FormData();
    let fileIndex = 0;
    const items = files.map((f) => {
      if (f.type === "youtube") return { type: "youtube", url: f.url };
      form.append("files", f.file, f.name);
      return { type: "file", file_index: fileIndex++ };
    });
    form.append("items", JSON.stringify(items));
    const res = await fetch("/api/jobs", { method: "POST", body: form });
    // A reverse proxy can return an HTML error, particularly for oversized uploads.
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(res.status === 413 ? "Upload grande demais (limite: 1 GiB)."
      : data.error || res.statusText || `HTTP ${res.status}`);
    if (!data.id) throw new Error("O servidor não retornou a identificação do envio.");
    location.hash = `g/${data.id}`;
  } catch (err) {
    message("#send-error", `Falha ao enviar: ${err.message} Sua lista foi mantida; tente novamente.`);
    $("#send-error").focus();
  } finally {
    sending = false;
    renderQueue();
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

const STATUS_LABEL = { pending: "aguardando", downloading: "baixando", cached: "já transcrito", done: "ok", error: "falhou" };
const PHASE_LABEL = { queued: "Na fila…", downloading: "Baixando do YouTube…", summarizing: "Gerando resumo…" };

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
  const video = data.meta.youtube;
  if (video) {
    const a = el("a", "chip", video.title || video.video_id);
    a.href = video.url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    meta.append(a);
    if (video.uploader) meta.append(el("span", "chip plain", video.uploader));
  } else {
    (data.meta.files || []).forEach((f, i) => {
      const chip = el(f.youtube ? "a" : "span", "chip", `${i + 1} · ${f.name}`);
      if (f.youtube) {
        chip.href = f.youtube.url;
        chip.target = "_blank";
        chip.rel = "noopener noreferrer";
      }
      meta.append(chip);
    });
  }
  meta.append(el("span", "chip plain", fmtDate(data.meta.created_at)));

  // the summary markdown comes from an LLM over arbitrary audio — sanitize it
  const summary = $("#summary");
  if (data.summary) {
    summary.innerHTML = DOMPurify.sanitize(marked.parse(data.summary));
  } else {
    summary.innerHTML = "";
    summary.append(el("p", null, data.meta.summary_error
      ? `O resumo falhou: ${data.meta.summary_error}. Volte e envie o mesmo grupo novamente (as transcrições já ficam em cache).`
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
  box.textContent = `Algo deu errado: ${msg}. O grupo não foi resumido. Volte à fila e tente novamente; as transcrições concluídas ficam em cache. Após recarregar a página, adicione novamente os arquivos e links.`;
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

const $ = (id) => document.getElementById(id);

function promptEl() {
  return $("prompt");
}
function promptText() {
  const el = promptEl();
  if (!el) return "";
  if (el.tagName === "TEXTAREA") return String(el.value || "").trim();
  return String(el.innerText || "").replace(/\u00a0/g, " ").trim();
}
function setPrompt(text) {
  const el = promptEl();
  if (!el) return;
  if (el.tagName === "TEXTAREA") {
    el.value = text || "";
    return;
  }
  el.textContent = text || "";
}
function focusPrompt() {
  const el = promptEl();
  if (el) el.focus();
}
function enablePrompt() {
  const el = promptEl();
  if (!el) return;
  if (el.tagName === "TEXTAREA") el.disabled = false;
  else el.contentEditable = "true";
}
function activeModel() {
  return sanitizeModel((state.session && state.session.model) || (state.settings && state.settings.model) || "");
}
function syncModelChip() {
  if ($("modelChip")) $("modelChip").textContent = (activeModel() || "model") + " ▾";
}

const MODE_LABEL = { plan: "Plan", agent: "Agent", build: "Build" };
function syncModeChip() {
  const mode = MODE_LABEL[state.mode] ? state.mode : "build";
  state.mode = mode;
  const chip = $("modeChip");
  if (chip) {
    chip.textContent = (MODE_LABEL[mode] || "Build") + " ▾";
    chip.dataset.mode = mode;
  }
  document.querySelectorAll("#modeMenu button[data-mode]").forEach((b) => {
    b.classList.toggle("on", b.dataset.mode === mode);
  });
}

function setMode(mode) {
  if (!MODE_LABEL[mode]) return;
  state.mode = mode;
  syncModeChip();
  if ($("modeMenu")) $("modeMenu").hidden = true;
}

const state = {
  settings: null,
  sessions: [],
  session: null,
  mode: "build",
  busy: false,
  preview: null,
  view: "preview",
  expanded: new Set(),
  treeNodes: [],
  pendingFiles: [],
  models: [],
  modelItems: [],
  runs: {},
};

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function tidyAssistantText(text) {
  let t = String(text || "");
  t = t.replace(/\*\(lanjut otomatis…\)\*/g, "");
  t = t.replace(/([.!?])\s*(Baik\b)/g, "$1\n\n$2");
  t = t.replace(/(Baik[,!]?\s+saya akan[\s\S]{8,280}?[.!?])(?=\s*Baik\b)/gi, "$1\n\n");
  const paras = t.split(/\n{2,}/).map((s) => s.trim()).filter(Boolean);
  const out = [];
  const isWork = (s) => /saya akan|mari saya|melanjutkan|menyelesaikan semua file|let me |i'll continue/i.test(s);
  for (const p of paras) {
    const key = p.toLowerCase().replace(/\s+/g, " ").slice(0, 96);
    const prev = out.length ? out[out.length - 1].toLowerCase().replace(/\s+/g, " ").slice(0, 96) : "";
    if (key && prev && (key === prev || (isWork(p) && isWork(out[out.length - 1])))) continue;
    out.push(p);
  }
  const work = out.filter(isWork);
  if (work.length >= 3 && work.length >= out.length - 1) return work[work.length - 1];
  return out.join("\n\n");
}

function md(text) {
  const parts = String(text || "").split(/```/);
  return parts.map((part, i) => {
    if (i % 2 === 1) {
      const nl = part.indexOf("\n");
      const code = nl === -1 ? part : part.slice(nl + 1);
      return `<pre><code>${esc(code.replace(/\n$/, ""))}</code></pre>`;
    }
    let h = esc(part);
    h = h.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    h = h.replace(/`([^`]+)`/g, "<code>$1</code>");
    h = h.replace(/\[(.+?)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
    h = h.split(/\n{2,}/).map((p) => `<p>${p.replace(/\n/g, "<br>")}</p>`).join("");
    return h;
  }).join("");
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (opts.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, { ...opts, headers });
  const raw = await res.text();
  let data = null;
  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch {
      data = raw;
    }
  }
  if (!res.ok) {
    let msg = res.status + " " + res.statusText;
    if (data && typeof data === "object") {
      const d = data.detail;
      msg = typeof d === "string" ? d : JSON.stringify(d || data);
    } else if (typeof data === "string" && data.trim()) {
      msg = data.slice(0, 500);
    }
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return data;
}

function isRunning(sid) {
  const run = sid && state.runs[sid];
  return Boolean(run && !run.done);
}

function syncBusyUi() {
  const mine = isRunning(state.session && state.session.id);
  state.busy = mine;
  if ($("sendBtn")) $("sendBtn").disabled = mine;
  if ($("stopBtn")) $("stopBtn").hidden = !mine;
  if ($("continueBtn")) $("continueBtn").disabled = mine;
  enablePrompt();
}

async function copyText(text, btn) {
  const val = String(text || "").trim();
  if (!val) return;
  try {
    await navigator.clipboard.writeText(val);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = val;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  if (btn) {
    const old = btn.textContent;
    btn.textContent = "Tersalin";
    setTimeout(() => { btn.textContent = old; }, 1200);
  }
}

function copyBtn(text) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "copy-msg";
  b.textContent = "Salin";
  b.title = "Salin teks";
  b.addEventListener("click", (e) => {
    e.stopPropagation();
    copyText(text, b);
  });
  return b;
}

function whoRow(label, text) {
  const row = document.createElement("div");
  row.className = "who";
  const span = document.createElement("span");
  span.textContent = label;
  row.appendChild(span);
  if (text) row.appendChild(copyBtn(text));
  return row;
}

function threadText() {
  const lines = [];
  const ui = (state.session && state.session.ui) || [];
  for (const m of ui) {
    const who = m.role === "user" ? "Anda" : m.role === "system" ? "sistem" : "Arka";
    if (m.text) lines.push(`${who}:\n${m.text}`);
  }
  const sid = state.session && state.session.id;
  const run = sid && state.runs[sid];
  if (run && (run.acc || run.error) && !run.done) {
    lines.push("Arka:\n" + (run.acc || run.error));
  }
  return lines.join("\n\n");
}

function paintRun(sid) {
  const run = state.runs[sid];
  if (!run || state.session?.id !== sid) return;
  const empty = $("emptyState");
  if (empty) empty.remove();
  let last = $("messages").querySelector(".msg.assistant:last-of-type");
  if (!last) {
    last = document.createElement("div");
    last.className = "msg assistant";
    last.innerHTML = `<div class="bubble"></div>`;
    last.prepend(whoRow("Arka", run.acc || run.error || ""));
    $("messages").appendChild(last);
  } else {
    const oldWho = last.querySelector(".who");
    const next = whoRow("Arka", run.acc || run.error || "");
    if (oldWho) oldWho.replaceWith(next);
    else last.prepend(next);
  }
  const bubble = last.querySelector(".bubble");
  bubble.innerHTML = "";
  for (const t of run.tools) bubble.appendChild(toolCard(t));
  if (run.acc) {
    const div = document.createElement("div");
    div.className = "md";
    div.innerHTML = md(tidyAssistantText(run.acc));
    bubble.appendChild(div);
  }
  if (run.error) {
    const err = document.createElement("div");
    err.className = "err";
    err.textContent = run.error;
    bubble.appendChild(err);
  }
  if (!run.done && !run.error && !run.acc && !run.tools.length) {
    const think = document.createElement("div");
    think.className = "thinking";
    think.textContent = "bekerja…";
    bubble.appendChild(think);
  }
  $("messages").scrollTop = $("messages").scrollHeight;
}

function closeSessMenu() {
  const m = $("sessMenu");
  if (m) m.remove();
  document.querySelectorAll(".sess .more.open").forEach((el) => el.classList.remove("open"));
}

async function deleteSessionChat(id) {
  if (!confirm("Hapus chat ini?")) return;
  await api(`/api/sessions/${id}`, { method: "DELETE" });
  if (state.session?.id === id) {
    state.session = null;
    $("messages").innerHTML = "";
    showEmpty();
  }
  await refreshSessions();
}

function openSessMenu(btn, s) {
  closeSessMenu();
  btn.classList.add("open");
  const menu = document.createElement("div");
  menu.id = "sessMenu";
  menu.className = "sess-menu";
  menu.innerHTML = `
    <button type="button" data-act="rename">Ganti nama</button>
    <button type="button" data-act="delete" class="danger">Hapus chat</button>
  `;
  document.body.appendChild(menu);
  const r = btn.getBoundingClientRect();
  const w = 176;
  let left = r.right - w;
  let top = r.bottom + 4;
  if (left < 8) left = 8;
  if (top + 88 > window.innerHeight) top = Math.max(8, r.top - 88);
  menu.style.left = left + "px";
  menu.style.top = top + "px";
  menu.addEventListener("click", async (e) => {
    const act = e.target.closest("button") && e.target.closest("button").dataset.act;
    closeSessMenu();
    if (act === "rename") await renameSession(s.id, s.title);
    if (act === "delete") await deleteSessionChat(s.id);
  });
}

function renderSessions() {
  const box = $("sessionList");
  box.innerHTML = "";
  for (const s of state.sessions) {
    const b = document.createElement("div");
    b.className = "sess" + (state.session?.id === s.id ? " on" : "") + (isRunning(s.id) ? " run" : "");
    b.innerHTML = `<span class="sess-title">${esc(s.title || "Sesi")}${s.model ? `<small>${esc(String(s.model).slice(0, 28))}</small>` : ""}</span>
      <button type="button" class="more" title="Menu sesi">⋯</button>`;
    b.addEventListener("click", (e) => {
      if (e.target.closest(".more")) return;
      closeSessMenu();
      openSession(s.id);
    });
    b.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      openSessMenu(b.querySelector(".more"), s);
    });
    b.querySelector(".more").addEventListener("click", (e) => {
      e.stopPropagation();
      const btn = e.currentTarget;
      if ($("sessMenu") && btn.classList.contains("open")) {
        closeSessMenu();
        return;
      }
      openSessMenu(btn, s);
    });
    box.appendChild(b);
  }
}

function showEmpty() {
  $("messages").innerHTML = "";
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.id = "emptyState";
  empty.innerHTML = `
    <div class="mark lg">A</div>
    <h1>Apa yang ingin dikerjakan?</h1>
    <p>Arka membaca repo, merencanakan perubahan, mengedit file, dan menjalankan perintah.</p>
    <div class="hints">
      <button type="button" data-fill="Jelaskan struktur proyek ini.">Jelaskan proyek</button>
      <button type="button" data-fill="Buat file hello.py yang mencetak Halo Arka, lalu jalankan.">Tulis + jalankan</button>
      <button type="button" data-fill="Cari di web cara pakai FastAPI StreamingResponse.">Cari di internet</button>
    </div>`;
  $("messages").appendChild(empty);
  empty.querySelectorAll("[data-fill]").forEach((btn) => {
    btn.addEventListener("click", () => {
      setPrompt(btn.dataset.fill);
      focusPrompt();
    });
  });
  if (!state.session) $("sessionTitle").textContent = "Pilih sesi";
}

function toolCard(t) {
  const el = document.createElement("div");
  el.className = "tool";
  const args = t.args || {};
  const path = args.path || "";
  const short = `${t.name} ${esc(JSON.stringify(args)).slice(0, 80)}`;
  el.innerHTML = `<div class="tool-h"><span><b>${esc(t.name)}</b> · ${short.slice(t.name.length + 1)}</span>
    <span>${path ? `<button type="button" class="btn ghost xs lihat">Lihat</button>` : ""} ${t.status === "running" ? "…" : "ok"}</span></div>
    <pre>${esc((t.result || JSON.stringify(args)).toString().slice(0, 4000))}</pre>`;
  el.querySelector(".tool-h").addEventListener("click", (e) => {
    if (e.target.closest(".lihat")) return;
    el.classList.toggle("open");
  });
  const lihat = el.querySelector(".lihat");
  if (lihat && path) {
    lihat.addEventListener("click", (e) => {
      e.stopPropagation();
      openFile(path);
    });
  }
  return el;
}

function renderMessages(session) {
  const box = $("messages");
  box.innerHTML = "";
  const ui = session.ui || [];
  if (!ui.length) {
    showEmpty();
    $("sessionTitle").textContent = session.title || "Sesi";
    return;
  }
  for (const m of ui) {
    const wrap = document.createElement("div");
    wrap.className = `msg ${m.role}`;
    const who = m.role === "user" ? "Anda" : m.role === "system" ? "sistem" : "Arka";
    wrap.appendChild(whoRow(who, m.text || ""));
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    wrap.appendChild(bubble);
    for (const t of m.tools || []) bubble.appendChild(toolCard({ ...t, status: "done" }));
    if (m.text) {
      const div = document.createElement("div");
      div.className = "md";
      div.innerHTML = md(m.role === "assistant" ? tidyAssistantText(m.text) : m.text);
      bubble.appendChild(div);
    }
    box.appendChild(wrap);
  }
  box.scrollTop = box.scrollHeight;
  $("sessionTitle").textContent = session.title || "Sesi";
  renderTodos(session.todos || []);
}

function renderTodos(items) {
  const el = $("todos");
  if (!items.length) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.innerHTML = items.map((t) => `<span class="${t.status === "done" ? "done" : ""}">${esc(t.status)} · ${esc(t.content)}</span>`).join("");
}

function currentAssistantEls() {
  let last = $("messages").querySelector(".msg.assistant:last-of-type");
  if (!last) {
    last = document.createElement("div");
    last.className = "msg assistant";
    last.innerHTML = `<div class="who">Arka</div><div class="bubble"><div class="md"></div></div>`;
    $("messages").appendChild(last);
  }
  const bubble = last.querySelector(".bubble");
  let mdEl = bubble.querySelector(".md");
  if (!mdEl) {
    mdEl = document.createElement("div");
    mdEl.className = "md";
    bubble.appendChild(mdEl);
  }
  return { last, bubble, mdEl };
}

async function refreshSessions() {
  state.sessions = await api("/api/sessions");
  renderSessions();
}

async function openSession(id) {
  state.session = await api(`/api/sessions/${id}`);
  state.mode = state.session.mode || "build";
  document.querySelectorAll("#modeSeg button").forEach((b) => {
    b.classList.toggle("on", b.dataset.mode === state.mode);
  });
  renderMessages(state.session);
  if (isRunning(id)) paintRun(id);
  renderSessions();
  syncBusyUi();
  if (state.session.last_files) showChanged(state.session.last_files);
  enablePrompt();
  focusPrompt();
  syncModelChip();
}

async function ensureSession() {
  if (state.session) return state.session;
  const s = await api("/api/sessions", {
    method: "POST",
    body: JSON.stringify({
      workspace: state.settings?.workspace || "",
      model: (state.settings && state.settings.model) || "",
    }),
  });
  state.session = s;
  await refreshSessions();
  renderSessions();
  $("sessionTitle").textContent = s.title;
  const empty = $("emptyState");
  if (empty) empty.remove();
  return s;
}

function parentPath(p) {
  const i = p.lastIndexOf("/");
  return i === -1 ? "" : p.slice(0, i);
}

function isVisibleNode(n) {
  let p = parentPath(n.path);
  while (p) {
    if (!state.expanded.has(p)) return false;
    p = parentPath(p);
  }
  return true;
}

function drawTree() {
  const tree = $("tree");
  if (!tree) return;
  tree.innerHTML = "";
  const nodes = state.treeNodes || [];
  for (const n of nodes) {
    if (n.depth > 0 && !isVisibleNode(n)) continue;
    const d = document.createElement("div");
    const open = n.dir && state.expanded.has(n.path);
    d.className = "n" + (n.dir ? " dir" : "");
    d.style.paddingLeft = `${8 + n.depth * 14}px`;
    d.innerHTML = n.dir ? `<span class="twisty">${open ? "▾" : "▸"}</span> ${esc(n.name)}` : esc(n.name);
    d.title = n.path;
    d.addEventListener("click", async () => {
      if (n.dir) {
        if (state.expanded.has(n.path)) state.expanded.delete(n.path);
        else state.expanded.add(n.path);
        drawTree();
        return;
      }
      document.querySelectorAll(".tree .n").forEach((el) => el.classList.remove("on"));
      d.classList.add("on");
      await openFile(n.path);
      const cur = promptText();
      if (cur.indexOf("@" + n.path) === -1) {
        setPrompt((cur ? cur + " " : "") + "@" + n.path);
      }
    });
    tree.appendChild(d);
  }
}

async function loadTree() {
  try {
    const data = await api("/api/tree");
    $("wsName").textContent = data.workspace;
    $("wsName").title = data.workspace;
    state.treeNodes = data.nodes || [];
    const roots = new Set(state.treeNodes.filter((n) => n.dir && n.depth === 0).map((n) => n.path));
    if (state.expanded.size === 0) {
      roots.forEach((p) => state.expanded.add(p));
    }
    drawTree();
  } catch (e) {
    if ($("tree")) $("tree").textContent = e.message;
  }
}

async function loadSettings() {
  state.settings = await api("/api/settings");
  $("cfgWorkspace").value = state.settings.workspace || "";
  $("cfgBase").value = state.settings.api_base || "";
  $("cfgModel").value = state.settings.model || "";
  $("cfgKey").value = state.settings.api_key || "";
  syncModelChip();
  $("demoPill").hidden = Boolean(state.settings.api_key);
  $("wsName").textContent = state.settings.workspace || "folder proyek";
}

function applyModeButtons() {
  const chip = $("modeChip");
  const menu = $("modeMenu");
  if (!chip || !menu) return;
  chip.addEventListener("click", (e) => {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
    if ($("modelMenu")) $("modelMenu").hidden = true;
  });
  menu.querySelectorAll("button[data-mode]").forEach((b) => {
    b.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      setMode(b.dataset.mode);
    });
  });
  document.addEventListener("click", (e) => {
    if (!menu.hidden && !e.target.closest(".mode-wrap")) menu.hidden = true;
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") menu.hidden = true;
  });
  syncModeChip();
}

function isImageFile(f) {
  return Boolean(f && String(f.type || "").startsWith("image/"));
}

function renderChips() {
  const row = $("attachRow");
  if (!row) return;
  if (!state.pendingFiles.length) {
    row.hidden = true;
    row.innerHTML = "";
    return;
  }
  row.hidden = false;
  row.innerHTML = "";
  state.pendingFiles.forEach((f, i) => {
    const span = document.createElement("span");
    span.className = "chip" + (isImageFile(f) ? " img" : "");
    if (isImageFile(f)) {
      const img = document.createElement("img");
      img.alt = f.name;
      img.src = URL.createObjectURL(f);
      span.appendChild(img);
    }
    const label = document.createElement("span");
    label.textContent = f.name;
    span.appendChild(label);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "×";
    btn.addEventListener("click", () => {
      state.pendingFiles.splice(i, 1);
      renderChips();
    });
    span.appendChild(btn);
    row.appendChild(span);
  });
}

function addPending(list) {
  for (const f of list) {
    if (!f) continue;
    const dup = state.pendingFiles.some((x) => {
      if (x.size === f.size && (x.type || "") === (f.type || "")) return true;
      if (x.name === f.name && x.size === f.size) return true;
      return false;
    });
    if (!dup) state.pendingFiles.push(f);
  }
  renderChips();
}

function pasteImageName(file, i) {
  const raw = (file && file.type) || "image/png";
  let ext = (raw.split("/")[1] || "png").toLowerCase();
  if (ext === "jpeg") ext = "jpg";
  if (ext === "svg+xml") ext = "svg";
  ext = ext.replace(/[^a-z0-9]/g, "") || "png";
  return `paste-${Date.now()}-${i + 1}.${ext}`;
}

function dataUrlToFile(url, i) {
  const m = /^data:([^;]+);base64,(.+)$/.exec(String(url || ""));
  if (!m) return null;
  try {
    const bin = atob(m[2]);
    const arr = new Uint8Array(bin.length);
    for (let j = 0; j < bin.length; j++) arr[j] = bin.charCodeAt(j);
    return new File([arr], pasteImageName({ type: m[1] }, i), { type: m[1] });
  } catch {
    return null;
  }
}

function filesFromClipboard(e) {
  const cd = e.clipboardData;
  if (!cd) return [];
  const seen = new Set();
  const out = [];
  const add = (file) => {
    if (!isImageFile(file)) return;
    const key = `${file.size}:${file.type || "image/png"}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push(file);
  };
  if (cd.items && cd.items.length) {
    for (const it of cd.items) {
      if (it.kind === "file") add(it.getAsFile());
    }
  }
  if (!out.length && cd.files && cd.files.length) {
    for (const f of cd.files) add(f);
  }
  if (!out.length) {
    try {
      const html = cd.getData && cd.getData("text/html");
      const m = html && html.match(/src=["'](data:image\/[^"']+)["']/i);
      if (m) add(dataUrlToFile(m[1], 0));
    } catch (err) {}
  }
  return out;
}

function namedPasteFiles(imgs) {
  return imgs.map((f, i) => new File([f], pasteImageName(f, i), { type: f.type || "image/png" }));
}

async function filesFromNavigatorClipboard() {
  if (!navigator.clipboard || !navigator.clipboard.read) return [];
  try {
    const items = await navigator.clipboard.read();
    const out = [];
    for (const item of items) {
      const type = (item.types || []).find((t) => String(t).startsWith("image/"));
      if (!type) continue;
      const blob = await item.getType(type);
      out.push(new File([blob], pasteImageName({ type }, out.length), { type }));
    }
    return out;
  } catch {
    return [];
  }
}

async function fileFromNativeClipboard() {
  try {
    const data = await api("/api/clipboard-image", { method: "POST", body: "{}" });
    if (!data || !data.ok || !data.data) return null;
    const bin = atob(data.data);
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return new File([arr], data.name || pasteImageName({ type: data.mime }, 0), { type: data.mime || "image/png" });
  } catch {
    return null;
  }
}

async function attachClipboardImages(opts = {}) {
  const fromEvent = opts.files || [];
  if (fromEvent.length) {
    addPending(namedPasteFiles(fromEvent));
    stripPromptImages();
    return true;
  }
  const nav = await filesFromNavigatorClipboard();
  if (nav.length) {
    addPending(namedPasteFiles(nav));
    stripPromptImages();
    return true;
  }
  const native = await fileFromNativeClipboard();
  if (native) {
    addPending([native]);
    stripPromptImages();
    return true;
  }
  if (opts.warn) alert("Tidak ada gambar di clipboard. Copy/screenshot dulu, lalu Ctrl+V atau tombol 📋.");
  return false;
}

function stripPromptImages() {
  const el = promptEl();
  if (!el || el.tagName === "TEXTAREA") return;
  el.querySelectorAll("img").forEach((img) => img.remove());
}

async function harvestPromptImages() {
  const el = promptEl();
  if (!el || el.tagName === "TEXTAREA") return 0;
  const imgs = [...el.querySelectorAll("img")];
  let n = 0;
  for (let i = 0; i < imgs.length; i++) {
    const img = imgs[i];
    let file = null;
    const src = img.getAttribute("src") || "";
    if (src.startsWith("data:")) file = dataUrlToFile(src, i);
    else if (src.startsWith("blob:")) {
      try {
        const blob = await fetch(src).then((r) => r.blob());
        file = new File([blob], pasteImageName({ type: blob.type || "image/png" }, i), { type: blob.type || "image/png" });
      } catch (err) {}
    }
    img.remove();
    if (file) {
      addPending([file]);
      n += 1;
    }
  }
  return n;
}

let pasteBusy = false;
function onPasteImages(e) {
  if (pasteBusy) {
    e.preventDefault();
    return true;
  }
  const imgs = filesFromClipboard(e);
  const text = (e.clipboardData && e.clipboardData.getData && e.clipboardData.getData("text")) || "";
  if (imgs.length) {
    e.preventDefault();
    pasteBusy = true;
    attachClipboardImages({ files: imgs }).finally(() => { pasteBusy = false; });
    return true;
  }
  const hadText = Boolean(text && text.trim());
  pasteBusy = true;
  setTimeout(async () => {
    try {
      const n = await harvestPromptImages();
      if (n || hadText) return;
      await attachClipboardImages({});
    } finally {
      pasteBusy = false;
    }
  }, 50);
  return false;
}

function fileToImagePayload(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const s = String(r.result || "");
      const m = /^data:([^;]+);base64,(.+)$/.exec(s);
      if (!m || m[2].length > 5_000_000) {
        resolve(null);
        return;
      }
      resolve({ name: file.name, mime: m[1], data: m[2] });
    };
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

async function uploadPending() {
  if (!state.pendingFiles.length) return [];
  const fd = new FormData();
  for (const f of state.pendingFiles) fd.append("files", f);
  const res = await fetch("/api/upload", { method: "POST", body: fd });
  const raw = await res.text();
  let data;
  try { data = JSON.parse(raw); } catch { throw new Error(raw.slice(0, 300)); }
  if (!res.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : raw.slice(0, 300));
  state.pendingFiles = [];
  renderChips();
  return data.files || [];
}

async function send() {
  await harvestPromptImages();
  const text = promptText();
  await ensureSession();
  const sid = state.session.id;
  if ((!text && !state.pendingFiles.length) || isRunning(sid)) return;
  setPrompt("");
  const pendingSnap = [...state.pendingFiles];
  let attached = [];
  let images = [];
  try {
    images = (await Promise.all(pendingSnap.filter(isImageFile).map(fileToImagePayload))).filter(Boolean);
    attached = await uploadPending();
  } catch (e) {
    setPrompt(text);
    state.pendingFiles = pendingSnap;
    renderChips();
    alert("Gagal unggah: " + e.message);
    return;
  }
  let bodyText = text || "Lihat gambar/file terlampir.";
  if (attached.length) {
    const bits = attached.map((f) => {
      if (f.error) return `- ${f.name}: ${f.error}`;
      const body = f.content ? `\n\n### ${f.path}\n\n\`\`\`\n${f.content}\n\`\`\`\n` : `\n- file: ${f.path} (${f.size} byte)`;
      return body;
    }).join("\n");
    bodyText += "\n\nLampiran tersimpan di folder inbox:\n" + bits;
  }
  const empty = $("emptyState");
  if (empty) empty.remove();

  const user = document.createElement("div");
  user.className = "msg user";
  user.appendChild(whoRow("Anda", text || "(lampiran)"));
  const ub = document.createElement("div");
  ub.className = "bubble";
  ub.innerHTML = `${esc(text || "(lampiran)")}${attached.length ? `<div class="muted">📎 ${attached.map((f) => esc(f.name || f.path)).join(", ")}</div>` : ""}`;
  const thumbs = pendingSnap.filter(isImageFile);
  if (thumbs.length) {
    const row = document.createElement("div");
    row.className = "msg-thumbs";
    for (const f of thumbs) {
      const img = document.createElement("img");
      img.alt = f.name;
      img.src = URL.createObjectURL(f);
      row.appendChild(img);
    }
    ub.appendChild(row);
  }
  user.appendChild(ub);
  $("messages").appendChild(user);

  const ac = new AbortController();
  state.runs[sid] = { acc: "", tools: [], abort: ac, done: false, error: "" };
  paintRun(sid);
  syncBusyUi();
  renderSessions();
  try {
    const res = await fetch(`/api/sessions/${sid}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: bodyText, mode: state.mode, images }),
      signal: ac.signal,
    });
    if (!res.ok) throw new Error(await res.text());
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const chunks = buf.split("\n\n");
      buf = chunks.pop() || "";
      const run = state.runs[sid];
      if (!run) break;
      for (const chunk of chunks) {
        const lines = chunk.split("\n");
        let event = "message";
        let data = "";
        for (const line of lines) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;
        let ev = {};
        try { ev = JSON.parse(data); } catch { ev = { message: data }; }
        if (event === "tool") {
          const prev = run.tools.findIndex((t) => t.name === ev.name && t.status === "running" && JSON.stringify(t.args) === JSON.stringify(ev.args || {}));
          if (prev >= 0 && ev.status === "done") run.tools[prev] = ev;
          else run.tools.push(ev);
          const pth = ev.args && ev.args.path;
          if (state.session?.id === sid && ev.status === "done" && pth && ["write_file", "edit_file", "download_url"].includes(ev.name)) {
            openFile(pth, { inChat: true }).catch(() => {});
          }
        } else if (event === "text") {
          run.acc += ev.delta || "";
        } else if (event === "error") {
          run.error = ev.message || "Error";
          run.done = true;
        } else if (event === "done") {
          run.doneMeta = ev;
        }
        paintRun(sid);
      }
    }
  } catch (e) {
    const aborted = e && (e.name === "AbortError" || /abort/i.test(String(e.message || e)));
    if (!aborted && state.runs[sid]) state.runs[sid].error = e.message || String(e);
    paintRun(sid);
  } finally {
    const run = state.runs[sid];
    if (run) run.done = true;
    if (state.session?.id === sid) {
      const meta = run && run.doneMeta;
      if (meta && meta.title) $("sessionTitle").textContent = meta.title;
      if (meta) renderTodos(meta.todos || []);
      if (meta && meta.files_changed && meta.files_changed.length) showChanged(meta.files_changed);
      await refreshSessions();
      await loadTree();
      try {
        state.session = await api(`/api/sessions/${sid}`);
      } catch (e) {}
    } else {
      refreshSessions().catch(() => {});
    }
    syncBusyUi();
    renderSessions();
  }
}

document.addEventListener("click", (e) => {
  if ($("sessMenu") && !e.target.closest(".sess-menu") && !e.target.closest(".sess .more")) {
    closeSessMenu();
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeSessMenu();
});
$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  send();
});
if ($("attachBtn") && $("filePick")) {
  $("attachBtn").addEventListener("click", () => $("filePick").click());
  $("filePick").addEventListener("change", () => {
    addPending($("filePick").files);
    $("filePick").value = "";
  });
}
["dragenter", "dragover"].forEach((ev) => {
  $("composer").addEventListener(ev, (e) => {
    e.preventDefault();
    $("composer").classList.add("drop");
  });
});
["dragleave", "drop"].forEach((ev) => {
  $("composer").addEventListener(ev, (e) => {
    e.preventDefault();
    $("composer").classList.remove("drop");
  });
});
$("composer").addEventListener("drop", (e) => {
  if (e.dataTransfer && e.dataTransfer.files) addPending(e.dataTransfer.files);
});
document.addEventListener("paste", (e) => {
  const t = e.target;
  if (t && t.closest && t.closest("input, textarea") && t.id !== "prompt") return;
  onPasteImages(e);
}, true);
if ($("pasteImgBtn")) {
  $("pasteImgBtn").addEventListener("click", () => attachClipboardImages({ warn: true }));
}
if ($("continueBtn")) {
  $("continueBtn").addEventListener("click", () => {
    setPrompt("Lanjutkan sampai proyek benar-benar selesai. Tulis semua file yang masih kurang, jangan berhenti di tengah.");
    send();
  });
}
$("prompt").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});
$("newSession").addEventListener("click", async () => {
  const s = await api("/api/sessions", {
    method: "POST",
    body: JSON.stringify({
      workspace: state.settings?.workspace || "",
      model: (state.settings && state.settings.model) || "",
    }),
  });
  await refreshSessions();
  await openSession(s.id);
  showEmpty();
  $("sessionTitle").textContent = s.title;
  focusPrompt();
});
if ($("copyChatBtn")) {
  $("copyChatBtn").addEventListener("click", () => copyText(threadText(), $("copyChatBtn")));
}
$("undoBtn").addEventListener("click", async () => {
  if (!state.session) return;
  const r = await api(`/api/sessions/${state.session.id}/undo`, { method: "POST" });
  if (r.session) {
    state.session = r.session;
    renderMessages(state.session);
    loadTree();
  } else {
    alert(r.message || "Tidak ada undo");
  }
});
$("stopBtn").addEventListener("click", async () => {
  if (!state.session) return;
  const sid = state.session.id;
  const run = state.runs[sid];
  if (run && run.abort) {
    try { run.abort.abort(); } catch (e) {}
  }
  await api(`/api/sessions/${sid}/stop`, { method: "POST" });
});
function openSettingsModal() {
  if ($("settingsModal")) $("settingsModal").hidden = false;
  closeMoreMenu();
  closeSide();
}
function closeSide() {
  $("sidebar") && $("sidebar").classList.remove("open");
  $("sideScrim") && $("sideScrim").classList.remove("open");
}
function closeMoreMenu() {
  const m = $("moreMenu");
  if (m) m.hidden = true;
}
if ($("openSettings")) $("openSettings").addEventListener("click", openSettingsModal);
if ($("openSettingsTop")) $("openSettingsTop").addEventListener("click", openSettingsModal);
if ($("menuBtn")) {
  $("menuBtn").addEventListener("click", () => {
    const s = $("sidebar");
    const scrim = $("sideScrim");
    if (!s) return;
    const on = !s.classList.contains("open");
    s.classList.toggle("open", on);
    if (scrim) scrim.classList.toggle("open", on);
  });
}
if ($("sideScrim")) $("sideScrim").addEventListener("click", closeSide);
if ($("sessionList")) {
  $("sessionList").addEventListener("click", () => {
    if (window.matchMedia("(max-width: 900px)").matches) closeSide();
  });
}
if ($("moreBtn")) {
  $("moreBtn").addEventListener("click", (e) => {
    e.stopPropagation();
    if ($("moreMenu")) { closeMoreMenu(); return; }
    const menu = document.createElement("div");
    menu.id = "moreMenu";
    menu.className = "more-menu";
    menu.innerHTML = `
      <button type="button" data-act="settings">Pengaturan</button>
      <button type="button" data-act="model">Pilih model</button>
      <button type="button" data-act="copy">Salin chat</button>
      <button type="button" data-act="undo">Undo file</button>
    `;
    document.body.appendChild(menu);
    const r = $("moreBtn").getBoundingClientRect();
    menu.style.top = (r.bottom + 4) + "px";
    menu.style.right = Math.max(8, window.innerWidth - r.right) + "px";
    menu.addEventListener("click", (ev) => {
      const act = ev.target.closest("button") && ev.target.closest("button").dataset.act;
      closeMoreMenu();
      if (act === "settings") openSettingsModal();
      if (act === "copy" && $("copyChatBtn")) $("copyChatBtn").click();
      if (act === "undo" && $("undoBtn")) $("undoBtn").click();
      if (act === "model") openModelPicker();
    });
  });
  document.addEventListener("click", (e) => {
    if ($("moreMenu") && !e.target.closest("#moreMenu") && !e.target.closest("#moreBtn")) closeMoreMenu();
  });
}
$("useAppFolder").addEventListener("click", () => {
  const suggested = state.settings?.suggested_workspace || "/home/user/arka";
  $("cfgWorkspace").value = suggested;
});
$("closeSettings").addEventListener("click", () => { $("settingsModal").hidden = true; });
if ($("pickFolderBtn")) {
  $("pickFolderBtn").addEventListener("click", async () => {
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder) {
        const p = await window.pywebview.api.pick_folder();
        if (p && $("cfgWorkspace")) $("cfgWorkspace").value = p;
        return;
      }
    } catch (e) {}
    if (window.ArkaNative && window.ArkaNative.pickFolder) {
      window.ArkaNative.pickFolder();
      return;
    }
    alert("Pemilih folder hanya di aplikasi Arka (Windows/Android), bukan browser biasa.");
  });
}
$("saveSettings").addEventListener("click", async () => {
  try {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        workspace: $("cfgWorkspace").value.trim(),
        api_base: $("cfgBase").value.trim(),
        model: $("cfgModel").value.trim(),
        api_key: $("cfgKey").value.trim(),
      }),
    });
  } catch (e) {
    alert(e.message || String(e));
    return;
  }
  await loadSettings();
  await loadTree();
  $("settingsModal").hidden = true;
  loadModels().catch(() => {});
});
document.querySelectorAll(".presets button").forEach((b) => {
  b.addEventListener("click", () => {
    $("cfgBase").value = b.dataset.base;
    $("cfgModel").value = b.dataset.model;
    /* preset hanya isi form Pengaturan, tidak ganti model sesi yang sedang jalan */
  });
});
function bind(id, ev, fn) {
  const el = $(id);
  if (el) el.addEventListener(ev, fn);
}
bind("refreshTree", "click", loadTree);
bind("renameBtn", "click", () => {
  if (state.session) renameSession(state.session.id, state.session.title);
});
bind("sessionTitle", "click", () => {
  if (state.session) renameSession(state.session.id, state.session.title);
});
document.querySelectorAll("#viewSeg button").forEach((b) => {
  b.addEventListener("click", () => {
    state.view = b.dataset.view;
    document.querySelectorAll("#viewSeg button").forEach((x) => x.classList.toggle("on", x === b));
    renderPreview();
  });
});
bind("expandPreview", "click", () => {
  if (!state.preview) return;
  openOverlay(state.preview);
});
bind("closeOverlay", "click", () => {
  const ov = $("previewOverlay");
  if (ov) ov.hidden = true;
  if ($("overlayFrame")) $("overlayFrame").src = "about:blank";
});
applyModeButtons();
enableSplit();

function setModelStatus(msg) {
  if ($("modelStatus")) $("modelStatus").textContent = msg || "";
  if ($("cfgModelHint") && msg) $("cfgModelHint").textContent = msg;
}

const SEED_MODELS = ["auto", "auto/coding", "auto/fast", "auto/cheap", "auto/quality"];

function ensureSeedModels() {
  const have = new Set(state.models || []);
  const extra = SEED_MODELS.filter((id) => !have.has(id));
  if (extra.length) state.models = extra.concat(state.models || []);
}

function fillModelDatalist() {
  ensureSeedModels();
  const sel = $("cfgModelSelect");
  if (sel) {
    const cur = ($("cfgModel") && $("cfgModel").value) || "";
    sel.innerHTML = (state.models || []).slice(0, 500).map((id) => {
      const on = id === cur ? " selected" : "";
      return `<option value="${esc(id)}"${on}>${esc(id)}</option>`;
    }).join("");
  }
  const box = $("cfgModelBox");
  if (!box) return;
  box.innerHTML = "";
  for (const id of (state.models || []).slice(0, 80)) {
    const meta = (state.modelItems || []).find((m) => m.id === id) || {};
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = (meta.type === "combo" ? "combo · " : "") + id;
    b.addEventListener("click", () => {
      if ($("cfgModel")) $("cfgModel").value = id;
      pickModel(id);
    });
    box.appendChild(b);
  }
}

function sanitizeModel(s) {
  s = String(s || "").replace(/\u200b/g, "").trim().replace(/^["'`]+|["'`]+$/g, "");
  if (/^model\s*:/i.test(s)) s = s.replace(/^model\s*:/i, "").trim();
  const head = s.split(" (")[0].trim();
  if (head && (head.includes("/") || /^(auto|cx|cc|gg)\b/i.test(head))) s = head;
  return s.split("\n")[0].trim();
}

function renderModelList() {
  const box = $("modelList");
  if (!box) return;
  const rawQ = ($("modelSearch") && $("modelSearch").value) || "";
  const q = rawQ.toLowerCase().trim();
  const current = activeModel();
  const items = (state.models || []).filter((id) => {
    if (!q) return true;
    if (id.toLowerCase().includes(q)) return true;
    const meta = (state.modelItems || []).find((m) => m.id === id);
    return meta && String(meta.name || "").toLowerCase().includes(q);
  });
  box.innerHTML = "";
  const shown = items.slice(0, 300);
  if (!shown.length) {
    const hint = document.createElement("div");
    hint.className = "muted sm";
    hint.style.padding = "8px";
    hint.textContent = state.models.length
      ? "Tidak ada yang cocok di daftar. Enter untuk pakai ID yang ditempel."
      : "Belum ada daftar. Klik Muat daftar.";
    box.appendChild(hint);
    if (q) {
      const use = document.createElement("button");
      use.type = "button";
      use.textContent = "Pakai ID: " + sanitizeModel(rawQ);
      use.addEventListener("click", () => pickModel(sanitizeModel(rawQ)));
      box.appendChild(use);
    }
    return;
  }
  const ranked = shown.slice().sort((a, b) => {
    const ta = ((state.modelItems || []).find((m) => m.id === a) || {}).type === "combo" ? 0 : 1;
    const tb = ((state.modelItems || []).find((m) => m.id === b) || {}).type === "combo" ? 0 : 1;
    return ta - tb;
  });
  for (const id of ranked) {
    const meta = (state.modelItems || []).find((m) => m.id === id) || {};
    const b = document.createElement("button");
    b.type = "button";
    b.className = id === current ? "on" : "";
    b.textContent = (meta.type === "combo" ? "combo · " : "") + id;
    b.title = id;
    b.addEventListener("click", () => pickModel(id));
    box.appendChild(b);
  }
  if (items.length > shown.length) {
    const more = document.createElement("div");
    more.className = "muted sm";
    more.style.padding = "8px";
    more.textContent = `Menampilkan ${shown.length} dari ${items.length}. Ketik lebih spesifik.`;
    box.appendChild(more);
  }
}

async function loadModels() {
  setModelStatus("Memuat model…");
  try {
    const data = await api("/api/models", {
      method: "POST",
      body: JSON.stringify({
        api_base: ($("cfgBase") && $("cfgBase").value.trim()) || undefined,
        api_key: ($("cfgKey") && $("cfgKey").value) || undefined,
      }),
    });
    state.models = data.models || [];
    state.modelItems = data.items || [];
    fillModelDatalist();
    renderModelList();
    if (data.error && !state.models.length) setModelStatus(data.error);
    else setModelStatus(state.models.length ? `${state.models.length} model dari ${data.base || "API"}` : (data.error || "Daftar kosong"));
  } catch (e) {
    setModelStatus(e.message || "Gagal memuat model");
  }
}

function openModelPicker() {
  closeMoreMenu();
  const menu = $("modelMenu");
  const wrap = document.querySelector(".model-wrap");
  if (!menu) return;
  if (window.matchMedia("(max-width: 900px)").matches) {
    document.body.appendChild(menu);
    menu.classList.add("mobile-sheet");
  } else if (wrap) {
    wrap.appendChild(menu);
    menu.classList.remove("mobile-sheet");
  }
  menu.hidden = false;
  if ($("modelSearch")) $("modelSearch").focus();
  if (!state.models.length) loadModels();
  else renderModelList();
}

async function pickModel(id) {
  id = sanitizeModel(id);
  if (!id) return;
  try {
    if (state.session) {
      state.session.model = id;
      await api(`/api/sessions/${state.session.id}`, { method: "PATCH", body: JSON.stringify({ model: id }) });
    } else {
      if (state.settings) state.settings.model = id;
      if ($("cfgModel")) $("cfgModel").value = id;
      await api("/api/settings", { method: "PUT", body: JSON.stringify({ model: id }) });
    }
  } catch (e) {
    alert(e.message || String(e));
    return;
  }
  syncModelChip();
  renderModelList();
  if ($("modelMenu")) $("modelMenu").hidden = true;
}

if ($("modelChip") && $("modelMenu")) {
  $("modelChip").addEventListener("click", (e) => {
    e.stopPropagation();
    if ($("modeMenu")) $("modeMenu").hidden = true;
    if (!$("modelMenu").hidden) $("modelMenu").hidden = true;
    else openModelPicker();
  });
  document.addEventListener("click", (e) => {
    const menu = $("modelMenu");
    if (!menu || menu.hidden) return;
    if (e.target.closest(".model-wrap") || e.target.closest("#modelMenu") || e.target.closest("#moreBtn")) return;
    menu.hidden = true;
  });
}
if ($("cfgModelSelect")) {
  $("cfgModelSelect").addEventListener("change", () => {
    const id = $("cfgModelSelect").value;
    if ($("cfgModel")) $("cfgModel").value = id;
    pickModel(id);
  });
}
if ($("modelSearch")) {
  $("modelSearch").addEventListener("input", renderModelList);
  $("modelSearch").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const q = sanitizeModel($("modelSearch").value);
    if (!q) return;
    const hits = (state.models || []).filter((id) => id.toLowerCase().includes(q.toLowerCase()));
    pickModel(hits.length === 1 ? hits[0] : q);
  });
}
if ($("cfgModel")) {
  $("cfgModel").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      pickModel($("cfgModel").value);
    }
  });
}
if ($("refreshModels")) $("refreshModels").addEventListener("click", loadModels);
if ($("loadModels")) $("loadModels").addEventListener("click", loadModels);

async function renameSession(id, current) {
  const next = prompt("Nama sesi", current || "");
  if (next == null) return;
  const s = await api(`/api/sessions/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title: next }),
  });
  if (state.session?.id === id) {
    state.session = { ...state.session, ...s };
    $("sessionTitle").textContent = s.title;
  }
  await refreshSessions();
}

function paneOff(el) {
  if (!el) return;
  el.classList.add("is-off");
  el.hidden = true;
}
function paneOn(el) {
  if (!el) return;
  el.classList.remove("is-off");
  el.hidden = false;
}

function hidePreviewPanes() {
  paneOff($("previewFrame"));
  paneOff($("previewHtml"));
  paneOff($("previewImg"));
  paneOff($("previewMd"));
  paneOff($("preview"));
}

function fillIframe(iframe, html) {
  if (!iframe) return false;
  paneOn(iframe);
  const src = html && html.trim() ? html : "<!DOCTYPE html><p>(file HTML kosong)</p>";
  const write = () => {
    try {
      const doc = iframe.contentDocument;
      if (!doc) return false;
      doc.open();
      doc.write(src);
      doc.close();
      return true;
    } catch (e) {
      return false;
    }
  };
  if (!write()) {
    iframe.src = "about:blank";
    iframe.onload = write;
    setTimeout(write, 40);
  }
  return true;
}

function enableSplit() {
  const root = document.documentElement;
  document.querySelectorAll(".gutter-x").forEach((g) => {
    g.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const kind = g.dataset.split;
      const start = e.clientX;
      const sideW = $("sidebar") ? $("sidebar").getBoundingClientRect().width : 240;
      const filesW = $("filesPanel") ? $("filesPanel").getBoundingClientRect().width : 420;
      const move = (ev) => {
        const dx = ev.clientX - start;
        if (kind === "side") {
          root.style.setProperty("--side", Math.min(520, Math.max(160, sideW + dx)) + "px");
        } else {
          root.style.setProperty("--files", Math.min(900, Math.max(280, filesW - dx)) + "px");
        }
      };
      const up = () => {
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
        document.body.style.cursor = "";
      };
      document.body.style.cursor = "col-resize";
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    });
  });
  document.querySelectorAll(".gutter-y").forEach((g) => {
    g.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const start = e.clientY;
      const h = $("tree") ? $("tree").getBoundingClientRect().height : 140;
      const move = (ev) => {
        root.style.setProperty("--tree", Math.min(560, Math.max(48, h + (ev.clientY - start))) + "px");
      };
      const up = () => {
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
        document.body.style.cursor = "";
      };
      document.body.style.cursor = "row-resize";
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    });
  });
}

function paintHtml(host, html) {
  if (!host) return;
  const src = html || "<p style='padding:12px;color:#666'>(file HTML kosong)</p>";
  let root = host.shadowRoot;
  if (!root) root = host.attachShadow({ mode: "open" });
  let bodyHtml = src;
  let extraCss = "";
  try {
    const doc = new DOMParser().parseFromString(src, "text/html");
    extraCss = [...doc.querySelectorAll("style")].map((s) => s.textContent).join("\n");
    bodyHtml = doc.body ? doc.body.innerHTML : src;
  } catch (e) {
    bodyHtml = `<pre>${esc(src)}</pre>`;
  }
  root.innerHTML = `<style>
    :host { display:block; background:#fff; color:#111; }
    html,body,.wrap { margin:0; padding:16px; font-family:"Segoe UI",sans-serif; line-height:1.45; }
    ${extraCss}
  </style><div class="wrap">${bodyHtml}</div>`;
}

function openOverlay(p) {
  if (!p || !$("previewOverlay")) return;
  $("overlayName").textContent = p.path || "Preview";
  $("previewOverlay").hidden = false;
  const frame = $("overlayFrame");
  if (p.kind === "html") fillIframe(frame, p.content);
  else if (p.kind === "markdown") fillIframe(frame, `<!DOCTYPE html><body style="font-family:Segoe UI;padding:24px;background:#111;color:#eee">${md(p.content || "")}</body>`);
  else fillIframe(frame, `<!DOCTYPE html><pre style="white-space:pre-wrap;padding:16px">${esc(p.content || "")}</pre>`);
}

function htmlUrl(html) {
  const blob = new Blob([html || "<p>(kosong)</p>"], { type: "text/html;charset=utf-8" });
  return URL.createObjectURL(blob);
}

function renderPreview() {
  const p = state.preview;
  if (!p) {
    hidePreviewPanes();
    paneOn($("preview"));
    $("preview").textContent = "Pilih file di daftar, atau minta AI membuat index.html.";
    $("previewName").textContent = "Preview file";
    return;
  }
  $("previewName").textContent = p.path + (p.kind ? ` · ${p.kind}` : "");
  hidePreviewPanes();
  const wantPreview = state.view === "preview";
  if (wantPreview && p.kind === "html") {
    const ok = fillIframe($("previewFrame"), p.content);
    if (!ok) {
      paneOn($("previewHtml"));
      paintHtml($("previewHtml"), p.content);
    }
    return;
  }
  if (wantPreview && p.kind === "image") {
    paneOn($("previewImg"));
    $("previewImg").src = `/api/preview-file?path=${encodeURIComponent(p.path)}`;
    return;
  }
  if (wantPreview && p.kind === "markdown") {
    paneOn($("previewMd"));
    $("previewMd").innerHTML = md(p.content || "");
    return;
  }
  paneOn($("preview"));
  $("preview").textContent = p.content || "(tidak ada teks)";
}

async function openFile(path, opts = {}) {
  if ($("previewName")) $("previewName").textContent = "Memuat " + path + "…";
  try {
    const f = await api("/api/read-file", {
      method: "POST",
      body: JSON.stringify({ rel: path }),
    });
    state.preview = f;
    if (["html", "markdown", "image"].includes(f.kind)) state.view = "preview";
    else state.view = "code";
    document.querySelectorAll("#viewSeg button").forEach((x) => x.classList.toggle("on", x.dataset.view === state.view));
    renderPreview();
    if (opts.inChat && typeof attachChatPreview === "function") {
      attachChatPreview(opts.chatRoot || document.querySelector(".msg.assistant:last-of-type .bubble"), f);
    }
  } catch (e) {
    hidePreviewPanes();
    paneOn($("preview"));
    const msg = "Gagal preview: " + (e && e.message ? e.message : e) + "\n" + path;
    if ($("preview")) $("preview").textContent = msg;
  }
}

function showChanged(files) {
  const box = $("changedFiles");
  const items = (files || []).filter((f) => f.action !== "delete" && f.path);
  if (!items.length) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  box.innerHTML = items.map((f) => `<button type="button" data-path="${esc(f.path)}">${esc(f.path)}</button>`).join("");
  box.querySelectorAll("button").forEach((b) => {
    b.addEventListener("click", () => openFile(b.dataset.path));
  });
  openFile(items[items.length - 1].path);
}

(async function init() {
  await loadSettings();
  await refreshSessions();
  await loadTree();
  if (!state.settings.workspace) {
    $("settingsModal").hidden = false;
  }
  if (state.sessions.length) {
    await openSession(state.sessions[0].id);
  } else {
    showEmpty();
  }
  loadModels().catch(() => {});
})();

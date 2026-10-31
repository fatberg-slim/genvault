"use strict";
/* genvault v2 frontend — vanilla JS, no build step.
   Shell (topbar) renders once; the main area re-renders on data changes so the
   search input never loses focus. Inbox edits live on the asset objects
   (`_f` fields) and survive refreshes. */

/* ---------- tiny helpers ---------- */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const fmtSize = (b) => {
  if (!b && b !== 0) return "";
  if (b >= 1 << 30) return (b / (1 << 30)).toFixed(2) + " GB";
  if (b >= 1 << 20) return (b / (1 << 20)).toFixed(1) + " MB";
  return Math.round(b / 1024) + " KB";
};
const fmtDur = (s) => {
  if (!s) return "";
  s = Math.round(s);
  return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} min` : `${s}s`;
};
const fmtDate = (iso) => (iso || "").replace("T", " ").slice(0, 16);

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}
const jpost = (p, body) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
const jpatch = (p, body) => api(p, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const jdel = (p) => api(p, { method: "DELETE" });

function toast(msg, err = false) {
  const el = document.createElement("div");
  el.className = `px-4 py-2 rounded-lg shadow-xl text-sm border whitespace-pre-line max-w-xl ${err ? "bg-red-950 border-red-700 text-red-200" : "bg-zinc-800 border-zinc-600 text-zinc-100"}`;
  el.textContent = msg;
  $("#gv-toasts").appendChild(el);
  setTimeout(() => el.remove(), err ? 6000 : 3000);
}

async function copyText(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${label} copied`);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); ta.remove();
    toast(`${label} copied`);
  }
}

/* Stop a <video>/<audio> AND abort its download. pause()+remove() alone left
   the HTTP range request open, so the server kept the file busy and filing
   it failed with "used by another process". */
function unloadMedia(root) {
  if (!root) return;
  const els = root.matches && root.matches("video,audio") ? [root] : root.querySelectorAll("video,audio");
  els.forEach((m) => {
    try { m.pause(); m.removeAttribute("src"); m.load(); } catch {}
  });
}

/* Release every player/preview of one asset before it is moved or deleted. */
function releaseAsset(id) {
  $$(`.gv-card[data-id="${Number(id)}"]`).forEach(unloadMedia);
  if (state.drawerId === Number(id)) unloadMedia($("#gv-drawer"));
}

/* ---------- state ---------- */
const state = {
  view: "library",                 // inbox | library | favorites | trash
  q: "",
  filters: { project: null, sub_project: null, kind: null, media_type: null,
             character: null, lora: null, upload: null, sort: "added" },
  assets: [], facets: null, counts: { inbox: 0, library: 0, trash: 0 },
  values: { projects: [], sub_projects: [], characters: [], tags: [], kinds: [] },
  config: { version: "", platforms: [], kinds: [], library_root: "" },
  selection: new Set(),
  drawerId: null,
  focusId: null,                   // card under the mouse — target of shortcuts
  bulk: { project: "", sub_project: "", kind: "", character: "", tags: "" },
  blurAll: localStorage.getItem("gv-blur") === "1",   // privacy mode, persisted
  inboxFilter: null,               // media_type filter for the inbox view
  collapsed: new Set(JSON.parse(localStorage.getItem("gv-collapsed") || "[]")),
  renderJobs: [],
  release: {
    clips: [],                     // [{id, name, thumb, duration}] in concat order
    mode: "concat",                // concat | each
    wm: { enabled: false, position: "br", size: 15, size_unit: "%",
          opacity: 80, offset_x: 3, offset_y: 3, offset_unit: "%" },
    out: { type: "project", project: "", sub_project: "", kind: "release", folder: "" },
    quality: "standard",
    audio: "keep",                 // keep | silent (replace sound with a silent track)
    check: null, wmInfo: null, previewUrl: null,
  },
  bulkLib: { project: "", sub_project: "", kind: "" },   // library bulk move
};

const assetById = (id) => state.assets.find((a) => a.id === Number(id));

/* ---------- combobox (shared floating panel for .gv-combo inputs) ----------
   Native <datalist> can't honor recency sort or independent widths, so
   autocomplete fields use this instead (the gv-proj pattern from v1). */
let comboActive = null;
function comboOptions(input) {
  const src = input.dataset.src;
  if (src === "projects") return state.values.projects;
  if (src === "characters") return state.values.characters;
  if (src === "sub_projects") {
    const scope = input.closest("[data-fields]");
    const projInput = scope && $('[data-f="project"]', scope);
    const proj = projInput ? projInput.value.trim() : "";
    const subs = state.values.sub_projects;
    const scoped = proj ? subs.filter((s) => s.project === proj) : subs;
    return [...new Set(scoped.map((s) => s.name))];
  }
  return [];
}
function comboPanel() {
  let el = $("#gv-combopanel");
  if (!el) {
    el = document.createElement("div");
    el.id = "gv-combopanel";
    el.className = "hidden bg-zinc-800 border border-zinc-600 rounded-lg shadow-xl text-sm";
    // layout-critical styles inline, not via Tailwind-CDN classes that may be
    // generated a frame late
    Object.assign(el.style, { position: "fixed", zIndex: "80", overflowY: "auto" });
    document.body.appendChild(el);
    el.addEventListener("mousedown", (e) => {
      const item = e.target.closest(".gv-combo-item");
      if (!item || !comboActive) return;
      e.preventDefault();                        // keep focus; avoid blur race
      comboActive.value = item.dataset.val;
      comboActive.dispatchEvent(new Event("input", { bubbles: true }));
      comboActive.dispatchEvent(new Event("change", { bubbles: true }));
      hideCombo();
    });
  }
  return el;
}
const COMBO_MAX_H = 256;
function positionCombo(input) {
  const el = comboPanel(), r = input.getBoundingClientRect();
  el.style.left = Math.min(r.left, window.innerWidth - 380) + "px";
  el.style.minWidth = Math.max(r.width, 220) + "px";
  el.style.maxWidth = "min(460px, 94vw)";
  // Open upward when there is not enough room below (e.g. the bulk bar).
  // Upward panels are anchored by their BOTTOM edge to the input, so they
  // stay docked no matter how tall they end up — measuring the height first
  // raced Tailwind-CDN's late class generation (max-h-64 not yet applied ->
  // full height measured -> panel left floating far above the bar).
  // clientHeight = the box `position: fixed; bottom` is measured against
  const vh = document.documentElement.clientHeight || window.innerHeight || 800;
  const h = Math.min(el.scrollHeight, COMBO_MAX_H);
  const below = vh - r.bottom - 12, above = r.top - 12;
  if (below >= h || below >= above) {
    el.style.top = r.bottom + 4 + "px";
    el.style.bottom = "auto";
    el.style.maxHeight = Math.max(80, Math.min(COMBO_MAX_H, below)) + "px";
  } else {
    el.style.top = "auto";
    el.style.bottom = vh - r.top + 4 + "px";
    el.style.maxHeight = Math.max(80, Math.min(COMBO_MAX_H, above)) + "px";
  }
}
function renderCombo(input) {
  const el = comboPanel(), f = input.value.trim().toLowerCase();
  const opts = comboOptions(input).filter((p) => p.toLowerCase().includes(f));
  if (!opts.length) { el.classList.add("hidden"); return; }
  el.innerHTML = opts.slice(0, 60).map((p) =>
    `<div class="gv-combo-item px-3 py-1.5 cursor-pointer truncate hover:bg-indigo-600" title="${esc(p)}" data-val="${esc(p)}">${esc(p)}</div>`).join("");
  el.classList.remove("hidden");
  positionCombo(input);
}
function hideCombo() { comboPanel().classList.add("hidden"); comboActive = null; }
function initCombo() {
  const isCombo = (t) => t && t.classList && t.classList.contains("gv-combo");
  document.addEventListener("focusin", (e) => {
    if (isCombo(e.target)) { comboActive = e.target; positionCombo(e.target); renderCombo(e.target); }
  });
  document.addEventListener("input", (e) => {
    if (isCombo(e.target) && comboActive === e.target) renderCombo(e.target);
  });
  document.addEventListener("focusout", (e) => {
    if (isCombo(e.target)) setTimeout(() => { if (comboActive === e.target) hideCombo(); }, 150);
  });
  window.addEventListener("scroll", () => { if (comboActive) positionCombo(comboActive); }, true);
  window.addEventListener("resize", () => { if (comboActive) positionCombo(comboActive); });
}

/* ---------- data loading ---------- */
function buildQuery() {
  const p = new URLSearchParams();
  const status = state.view === "inbox" ? "inbox" : state.view === "trash" ? "trash" : "library";
  p.set("status", status);
  if (state.q && (state.view === "library" || state.view === "favorites")) p.set("q", state.q);
  if (state.view === "favorites") p.set("favorite", "1");
  if (state.view === "library" || state.view === "favorites") {
    for (const k of ["project", "sub_project", "kind", "media_type", "character", "lora", "upload"])
      if (state.filters[k]) p.set(k, state.filters[k]);
    p.set("sort", state.filters.sort || "added");
  }
  return p.toString();
}

async function refresh() {
  try {
    const [data, values] = await Promise.all([
      api("/api/assets?" + buildQuery()),
      api("/api/values"),
    ]);
    // carry over unsaved inbox edits so filing one item doesn't reset the rest
    const prev = new Map(state.assets.map((a) => [a.id, a]));
    for (const a of data.assets) {
      const p = prev.get(a.id);
      if (p && p._f) a._f = p._f;
    }
    state.assets = data.assets;
    state.facets = data.facets;
    state.counts = data.counts;
    state.values = values;
    for (const id of [...state.selection])
      if (!state.assets.some((a) => a.id === id)) state.selection.delete(id);
    renderMain();
    updateTopbar();
    if (state.drawerId != null && !assetById(state.drawerId)) closeDrawer();
    else if (state.drawerId != null) renderDrawer();
  } catch (e) {
    toast("Loading failed: " + e.message, true);
  }
}

/* ---------- shell ---------- */
function renderShell() {
  $("#app").innerHTML = `
  <header class="sticky top-0 z-40 bg-zinc-950/95 backdrop-blur border-b border-zinc-800">
    <div class="flex items-center gap-4 px-4 py-2.5">
      <div class="flex items-center gap-2">
        <span class="text-lg font-bold tracking-tight text-indigo-400">gen<span class="text-zinc-100">vault</span></span>
        <span class="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 border border-zinc-700">v${esc(state.config.version)}</span>
      </div>
      <nav class="flex items-center gap-1 text-sm">
        ${["inbox|Inbox", "library|Library", "favorites|Favorites", "release|Release", "trash|Trash"].map((t) => {
          const [key, label] = t.split("|");
          return `<button data-nav="${key}" class="gv-tab px-3 py-1.5 rounded-lg hover:bg-zinc-800">${label}<span data-badge="${key}" class="ml-1.5 text-xs text-zinc-500"></span></button>`;
        }).join("")}
      </nav>
      <div class="flex-1"></div>
      <input id="gv-search" type="search" placeholder="Search… (prompt, tags, project, LoRA, file)"
        class="w-72 bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm placeholder-zinc-600">
      <button id="gv-blurall"
        class="px-3 py-1.5 text-sm rounded-lg border border-zinc-700 bg-zinc-900 hover:bg-zinc-800 text-zinc-300"></button>
      <button id="gv-backup" title="Backup: zip sidecars + database"
        class="px-3 py-1.5 text-sm rounded-lg border border-zinc-700 bg-zinc-900 hover:bg-zinc-800 text-zinc-300">Backup</button>
    </div>
  </header>
  <main id="gv-main" class="p-4"></main>
  <div id="gv-bulkbar"></div>
  <aside id="gv-drawer" class="fixed top-0 right-0 h-full w-[500px] max-w-[96vw] z-50 bg-zinc-900 border-l border-zinc-700 shadow-2xl overflow-y-auto translate-x-full"></aside>`;

  $("#gv-search").addEventListener("input", (e) => {
    clearTimeout(state._qTimer);
    state._qTimer = setTimeout(() => { state.q = e.target.value.trim(); refresh(); }, 250);
  });
  $("#gv-blurall").addEventListener("click", () => {
    state.blurAll = !state.blurAll;
    localStorage.setItem("gv-blur", state.blurAll ? "1" : "0");
    applyBlurAll();
  });
  applyBlurAll();
  $("#gv-backup").addEventListener("click", async () => {
    try {
      const r = await jpost("/api/backup");
      toast("Backup created: " + r.file.split(/[\\/]/).pop());
    } catch (e) { toast("Backup failed: " + e.message, true); }
  });
  $$(".gv-tab").forEach((b) => b.addEventListener("click", () => setView(b.dataset.nav)));
  updateTopbar();
}

/* Privacy mode: one body class blurs every thumbnail/preview via CSS
   (see index.html), so it applies instantly to current AND future media. */
function applyBlurAll() {
  document.body.classList.toggle("gv-blur", state.blurAll);
  const b = $("#gv-blurall");
  if (!b) return;
  b.textContent = state.blurAll ? "🙈 Blurred" : "👁 Blur";
  b.title = state.blurAll ? "Privacy mode is ON — click to show previews" : "Blur all previews (privacy mode)";
  b.classList.toggle("bg-indigo-600", state.blurAll);
  b.classList.toggle("border-indigo-500", state.blurAll);
  b.classList.toggle("text-white", state.blurAll);
  b.classList.toggle("bg-zinc-900", !state.blurAll);
}

function updateTopbar() {
  const activeJobs = state.renderJobs.filter((j) => j.status === "queued" || j.status === "running").length;
  const badges = {
    inbox: state.counts.inbox,
    library: state.counts.library,
    favorites: state.facets ? state.facets.favorites : "",
    release: activeJobs || "",
    trash: state.counts.trash,
  };
  $$(".gv-tab").forEach((b) => {
    const active = b.dataset.nav === state.view;
    b.classList.toggle("bg-indigo-600", active);
    b.classList.toggle("text-white", active);
    b.classList.toggle("hover:bg-zinc-800", !active);
    const badge = $(`[data-badge="${b.dataset.nav}"]`, b);
    if (badge) badge.textContent = badges[b.dataset.nav] !== "" && badges[b.dataset.nav] != null ? `(${badges[b.dataset.nav]})` : "";
    if (badge) badge.classList.toggle("text-indigo-200", active);
  });
  $("#gv-search").style.visibility = (state.view === "library" || state.view === "favorites") ? "visible" : "hidden";
}

function setView(view) {
  state.view = view;
  state.selection.clear();
  closeDrawer();
  updateTopbar();
  refresh();
  if (view === "release") loadReleaseData();
}

/* ---------- shared card pieces ---------- */
function starsHtml(a, cls = "text-base") {
  let s = "";
  for (let i = 1; i <= 5; i++)
    s += `<span class="gv-star ${cls} ${i <= (a.rating || 0) ? "text-amber-400" : "text-zinc-600 hover:text-zinc-400"}" data-action="rate" data-id="${a.id}" data-val="${i}">★</span>`;
  return `<span class="whitespace-nowrap">${s}</span>`;
}
function favHtml(a, cls = "text-base") {
  return `<span class="gv-star ${cls} ${a.favorite ? "text-rose-400" : "text-zinc-600 hover:text-zinc-400"}" data-action="fav" data-id="${a.id}" title="Favorite (F)">♥</span>`;
}
function mediaIcon(t) { return t === "video" ? "🎬" : t === "image" ? "🖼️" : "🎵"; }

function thumbHtml(a, aspect = "aspect-video") {
  const inner = a.thumb_url
    ? `<img src="${esc(a.thumb_url)}" loading="lazy" class="absolute inset-0 w-full h-full object-cover">`
    : `<div class="absolute inset-0 flex items-center justify-center text-4xl opacity-40">${mediaIcon(a.media_type)}</div>`;
  const dur = (a.meta && a.meta.duration)
    ? `<span class="absolute bottom-1 right-1 text-[10px] px-1 py-0.5 rounded bg-black/70 text-zinc-200">${fmtDur(a.meta.duration)}</span>` : "";
  const postedOn = Object.entries(a.uploads || {}).filter(([, u]) => u && u.posted).map(([p]) => p);
  const postedBadges = postedOn.length
    ? `<span class="absolute top-1 right-1 flex gap-0.5 flex-wrap justify-end max-w-[70%]">${postedOn.map((p) =>
        `<span class="text-[9px] px-1 py-px rounded bg-emerald-900/90 text-emerald-200 border border-emerald-700/80" title="Posted on ${esc(p)}">${esc(p)} ✓</span>`).join("")}</span>` : "";
  const hoverData = a.media_type === "video" ? `data-video="${esc(a.media_url)}"` :
                    a.media_type === "audio" ? `data-audio="${esc(a.media_url)}"` : "";
  const audioBtn = a.media_type === "audio"
    ? `<button data-action="audio-toggle" data-id="${a.id}" class="absolute inset-0 m-auto w-10 h-10 rounded-full bg-black/60 text-white text-lg opacity-0 group-hover:opacity-100">▶</button>` : "";
  return `<div class="gv-thumb relative ${aspect} bg-zinc-950 cursor-pointer overflow-hidden" data-action="open-drawer" data-id="${a.id}" ${hoverData}>
    ${inner}${dur}${audioBtn}${postedBadges}
    <span class="absolute top-1 left-1 text-[11px] opacity-70">${mediaIcon(a.media_type)}</span>
  </div>`;
}

/* ---------- library view ---------- */
function facetSection(title, items, facetKey, activeVal, renameAction = null) {
  if (!items || !items.length) return "";
  const isCollapsed = state.collapsed.has(facetKey);
  return `<div class="mb-4">
    <button data-collapse="${facetKey}" class="w-full flex items-center justify-between text-[11px] uppercase tracking-wider text-zinc-500 mb-1.5 font-semibold hover:text-zinc-300">
      <span>${esc(title)}</span><span class="text-[10px]">${isCollapsed ? "▸" : "▾"}</span>
    </button>
    ${isCollapsed ? "" : items.map((it) => {
      const active = activeVal === it.value;
      const row = `<button data-facet="${facetKey}" data-val="${esc(it.value)}"
        class="w-full min-w-0 flex justify-between items-center gap-2 text-left px-2 py-1 rounded text-sm ${active ? "bg-indigo-600 text-white" : "hover:bg-zinc-800 text-zinc-300"}">
        <span class="truncate" title="${esc(it.label)}">${esc(it.label)}</span>
        <span class="text-xs ${active ? "text-indigo-200" : "text-zinc-500"}">${it.count}</span></button>`;
      if (!renameAction) return row;
      return `<div class="gv-facet-row flex items-center gap-0.5">${row}
        <button data-action="${renameAction}" data-val="${esc(it.value)}" title="Rename"
          class="gv-facet-edit shrink-0 w-6 py-1 rounded text-xs text-zinc-500 hover:text-zinc-100 hover:bg-zinc-800">✎</button></div>`;
    }).join("")}
  </div>`;
}

function renderSidebar() {
  const f = state.facets, fl = state.filters;
  if (!f) return "";
  const mk = (arr) => (arr || []).map((x) => ({ value: x.name, label: x.name, count: x.count }));
  const subs = fl.project
    ? f.sub_projects.filter((s) => s.project === fl.project).map((s) => ({ value: s.name, label: s.name, count: s.count }))
    : [];
  const uploads = [];
  for (const p of state.config.platforms) {
    const u = f.uploads[p] || { yes: 0, no: 0 };
    uploads.push({ value: `${p}:yes`, label: `${p} · posted`, count: u.yes });
    uploads.push({ value: `${p}:no`, label: `${p} · not posted`, count: u.no });
  }
  const anyFilter = ["project", "sub_project", "kind", "media_type", "character", "lora", "upload"].some((k) => fl[k]);
  return `<aside id="gv-sidebar" class="w-60 shrink-0 pr-4 border-r border-zinc-800/70 sticky top-14 self-start max-h-[calc(100vh-4.5rem)] overflow-y-auto">
    ${anyFilter ? `<button id="gv-clearfilters" class="mb-3 w-full text-sm px-2 py-1.5 rounded-lg border border-zinc-700 text-zinc-300 hover:bg-zinc-800">✕ Clear filters</button>` : ""}
    ${facetSection("Project", mk(f.projects), "project", fl.project, "rename-project")}
    ${fl.project ? facetSection("Sub-project", subs, "sub_project", fl.sub_project) : ""}
    ${facetSection("Kind", mk(f.kinds), "kind", fl.kind)}
    ${facetSection("Media type", mk(f.media_types), "media_type", fl.media_type)}
    ${facetSection("Character", mk(f.characters), "character", fl.character)}
    ${facetSection("LoRA", mk(f.loras).slice(0, 30), "lora", fl.lora)}
    ${facetSection("Upload status", uploads, "upload", fl.upload)}
  </aside>`;
}

function libraryCard(a) {
  const checked = state.selection.has(a.id);
  const selectBox = `<label class="absolute top-2 left-2 z-10 cursor-pointer ${checked ? "" : "opacity-0 group-hover:opacity-100"}" title="Select">
        <input type="checkbox" data-action="select" data-id="${a.id}" ${checked ? "checked" : ""} class="w-4 h-4 accent-indigo-500"></label>`;
  return `<div class="gv-card group relative bg-zinc-900 rounded-xl overflow-hidden border ${checked ? "border-indigo-500" : "border-zinc-800 hover:border-zinc-600"}" tabindex="0" data-id="${a.id}">
    ${selectBox}
    ${thumbHtml(a)}
    <div class="px-2.5 py-2">
      <div class="text-xs text-zinc-300 truncate" title="${esc(a.original_name)}">${esc(a.original_name)}</div>
      <div class="flex gap-1 mt-1 flex-wrap">
        ${a.project ? `<span class="text-[10px] px-1.5 py-0.5 rounded bg-indigo-950 text-indigo-300 border border-indigo-900 truncate max-w-[9rem]" title="${esc(a.project)}${a.sub_project ? " / " + esc(a.sub_project) : ""}">${esc(a.project)}${a.sub_project ? " / " + esc(a.sub_project) : ""}</span>` : ""}
        ${a.kind ? `<span class="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 border border-zinc-700">${esc(a.kind)}</span>` : ""}
      </div>
      <div class="flex items-center justify-between mt-1.5">
        ${starsHtml(a, "text-sm")}
        ${favHtml(a, "text-sm")}
      </div>
    </div>
  </div>`;
}

function renderLibrary() {
  const sortSel = `<select id="gv-sort" class="bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-1.5 text-sm text-zinc-300">
      ${[["added", "Recently added"], ["created", "Created (newest)"], ["rating", "Top rated"], ["name", "Name"]]
        .map(([v, l]) => `<option value="${v}" ${state.filters.sort === v ? "selected" : ""}>${l}</option>`).join("")}
    </select>`;
  const grid = state.assets.length
    ? `<div id="gv-grid" class="grid gap-3 ${state.selection.size ? "pb-24" : ""}" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr))">${state.assets.map(libraryCard).join("")}</div>`
    : `<div class="text-zinc-500 text-sm py-16 text-center">No files found.${state.q || Object.values(state.filters).some((v) => v && v !== "added") ? " Adjust search/filters?" : ""}</div>`;
  return `<div class="flex gap-4">
    ${renderSidebar()}
    <section class="flex-1 min-w-0">
      <div class="flex items-center gap-3 mb-3">
        <label class="flex items-center gap-2 text-sm text-zinc-400 cursor-pointer">
          <input type="checkbox" id="gv-lib-selectall" ${state.assets.length && state.assets.every((a) => state.selection.has(a.id)) ? "checked" : ""} class="w-4 h-4 accent-indigo-500"> Select all
        </label>
        <span class="text-sm text-zinc-500">${state.assets.length} ${state.assets.length === 1 ? "file" : "files"}${state.selection.size ? ` · ${state.selection.size} selected` : ""}</span>
        <div class="flex-1"></div>${sortSel}
      </div>
      ${grid}
    </section>
  </div>`;
}

/* ---------- inbox view ---------- */
function fieldInputs(prefix, vals, compact = false) {
  const w = compact ? "w-40" : "w-full";
  const base = `bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100 placeholder-zinc-500`;
  return `
    <input ${prefix}="project" value="${esc(vals.project || "")}" placeholder="Project *" class="gv-combo ${base} ${w}" data-src="projects" autocomplete="off">
    <input ${prefix}="sub_project" value="${esc(vals.sub_project || "")}" placeholder="Sub-project" class="gv-combo ${base} ${w}" data-src="sub_projects" autocomplete="off">
    <select ${prefix}="kind" class="${base} ${compact ? "w-32" : "w-full"}">
      <option value="">Select kind *</option>
      ${state.values.kinds.map((k) => `<option value="${esc(k)}" ${vals.kind === k ? "selected" : ""}>${esc(k)}</option>`).join("")}
    </select>
    <input ${prefix}="character" value="${esc(vals.character || "")}" placeholder="Character" class="gv-combo ${base} ${w}" data-src="characters" autocomplete="off">
    <input ${prefix}="tags" value="${esc(vals.tags || "")}" placeholder="Tags (comma-separated)" class="${base} ${w}">`;
}

function inboxCard(a) {
  const m = a.meta || {};
  const f = a._f || {};
  const checked = state.selection.has(a.id);
  const res = m.width ? `${m.width}×${m.height}` : "";
  return `<div class="gv-card gv-inbox-card relative bg-zinc-900 rounded-xl border ${checked ? "border-indigo-500" : "border-zinc-800"} overflow-hidden flex flex-col" data-id="${a.id}" data-fields tabindex="0">
    <label class="absolute top-2 left-2 z-10 cursor-pointer">
      <input type="checkbox" data-action="select" data-id="${a.id}" ${checked ? "checked" : ""} class="w-4 h-4 accent-indigo-500">
    </label>
    ${thumbHtml(a)}
    <div class="p-2.5 flex flex-col gap-1.5 flex-1">
      <div class="text-xs text-zinc-300 truncate font-medium" title="${esc(a.original_name)}">${esc(a.original_name)}</div>
      <div class="text-[11px] text-zinc-500 flex gap-2 flex-wrap">
        ${res ? `<span>${res}</span>` : ""}${m.duration ? `<span>${fmtDur(m.duration)}</span>` : ""}<span>${fmtSize(m.filesize)}</span>
        ${m.model ? `<span class="truncate max-w-[12rem]" title="${esc(m.model)}">${esc(m.model.split(/[\\/]/).pop())}</span>` : ""}
      </div>
      ${m.positive_prompt ? `<div class="text-[11px] text-zinc-500 gv-clamp2" title="${esc(m.positive_prompt)}">${esc(m.positive_prompt)}</div>` : ""}
      ${a.source_hint && a.source_hint !== "browser-upload" ? `<div class="text-[10px] text-zinc-600">Hint (drop folder): ${esc(a.source_hint)} — never applied automatically</div>` : ""}
      <div class="grid grid-cols-2 gap-1.5 mt-auto pt-1">${fieldInputs("data-f", f)}</div>
      <button data-action="file-one" data-id="${a.id}"
        class="mt-1.5 w-full py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium">Add to library</button>
    </div>
  </div>`;
}

function inboxVisible() {
  return state.inboxFilter
    ? state.assets.filter((a) => a.media_type === state.inboxFilter)
    : state.assets;
}

function renderInbox() {
  if (!state.assets.length)
    return `<div class="text-zinc-500 text-sm py-16 text-center">
      Inbox is empty. Drag&nbsp;&amp;&nbsp;drop files into this window<br>
      or put them into the drop folder: <code class="text-zinc-400">${esc(state.config.library_root)}\\drop</code></div>`;
  const counts = { video: 0, image: 0, audio: 0 };
  state.assets.forEach((a) => { counts[a.media_type] = (counts[a.media_type] || 0) + 1; });
  const chips = [[null, `All (${state.assets.length})`], ["video", `Videos (${counts.video})`],
                 ["image", `Images (${counts.image})`], ["audio", `Audio (${counts.audio})`]]
    .map(([val, label]) => `<button data-inbox-filter="${val ?? ""}"
      class="px-2.5 py-1 rounded-lg text-sm border ${state.inboxFilter === val ? "bg-indigo-600 border-indigo-500 text-white" : "border-zinc-700 text-zinc-400 hover:bg-zinc-800"}">${label}</button>`)
    .join("");
  const visible = inboxVisible();
  const allSelected = visible.length && visible.every((a) => state.selection.has(a.id));
  return `<div>
    <div class="flex items-center gap-3 mb-3 flex-wrap">
      <div class="flex gap-1.5">${chips}</div>
      <label class="flex items-center gap-2 text-sm text-zinc-400 cursor-pointer">
        <input type="checkbox" id="gv-selectall" ${allSelected ? "checked" : ""} class="w-4 h-4 accent-indigo-500"> Select all${state.inboxFilter ? " (filtered)" : ""}
      </label>
      <span class="text-sm text-zinc-500">${visible.length} shown${state.selection.size ? ` · ${state.selection.size} selected` : ""}</span>
    </div>
    <div id="gv-grid" class="grid gap-3 ${state.selection.size ? "pb-24" : ""}" style="grid-template-columns:repeat(auto-fill,minmax(300px,1fr))">
      ${visible.map(inboxCard).join("")}
    </div>
  </div>`;
}

function renderBulkbar() {
  const el = $("#gv-bulkbar");
  if ((state.view === "library" || state.view === "favorites") && state.selection.size) {
    const vids = state.assets.filter((a) => state.selection.has(a.id) && a.media_type === "video").length;
    const b = state.bulkLib;
    const base = "bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100 placeholder-zinc-500";
    el.innerHTML = `<div class="fixed bottom-0 inset-x-0 z-40 bg-zinc-900/95 backdrop-blur border-t border-zinc-700 px-4 py-3">
      <div class="flex items-center gap-2 flex-wrap" data-fields>
        <span class="text-sm text-zinc-300 font-medium mr-1">${state.selection.size} selected:</span>
        <input data-f="project" data-bulklib="project" value="${esc(b.project)}" placeholder="Project (keep)" class="gv-combo ${base} w-40" data-src="projects" autocomplete="off">
        <input data-f="sub_project" data-bulklib="sub_project" value="${esc(b.sub_project)}" placeholder="Sub-project (keep)" class="gv-combo ${base} w-40" data-src="sub_projects" autocomplete="off">
        <select data-bulklib="kind" class="${base} w-32">
          <option value="">Kind: keep</option>
          ${state.values.kinds.map((k) => `<option value="${esc(k)}" ${b.kind === k ? "selected" : ""}>${esc(k)}</option>`).join("")}
        </select>
        <button id="gv-bulk-move" class="px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium">Move (${state.selection.size})</button>
        ${vids ? `<button id="gv-make-release" class="px-4 py-1.5 rounded-lg bg-zinc-700 hover:bg-zinc-600 text-zinc-100 text-sm font-medium">Create release (${vids})</button>` : ""}
        <button data-action="bulk-trash" class="px-3 py-1.5 rounded-lg bg-red-950 border border-red-900 text-red-300 hover:bg-red-900 text-sm">Delete (${state.selection.size})</button>
        <button id="gv-bulk-clear" class="px-3 py-1.5 rounded-lg border border-zinc-700 text-zinc-300 text-sm hover:bg-zinc-800">Clear selection</button>
      </div></div>`;
    return;
  }
  if (state.view === "trash" && state.selection.size) {
    el.innerHTML = `<div class="fixed bottom-0 inset-x-0 z-40 bg-zinc-900/95 backdrop-blur border-t border-zinc-700 px-4 py-3">
      <div class="flex items-center gap-2 flex-wrap">
        <span class="text-sm text-zinc-300 font-medium mr-1">${state.selection.size} selected:</span>
        <button data-action="bulk-restore" class="px-4 py-1.5 rounded-lg bg-zinc-700 hover:bg-zinc-600 text-zinc-100 text-sm font-medium">Restore (${state.selection.size})</button>
        <button data-action="bulk-purge" class="px-4 py-1.5 rounded-lg bg-red-900/70 hover:bg-red-800 text-red-200 text-sm font-medium">Delete forever (${state.selection.size})</button>
        <button id="gv-bulk-clear" class="px-3 py-1.5 rounded-lg border border-zinc-700 text-zinc-300 text-sm hover:bg-zinc-800">Clear selection</button>
      </div></div>`;
    return;
  }
  if (state.view !== "inbox" || !state.selection.size) { el.innerHTML = ""; return; }
  el.innerHTML = `<div class="fixed bottom-0 inset-x-0 z-40 bg-zinc-900/95 backdrop-blur border-t border-zinc-700 px-4 py-3">
    <div class="flex items-center gap-2 flex-wrap" data-fields>
      <span class="text-sm text-zinc-300 font-medium mr-1">${state.selection.size} selected:</span>
      ${fieldInputs("data-bulk", state.bulk, true)}
      <button id="gv-bulk-file" class="px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium">Add to library (${state.selection.size})</button>
      <button data-action="bulk-trash" class="px-3 py-1.5 rounded-lg bg-red-950 border border-red-900 text-red-300 hover:bg-red-900 text-sm">Delete (${state.selection.size})</button>
      <button id="gv-bulk-clear" class="px-3 py-1.5 rounded-lg border border-zinc-700 text-zinc-300 text-sm hover:bg-zinc-800">Clear selection</button>
    </div>
  </div>`;
}

/* ---------- trash view ---------- */
function renderTrash() {
  if (!state.assets.length)
    return `<div class="text-zinc-500 text-sm py-16 text-center">Trash is empty.</div>`;
  const allSelected = state.assets.every((a) => state.selection.has(a.id));
  return `<div class="flex items-center gap-3 mb-3">
      <label class="flex items-center gap-2 text-sm text-zinc-400 cursor-pointer">
        <input type="checkbox" id="gv-trash-selectall" ${allSelected ? "checked" : ""} class="w-4 h-4 accent-indigo-500"> Select all
      </label>
      <span class="text-sm text-zinc-500">${state.assets.length} in trash${state.selection.size ? ` · ${state.selection.size} selected` : ""}</span>
    </div>
    <div id="gv-grid" class="grid gap-3 ${state.selection.size ? "pb-24" : ""}" style="grid-template-columns:repeat(auto-fill,minmax(260px,1fr))">
    ${state.assets.map((a) => {
      const ti = a.trash_info || {};
      const checked = state.selection.has(a.id);
      return `<div class="gv-card relative bg-zinc-900 rounded-xl border ${checked ? "border-indigo-500" : "border-zinc-800"} overflow-hidden" data-id="${a.id}">
        <label class="absolute top-2 left-2 z-10 cursor-pointer" title="Select">
          <input type="checkbox" data-action="select" data-id="${a.id}" ${checked ? "checked" : ""} class="w-4 h-4 accent-indigo-500"></label>
        ${thumbHtml(a)}
        <div class="p-2.5 text-xs space-y-1">
          <div class="text-zinc-300 truncate" title="${esc(a.original_name)}">${esc(a.original_name)}</div>
          <div class="text-zinc-500 truncate" title="${esc(ti.orig_rel_path || "")}">was: ${esc(ti.orig_rel_path || "?")}</div>
          <div class="text-zinc-600">deleted: ${fmtDate(ti.deleted_at)}</div>
          <div class="flex gap-2 pt-1">
            <button data-action="restore" data-id="${a.id}" class="flex-1 py-1 rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-100">Restore</button>
            <button data-action="purge" data-id="${a.id}" class="flex-1 py-1 rounded bg-red-900/70 hover:bg-red-800 text-red-200">Delete forever</button>
          </div>
        </div>
      </div>`;
    }).join("")}
  </div>`;
}

/* ---------- release view (watermark + concat) ---------- */
async function loadReleaseData() {
  try { state.release.wmInfo = await api("/api/render/watermark"); } catch {}
  if (state.release.clips.length) {
    try {
      state.release.check = await jpost("/api/render/check", { clip_ids: state.release.clips.map((c) => c.id) });
    } catch (e) { state.release.check = { error: e.message }; }
  } else state.release.check = null;
  await pollJobs();
  if (state.view === "release") renderMain();
}

async function pollJobs() {
  try {
    const r = await api("/api/render/jobs");
    state.renderJobs = r.jobs;
    const el = $("#gv-render-jobs");
    if (el) el.innerHTML = renderJobsHtml();
    updateTopbar();
  } catch {}
}

function renderJobsHtml() {
  if (!state.renderJobs.length) return `<div class="text-zinc-600 text-sm">No render jobs yet.</div>`;
  const colors = { queued: "text-zinc-400 border-zinc-700", running: "text-indigo-300 border-indigo-700",
                   done: "text-emerald-300 border-emerald-800", error: "text-red-300 border-red-800",
                   cancelled: "text-zinc-500 border-zinc-700" };
  return state.renderJobs.map((j) => {
    const names = (j.clip_names || []).join(", ");
    const bar = j.status === "running"
      ? `<div class="h-1.5 bg-zinc-800 rounded mt-1.5"><div class="h-1.5 bg-indigo-500 rounded" style="width:${j.progress}%"></div></div>` : "";
    const results = (j.results || []).map((res) => res.asset_id != null
      ? `<button data-action="job-open-asset" data-id="${res.asset_id}" class="text-indigo-400 hover:underline">Open asset #${res.asset_id}</button>`
      : `<span class="text-zinc-400 break-all">${esc(res.path)}</span> <button data-action="copy-path" data-path="${esc(res.path)}" class="text-indigo-400 hover:underline">Copy path</button>`
    ).join("<br>");
    const actions = (j.status === "queued" || j.status === "running")
      ? `<button data-action="job-cancel" data-job="${j.id}" class="text-xs px-2 py-0.5 rounded border border-zinc-700 hover:bg-zinc-800 text-zinc-400">Cancel</button>`
      : `<button data-action="job-remove" data-job="${j.id}" class="text-xs px-2 py-0.5 rounded border border-zinc-700 hover:bg-zinc-800 text-zinc-500" title="Remove from list">✕</button>`;
    return `<div class="bg-zinc-900 border ${colors[j.status] || "border-zinc-700"} rounded-xl px-3 py-2 text-sm">
      <div class="flex items-center gap-2">
        <span class="font-medium ${colors[j.status] || ""}">${j.status}${j.status === "running" ? ` ${j.progress}%` : ""}</span>
        <span class="text-zinc-500 text-xs truncate flex-1" title="${esc(names)}">${j.clips.length} clip(s): ${esc(names)}</span>
        ${actions}
      </div>
      ${bar}
      ${j.note ? `<div class="text-[11px] text-zinc-500 mt-1">${esc(j.note)}</div>` : ""}
      ${j.error ? `<div class="text-[11px] text-red-400 mt-1 break-all">${esc(j.error)}</div>` : ""}
      ${results ? `<div class="text-xs mt-1">${results}</div>` : ""}
    </div>`;
  }).join("");
}

/* Release clip lengths: probed duration when available, trims applied. */
function clipDuration(c) {
  const chk = state.release.check;
  const probed = chk && !chk.error && (chk.clips || []).find((x) => x.id === c.id);
  return (probed && probed.duration) || c.duration || 0;
}
const clipTrimmed = (c) => (c.trimStart || 0) > 0 || (c.trimEnd || 0) > 0;
const clipEffective = (c) => Math.max(0, clipDuration(c) - (c.trimStart || 0) - (c.trimEnd || 0));
const fmtSec = (s) => (s >= 60 ? fmtDur(s) : `${(Math.round(s * 10) / 10).toFixed(1)}s`);
function clipLenHtml(c) {
  return clipTrimmed(c)
    ? `<span class="text-indigo-300">${fmtSec(clipEffective(c))}</span> <span class="text-zinc-600 line-through">${fmtSec(clipDuration(c))}</span>`
    : fmtSec(clipDuration(c));
}

/* ---------- trim editor: play a release clip and set the cuts visually ---------- */
const trimEditorClip = () => {
  const r = state.release;
  return r.editId != null ? r.clips.find((c) => c.id === r.editId) || null : null;
};
const clipUrl = (c) => (assetById(c.id) || {}).media_url || c.url;
function clipFps(c) {
  const chk = state.release.check;
  const probed = chk && !chk.error && (chk.clips || []).find((x) => x.id === c.id);
  return (probed && probed.fps) || 24;
}

function trimEditorHtml() {
  const c = trimEditorClip();
  if (!c) return "";
  const btn = "px-2.5 py-1 rounded-lg border border-zinc-700 text-zinc-200 hover:bg-zinc-800";
  return `<div id="gv-trim-editor" class="mt-3 bg-zinc-900 border border-indigo-800/70 rounded-xl p-3 space-y-2">
    <div class="flex items-center gap-2">
      <span class="text-sm text-zinc-200 truncate flex-1" title="${esc(c.name)}">✂ ${esc(c.name)}</span>
      <button data-action="clip-edit-close" class="px-2 py-0.5 rounded hover:bg-zinc-800 text-zinc-400" title="Close preview">✕</button>
    </div>
    <video id="gv-trim-video" src="${esc(clipUrl(c))}" controls preload="auto" playsinline
      class="w-full max-h-80 bg-black rounded-lg"></video>
    <div id="gv-trim-bar" title="Click to jump" style="position:relative;height:18px;border-radius:6px;background:#27272a;cursor:pointer;overflow:hidden">
      <div id="gv-trim-keep" style="position:absolute;top:0;bottom:0;background:rgba(99,102,241,.55);border-left:2px solid #a5b4fc;border-right:2px solid #a5b4fc"></div>
      <div id="gv-trim-head" style="position:absolute;top:0;bottom:0;width:2px;background:#fff;pointer-events:none"></div>
    </div>
    <div class="flex items-center justify-between text-[11px] text-zinc-400">
      <span id="gv-trim-time">0.00s</span><span id="gv-trim-range"></span>
    </div>
    <div class="flex flex-wrap items-center gap-1.5 text-xs">
      <button data-action="trim-step" data-step="-1" class="${btn}" title="One frame back">◀ 1 frame</button>
      <button data-action="trim-step" data-step="1" class="${btn}" title="One frame forward">1 frame ▶</button>
      <span class="w-2"></span>
      <button data-action="trim-set-start" class="${btn}" title="Cut everything before the current position">⇤ Set start here</button>
      <button data-action="trim-set-end" class="${btn}" title="Cut everything after the current position">Set end here ⇥</button>
      <span class="w-2"></span>
      <button data-action="trim-play" class="px-2.5 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white">▶ Play trimmed</button>
      <button data-action="trim-reset" class="${btn}">Reset cuts</button>
    </div>
  </div>`;
}

/* Clip length incl. what the loaded video reports (most precise). */
function editorDuration(c) {
  const v = $("#gv-trim-video");
  return (v && isFinite(v.duration) && v.duration) || clipDuration(c) || 0;
}

/* Redraw the editor bar/labels from the clip's trims and the video position. */
function drawTrimBar() {
  const c = trimEditorClip(), v = $("#gv-trim-video");
  if (!c || !v) return;
  const dur = editorDuration(c);
  if (!dur) return;
  const start = Math.min(c.trimStart || 0, dur), end = Math.min(c.trimEnd || 0, dur);
  const keep = $("#gv-trim-keep"), head = $("#gv-trim-head");
  keep.style.left = (start / dur) * 100 + "%";
  keep.style.width = Math.max(0, (dur - start - end) / dur) * 100 + "%";
  head.style.left = `calc(${(v.currentTime / dur) * 100}% - 1px)`;
  $("#gv-trim-time").textContent = `${v.currentTime.toFixed(2)}s / ${dur.toFixed(2)}s`;
  $("#gv-trim-range").textContent = start || end
    ? `keeps ${start.toFixed(2)}s – ${(dur - end).toFixed(2)}s (${Math.max(0, dur - start - end).toFixed(2)}s)`
    : "no cuts — whole clip is kept";
}

/* Sync row inputs, lengths, total and hint after a trim changed. */
function refreshTrimUI(c) {
  const i = state.release.clips.indexOf(c);
  for (const which of ["start", "end"]) {
    const inp = $(`[data-trim="${which}"][data-idx="${i}"]`);
    const val = which === "start" ? c.trimStart : c.trimEnd;
    if (inp && document.activeElement !== inp) inp.value = val ? String(val) : "";
  }
  const len = $(`[data-cliplen="${i}"]`);
  if (len) len.innerHTML = clipLenHtml(c);
  const tot = $("#gv-release-total");
  if (tot) tot.textContent = `total ${fmtSec(state.release.clips.reduce((sum, x) => sum + clipEffective(x), 0))}`;
  const note = $("#gv-compat");
  if (note) note.innerHTML = releaseCompatNote();
  drawTrimBar();
}

function trimAction(action, el) {
  const c = trimEditorClip(), v = $("#gv-trim-video");
  if (!c || !v) return;
  const dur = editorDuration(c);
  // The frame on screen is always kept. Cuts are placed HALF a frame before
  // (start) / after (end) it: a boundary exactly on a frame would flip to the
  // neighbouring frame through rounding (1/24 s has no exact decimal form).
  const half = 0.5 / clipFps(c);
  const r3 = (x) => Math.max(0, Math.round(x * 1000) / 1000);
  if (action === "trim-step") {
    v.pause();
    state.release.rangePlay = false;
    v.currentTime = Math.min(dur, Math.max(0, v.currentTime + Number(el.dataset.step) / clipFps(c)));
  } else if (action === "trim-set-start") {
    const start = v.currentTime < half ? 0 : r3(v.currentTime - half);
    if (dur - start - (c.trimEnd || 0) < 0.2) { toast("The start has to be before the end cut", true); return; }
    c.trimStart = start;
    refreshTrimUI(c);
  } else if (action === "trim-set-end") {
    const end = dur - v.currentTime <= 3 * half ? 0 : r3(dur - v.currentTime - half);
    if (dur - (c.trimStart || 0) - end < 0.2) { toast("The end has to be after the start cut", true); return; }
    c.trimEnd = Math.max(0, end);
    refreshTrimUI(c);
  } else if (action === "trim-play") {
    state.release.rangePlay = true;
    v.currentTime = c.trimStart || 0;
    v.play().catch(() => {});
  } else if (action === "trim-reset") {
    c.trimStart = 0; c.trimEnd = 0;
    refreshTrimUI(c);
  }
}

/* Wire the freshly rendered editor video (called after every renderMain). */
function initTrimEditor() {
  const v = $("#gv-trim-video"), bar = $("#gv-trim-bar");
  if (!v || !bar) return;
  const r = state.release;
  v.addEventListener("loadedmetadata", () => {
    const c = trimEditorClip();
    if (c && r.editTime != null && r.editTimeId === c.id) v.currentTime = Math.min(r.editTime, v.duration || 0);
    else if (c) v.currentTime = c.trimStart || 0;
    drawTrimBar();
  });
  ["timeupdate", "seeked", "durationchange"].forEach((ev) => v.addEventListener(ev, () => {
    r.editTime = v.currentTime; r.editTimeId = r.editId;
    drawTrimBar();
  }));
  v.addEventListener("pause", () => { r.rangePlay = false; });
  v.addEventListener("play", () => {
    // smooth playhead + exact stop at the end cut while "Play trimmed" runs
    const tick = () => {
      if (v.paused || !document.body.contains(v)) return;
      const c = trimEditorClip();
      if (c && r.rangePlay) {
        const stopAt = editorDuration(c) - (c.trimEnd || 0);
        if (v.currentTime >= stopAt - 0.01) { v.pause(); v.currentTime = stopAt; r.rangePlay = false; }
      }
      drawTrimBar();
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  bar.addEventListener("click", (e) => {
    const c = trimEditorClip();
    if (!c) return;
    const rect = bar.getBoundingClientRect();
    v.currentTime = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) * editorDuration(c);
  });
}

/* Hint under the clip list: lossless vs. re-encode and why. Kept separate so
   typing a trim value can refresh it without re-rendering the form. */
function releaseCompatNote() {
  const r = state.release, chk = r.check;
  const multi = r.clips.length > 1;
  const anyTrim = r.clips.some(clipTrimmed);
  const mute = r.audio === "silent";
  let compatNote = "";
  if (chk && chk.error) compatNote = `<div class="text-xs text-red-400 mt-2">${esc(chk.error)}</div>`;
  else if (chk && multi && r.mode === "concat") {
    const match = mute ? chk.video_compatible : chk.compatible;
    const muteTxt = mute ? " The audio is replaced by silence." : "";
    if (!r.wm.enabled && !anyTrim && match)
      compatNote = `<div class="text-xs text-emerald-400 mt-2">⚡ Lossless stream copy possible — all clips share codec/resolution/fps.${muteTxt}</div>`;
    else
      compatNote = `<div class="text-xs text-amber-400 mt-2">Re-encode required (${!match ? "clips differ in codec/resolution/fps; " : ""}${r.wm.enabled ? "watermark overlay; " : ""}${anyTrim ? "trimmed — cuts are frame-accurate; " : ""}target ${chk.target.width}×${chk.target.height} @ ${chk.target.fps} fps, ${chk.nvenc ? "GPU NVENC" : "CPU x264/x265"}).${muteTxt}</div>`;
  } else if (chk && !chk.error && (!multi || r.mode === "each")) {
    if (!r.wm.enabled && !anyTrim && mute)
      compatNote = `<div class="text-xs text-emerald-400 mt-2">⚡ The video is copied losslessly — only the audio is replaced by silence.</div>`;
    else if (anyTrim)
      compatNote = `<div class="text-xs text-amber-400 mt-2">Trimmed clips are re-encoded so the cuts are frame-accurate.${mute ? " The audio is replaced by silence." : ""}</div>`;
  }
  return compatNote;
}

function renderRelease() {
  const r = state.release;
  const jobsBlock = `<div class="mt-6 max-w-3xl">
    <div class="text-[11px] uppercase tracking-wider text-zinc-500 font-semibold mb-2">Render queue</div>
    <div id="gv-render-jobs" class="space-y-2">${renderJobsHtml()}</div></div>`;

  if (!r.clips.length)
    return `<div class="max-w-3xl mx-auto">
      <div class="text-zinc-500 text-sm py-10 text-center">No clips selected.<br><br>
        Go to the <button data-nav-inline="library" class="text-indigo-400 hover:underline">Library</button>, tick one or more <b>video</b> cards
        (checkbox appears when hovering a card) and click "Create release".</div>
      ${jobsBlock}</div>`;

  const chk = r.check;
  const total = r.clips.reduce((sum, c) => sum + clipEffective(c), 0);
  const multi = r.clips.length > 1;
  const trimInput = (c, i, which) => `<input data-trim="${which}" data-idx="${i}" type="number" min="0" step="0.1"
        value="${(which === "start" ? c.trimStart : c.trimEnd) || ""}" placeholder="0"
        class="w-14 bg-zinc-800 border border-zinc-700 rounded px-1.5 py-0.5 text-xs text-zinc-100 placeholder-zinc-600">`;

  const clipRows = r.clips.map((c, i) => `
    <div class="gv-clip-row flex items-center gap-2 bg-zinc-900 border border-zinc-800 rounded-lg px-2 py-1.5 cursor-grab" draggable="true" data-idx="${i}">
      <span class="text-zinc-600 select-none">≡</span>
      <span class="text-zinc-500 text-xs w-5">${i + 1}.</span>
      ${c.thumb ? `<img src="${esc(c.thumb)}" class="w-14 h-8 object-cover rounded">` : `<span class="w-14 text-center">🎬</span>`}
      <span class="text-sm text-zinc-300 truncate flex-1" title="${esc(c.name)}">${esc(c.name)}</span>
      <label class="flex items-center gap-1 text-[11px] text-zinc-500" title="Seconds to cut from the beginning">cut start ${trimInput(c, i, "start")}</label>
      <label class="flex items-center gap-1 text-[11px] text-zinc-500" title="Seconds to cut from the end">end ${trimInput(c, i, "end")}</label>
      <span data-cliplen="${i}" class="text-xs text-zinc-500 w-24 text-right">${clipLenHtml(c)}</span>
      <button data-action="clip-edit" data-idx="${i}" title="Play the clip and set the cuts visually"
        class="px-1.5 py-0.5 rounded text-xs border ${r.editId === c.id ? "bg-indigo-600 border-indigo-500 text-white" : "border-zinc-700 text-zinc-300 hover:bg-zinc-800"}">✂ Preview</button>
      <span class="flex flex-col leading-none">
        <button data-action="clip-up" data-idx="${i}" class="text-zinc-500 hover:text-zinc-200 text-[10px] ${i === 0 ? "invisible" : ""}">▲</button>
        <button data-action="clip-down" data-idx="${i}" class="text-zinc-500 hover:text-zinc-200 text-[10px] ${i === r.clips.length - 1 ? "invisible" : ""}">▼</button>
      </span>
      <button data-action="clip-remove" data-idx="${i}" class="text-zinc-600 hover:text-red-400 px-1">✕</button>
    </div>`).join("");

  const wm = r.wm;
  const posGrid = [["tl", "tc", "tr"], ["ml", "c", "mr"], ["bl", "bc", "br"]].map((row) =>
    row.map((p) => `<button data-action="wm-pos" data-pos="${p}"
      class="w-7 h-7 rounded border ${wm.position === p ? "bg-indigo-600 border-indigo-500" : "border-zinc-700 hover:bg-zinc-800"}"></button>`).join("")
  ).join("<br>");
  const num = (key, val, step = 1) => `<input data-wm="${key}" type="number" value="${esc(val)}" step="${step}" min="0"
    class="w-20 bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100">`;
  const unitSel = (key, val) => `<select data-wm="${key}" class="bg-zinc-800 border border-zinc-700 rounded px-1 py-1 text-sm text-zinc-100">
    <option value="%" ${val === "%" ? "selected" : ""}>%</option><option value="px" ${val === "px" ? "selected" : ""}>px</option></select>`;

  const wmBody = !wm.enabled ? "" : `
    <div class="grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 mt-3 items-start">
      <div><div class="text-[11px] text-zinc-500 mb-1">Position</div><div class="leading-[0.6rem]">${posGrid}</div></div>
      <div class="space-y-2">
        <div class="flex items-center gap-2 text-sm text-zinc-400">Size ${num("size", wm.size)} ${unitSel("size_unit", wm.size_unit)} <span class="text-zinc-600 text-xs">(of video width, aspect locked)</span></div>
        <div class="flex items-center gap-2 text-sm text-zinc-400">Opacity <input data-wm="opacity" type="range" min="0" max="100" value="${esc(wm.opacity)}" class="w-40 accent-indigo-500"> <span id="gv-op-lbl" class="text-xs text-zinc-300 w-10">${esc(wm.opacity)} %</span></div>
        <div class="flex items-center gap-2 text-sm text-zinc-400">Margin X ${num("offset_x", wm.offset_x)} Y ${num("offset_y", wm.offset_y)} ${unitSel("offset_unit", wm.offset_unit)}</div>
        <button data-action="wm-preview" class="px-3 py-1.5 rounded-lg border border-zinc-600 text-sm text-zinc-200 hover:bg-zinc-800">Preview frame</button>
      </div>
    </div>
    ${r.previewUrl ? `<img src="${esc(r.previewUrl)}" class="mt-3 rounded-lg border border-zinc-700 max-h-72">` : ""}`;

  const wmInfo = r.wmInfo || {};
  const logoBlock = wmInfo.exists
    ? `<img src="${esc(wmInfo.url)}" class="h-10 bg-zinc-800/80 rounded px-1"> <span class="text-xs text-zinc-500">${wmInfo.width}×${wmInfo.height}px</span>`
    : `<span class="text-xs text-amber-400">No logo uploaded yet.</span>`;

  const o = r.out;
  const outBody = o.type === "project"
    ? `<div class="flex items-center gap-2 flex-wrap" data-fields>
        <input data-f="project" data-out="project" value="${esc(o.project)}" placeholder="Project *" class="gv-combo bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100 w-44" data-src="projects" autocomplete="off">
        <input data-f="sub_project" data-out="sub_project" value="${esc(o.sub_project)}" placeholder="Sub-project" class="gv-combo bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100 w-44" data-src="sub_projects" autocomplete="off">
        <select data-out="kind" class="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100">
          <option value="release" ${o.kind === "release" ? "selected" : ""}>release</option>
          <option value="pre-release" ${o.kind === "pre-release" ? "selected" : ""}>pre-release</option>
        </select></div>`
    : `<input data-out="folder" value="${esc(o.folder)}" placeholder="e.g. D:\\exports" class="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100 w-96">`;

  return `<div class="max-w-3xl mx-auto space-y-4">
    <div class="bg-zinc-950/60 rounded-xl p-4">
      <div class="flex items-center gap-3 mb-2">
        <div class="text-[11px] uppercase tracking-wider text-zinc-500 font-semibold flex-1">Clips (${r.clips.length}) — drag to set concat order · cut values in seconds</div>
        <span id="gv-release-total" class="text-xs text-zinc-400">total ${fmtSec(total)}</span>
      </div>
      <div class="space-y-1.5">${clipRows}</div>
      ${trimEditorHtml()}
      ${multi ? `<div class="flex gap-4 mt-3 text-sm text-zinc-300">
        <label class="flex items-center gap-1.5 cursor-pointer"><input type="radio" name="gv-mode" data-mode value="concat" ${r.mode === "concat" ? "checked" : ""} class="accent-indigo-500"> Join into ONE video (concat)</label>
        <label class="flex items-center gap-1.5 cursor-pointer"><input type="radio" name="gv-mode" data-mode value="each" ${r.mode === "each" ? "checked" : ""} class="accent-indigo-500"> Watermark each clip separately</label>
      </div>` : `<div class="text-xs text-zinc-500 mt-2">Single clip — trim it, add the watermark below and/or replace its audio.</div>`}
      <div id="gv-compat">${releaseCompatNote()}</div>
    </div>

    <div class="bg-zinc-950/60 rounded-xl p-4">
      <div class="flex items-center gap-3 flex-wrap">
        <label class="flex items-center gap-2 text-sm text-zinc-200 cursor-pointer font-medium">
          <input type="checkbox" data-wm="enabled" ${wm.enabled ? "checked" : ""} class="w-4 h-4 accent-indigo-500"> Watermark</label>
        ${logoBlock}
        <label class="text-xs text-indigo-400 hover:underline cursor-pointer">${wmInfo.exists ? "Replace logo…" : "Upload logo (PNG)…"}<input id="gv-wm-file" type="file" accept="image/png" class="hidden"></label>
      </div>
      ${wmBody}
    </div>

    <div class="bg-zinc-950/60 rounded-xl p-4 space-y-2">
      <div class="text-[11px] uppercase tracking-wider text-zinc-500 font-semibold">Output</div>
      <div class="flex gap-4 text-sm text-zinc-300">
        <label class="flex items-center gap-1.5 cursor-pointer"><input type="radio" name="gv-outtype" data-outtype value="project" ${o.type === "project" ? "checked" : ""} class="accent-indigo-500"> File into a project (kind release/pre-release)</label>
        <label class="flex items-center gap-1.5 cursor-pointer"><input type="radio" name="gv-outtype" data-outtype value="folder" ${o.type === "folder" ? "checked" : ""} class="accent-indigo-500"> Export to folder</label>
      </div>
      ${outBody}
      <div class="flex items-center gap-2 text-sm text-zinc-400 pt-1">Quality
        <select data-quality class="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100">
          <option value="standard" ${r.quality === "standard" ? "selected" : ""}>Standard (recommended, small files)</option>
          <option value="high" ${r.quality === "high" ? "selected" : ""}>High</option>
          <option value="max" ${r.quality === "max" ? "selected" : ""}>Near-lossless (very large files)</option>
        </select>
        <span class="text-xs text-zinc-600">applies to re-encodes — lossless concat is unaffected</span>
      </div>
      <div class="flex items-center gap-2 text-sm text-zinc-400">Audio
        <select data-audio class="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100">
          <option value="keep" ${r.audio === "keep" ? "selected" : ""}>Keep original audio</option>
          <option value="silent" ${r.audio === "silent" ? "selected" : ""}>Replace with silence</option>
        </select>
        <span class="text-xs text-zinc-600">the output always has an audio track — "silence" mutes all clips</span>
      </div>
    </div>

    <button data-action="render-start" class="w-full py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-medium">Start render</button>
    ${jobsBlock}
  </div>`;
}

async function startRender() {
  const r = state.release;
  const mode = r.clips.length > 1 ? r.mode : "each";
  const tooShort = r.clips.find((c) => clipTrimmed(c) && clipDuration(c) && clipEffective(c) < 0.2);
  if (tooShort) { toast(`Cutting that much leaves nothing of "${tooShort.name}"`, true); return; }
  if (mode === "each" && !r.wm.enabled && r.audio !== "silent" && !r.clips.every(clipTrimmed)) {
    toast("Nothing to render: enable the watermark, trim the clip(s) or replace the audio with silence.", true); return;
  }
  if (r.wm.enabled && !(r.wmInfo || {}).exists) { toast("Upload a watermark logo first", true); return; }
  if (r.out.type === "project" && !r.out.project.trim()) { toast("Output project is required", true); return; }
  if (r.out.type === "folder" && !r.out.folder.trim()) { toast("Output folder is required", true); return; }
  try {
    await jpost("/api/render/jobs", {
      clip_ids: r.clips.map((c) => c.id), mode,
      quality: r.quality,
      audio: r.audio,
      trims: r.clips.map((c) => [c.trimStart || 0, c.trimEnd || 0]),
      watermark: r.wm.enabled ? { ...r.wm } : null,
      output: r.out.type === "project"
        ? { type: "project", project: r.out.project.trim(), sub_project: r.out.sub_project.trim() || null, kind: r.out.kind }
        : { type: "folder", folder: r.out.folder.trim() },
    });
    toast("Render job(s) queued");
    await pollJobs();
  } catch (e) { toast("Could not start render: " + e.message, true); }
}

async function wmPreview() {
  const r = state.release;
  if (!r.clips.length) return;
  try {
    const resp = await fetch("/api/render/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clip_id: r.clips[0].id, watermark: r.wm.enabled ? { ...r.wm } : null,
                             trim_start: r.clips[0].trimStart || 0 }),
    });
    if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || resp.status);
    if (r.previewUrl) URL.revokeObjectURL(r.previewUrl);
    r.previewUrl = URL.createObjectURL(await resp.blob());
    renderMain();
  } catch (e) { toast("Preview failed: " + e.message, true); }
}

/* ---------- main render ---------- */
function renderMain() {
  const main = $("#gv-main");
  unloadMedia(main);                 // hover previews about to be replaced
  // the sidebar is re-rendered with the view — keep where the user scrolled it
  const side = $("#gv-sidebar");
  if (side) state.sidebarScroll = side.scrollTop;
  if (state.view === "inbox") main.innerHTML = renderInbox();
  else if (state.view === "trash") main.innerHTML = renderTrash();
  else if (state.view === "release") main.innerHTML = renderRelease();
  else main.innerHTML = renderLibrary();
  const newSide = $("#gv-sidebar");
  if (newSide && state.sidebarScroll) newSide.scrollTop = state.sidebarScroll;
  renderBulkbar();
  const sort = $("#gv-sort");
  if (sort) sort.addEventListener("change", () => { state.filters.sort = sort.value; refresh(); });
  const clear = $("#gv-clearfilters");
  if (clear) clear.addEventListener("click", () => {
    Object.assign(state.filters, { project: null, sub_project: null, kind: null, media_type: null, character: null, lora: null, upload: null });
    refresh();
  });
  const selAll = $("#gv-selectall");
  if (selAll) selAll.addEventListener("change", () => {
    if (selAll.checked) inboxVisible().forEach((a) => state.selection.add(a.id));
    else inboxVisible().forEach((a) => state.selection.delete(a.id));
    renderMain();
  });
  $$("[data-inbox-filter]").forEach((b) => b.addEventListener("click", () => {
    state.inboxFilter = b.dataset.inboxFilter || null;
    renderMain();
  }));
  const bulkFile = $("#gv-bulk-file");
  if (bulkFile) bulkFile.addEventListener("click", fileBulk);
  const bulkClear = $("#gv-bulk-clear");
  if (bulkClear) bulkClear.addEventListener("click", () => { state.selection.clear(); renderMain(); });
  const trashSelAll = $("#gv-trash-selectall");
  if (trashSelAll) trashSelAll.addEventListener("change", () => {
    if (trashSelAll.checked) state.assets.forEach((a) => state.selection.add(a.id));
    else state.selection.clear();
    renderMain();
  });
  const libSelAll = $("#gv-lib-selectall");
  if (libSelAll) libSelAll.addEventListener("change", () => {
    if (libSelAll.checked) state.assets.forEach((a) => state.selection.add(a.id));
    else state.selection.clear();
    renderMain();
  });
  const bulkMove = $("#gv-bulk-move");
  if (bulkMove) bulkMove.addEventListener("click", moveBulk);
  const makeRelease = $("#gv-make-release");
  if (makeRelease) makeRelease.addEventListener("click", () => {
    state.release.clips = state.assets
      .filter((a) => state.selection.has(a.id) && a.media_type === "video")
      .map((a) => ({ id: a.id, name: a.original_name, thumb: a.thumb_url, duration: (a.meta || {}).duration,
                     url: a.media_url, trimStart: 0, trimEnd: 0 }));
    state.release.editId = null;
    state.release.check = null;
    state.release.previewUrl = null;
    setView("release");
  });
  initTrimEditor();
  const wmFile = $("#gv-wm-file");
  if (wmFile) wmFile.addEventListener("change", async () => {
    if (!wmFile.files.length) return;
    const fd = new FormData();
    fd.append("file", wmFile.files[0]);
    try {
      state.release.wmInfo = await api("/api/render/watermark", { method: "POST", body: fd });
      toast("Watermark logo saved");
      renderMain();
    } catch (e) { toast("Logo upload failed: " + e.message, true); }
  });
}

/* ---------- filing ---------- */
const splitTags = (s) => (s || "").split(",").map((t) => t.trim()).filter(Boolean);

async function fileOne(id) {
  const a = assetById(id);
  const f = a._f || {};
  if (!f.project || !f.project.trim()) { toast("Project is required", true); return; }
  if (!f.kind) { toast("Kind is required", true); return; }
  try {
    releaseAsset(id);
    await jpost(`/api/assets/${id}/file`, {
      project: f.project, sub_project: f.sub_project || null, kind: f.kind,
      character: f.character || null, tags: splitTags(f.tags),
    });
    toast(`Filed: ${a.original_name}`);
    state.selection.delete(Number(id));
    await refresh();
  } catch (e) { toast("Filing failed: " + e.message, true); }
}

async function moveBulk() {
  const b = state.bulkLib;
  const patch = {};
  if (b.project.trim()) patch.project = b.project.trim();
  if (b.sub_project.trim()) patch.sub_project = b.sub_project.trim();
  if (b.kind) patch.kind = b.kind;
  if (!Object.keys(patch).length) { toast("Set a project, sub-project or kind first — empty fields keep the current value", true); return; }
  try {
    state.selection.forEach(releaseAsset);
    const r = await jpost("/api/assets/move-bulk", { ids: [...state.selection], ...patch });
    reportBulk(`${r.moved.length} file(s) moved`, r.errors);
    // keep failed files selected so they can simply be retried
    state.selection = new Set(r.errors.map((e) => Number(e.id)));
    await refresh();
  } catch (e) { toast("Move failed: " + e.message, true); }
}

/* Trash / restore / delete-forever for the whole selection. */
async function bulkAction(action) {
  const ids = [...state.selection];
  const n = ids.length;
  if (!n) return;
  if (action === "trash" && !confirm(`Move ${n} file(s) to the trash?`)) return;
  if (action === "purge" && !confirm(`Delete ${n} file(s) FOREVER?\n\nThis cannot be undone.`)) return;
  ids.forEach(releaseAsset);
  try {
    const r = await jpost(`/api/assets/bulk/${action}`, { ids });
    const verb = { trash: "moved to the trash", restore: "restored", purge: "deleted forever" }[action];
    reportBulk(`${r.done.length} file(s) ${verb}`, r.errors);
    state.selection = new Set(r.errors.map((e) => Number(e.id)));
    if (state.drawerId != null && r.done.includes(state.drawerId)) closeDrawer();
    await refresh();
  } catch (e) { toast("Failed: " + e.message, true); }
}

/* Rename a project everywhere (files are moved into the new project folder).
   Renaming onto an existing project merges the two. */
async function renameProject(old) {
  const facet = ((state.facets || {}).projects || []).find((p) => p.name === old);
  const input = prompt(`Rename project "${old}" (${facet ? facet.count : 0} file(s)) to:`, old);
  if (input == null) return;
  const name = input.trim();
  if (!name || name === old) return;
  if (state.values.projects.includes(name) &&
      !confirm(`A project named "${name}" already exists.\n\nMerge "${old}" into "${name}"?`)) return;
  try {
    const r = await jpost("/api/projects/rename", { old, new: name });
    if (state.filters.project === old) state.filters.project = name;
    if (state.bulkLib.project === old) state.bulkLib.project = name;
    if (state.release.out.project === old) state.release.out.project = name;
    reportBulk(`Project renamed to "${name}" (${r.renamed} file(s))`, r.errors);
    await refresh();
  } catch (e) { toast("Rename failed: " + e.message, true); }
}

/* Summary toast for bulk actions; names the failed files and the reason. */
function reportBulk(summary, errors) {
  if (!errors.length) { toast(summary); return; }
  const lines = errors.slice(0, 3).map((e) => `• ${e.name}: ${e.error}`);
  if (errors.length > 3) lines.push(`… and ${errors.length - 3} more`);
  toast(`${summary}, ${errors.length} failed:\n${lines.join("\n")}`, true);
}

async function fileBulk() {
  const b = state.bulk;
  if (!b.project || !b.project.trim()) { toast("Project is required", true); return; }
  if (!b.kind) { toast("Kind is required", true); return; }
  try {
    state.selection.forEach(releaseAsset);
    const r = await jpost("/api/assets/file-bulk", {
      ids: [...state.selection], project: b.project, sub_project: b.sub_project || null,
      kind: b.kind, character: b.character || null, tags: splitTags(b.tags),
    });
    reportBulk(`${r.filed.length} file(s) filed`, r.errors);
    state.selection = new Set(r.errors.map((e) => Number(e.id)));
    await refresh();
  } catch (e) { toast("Bulk filing failed: " + e.message, true); }
}

/* ---------- drawer ---------- */
function drawerPreview(a) {
  if (a.media_type === "video")
    return `<div class="overflow-hidden rounded-lg"><video src="${esc(a.media_url)}" controls loop class="w-full max-h-80 bg-black"></video></div>`;
  if (a.media_type === "image")
    return `<a href="${esc(a.media_url)}" target="_blank" class="block overflow-hidden rounded-lg"><img src="${esc(a.media_url)}" class="w-full max-h-80 object-contain bg-black"></a>`;
  return `<div class="bg-zinc-950 rounded-lg p-3 overflow-hidden">
    ${a.thumb_url ? `<img src="${esc(a.thumb_url)}" class="w-full h-24 object-cover rounded mb-2">` : ""}
    <audio src="${esc(a.media_url)}" controls class="w-full"></audio></div>`;
}

function metaRow(label, key, value, wide = false) {
  return `<label class="block ${wide ? "col-span-2" : ""}">
    <span class="text-[11px] text-zinc-500">${label}</span>
    <input data-meta="${key}" value="${esc(value ?? "")}" class="block w-full mt-0.5 bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100">
  </label>`;
}

function paramsBlock(a) {
  const m = a.meta || {};
  const loras = (m.loras || []).map((l) => `${l.name}${l.weight != null ? ` @ ${l.weight}` : ""}`).join(", ");
  return [
    m.positive_prompt ? `Prompt: ${m.positive_prompt}` : null,
    m.negative_prompt ? `Negative: ${m.negative_prompt}` : null,
    m.model ? `Model: ${m.model}` : null,
    m.seed ? `Seed: ${m.seed}` : null,
    m.sampler ? `Sampler: ${m.sampler}${m.scheduler ? " / " + m.scheduler : ""}` : null,
    m.steps ? `Steps: ${m.steps}` : null,
    m.cfg != null ? `CFG: ${m.cfg}` : null,
    m.shift != null ? `Shift: ${m.shift}` : null,
    loras ? `LoRAs: ${loras}` : null,
  ].filter(Boolean).join("\n");
}

function renderDrawer() {
  const el = $("#gv-drawer");
  const a = state.drawerId != null ? assetById(state.drawerId) : null;
  if (!a) { el.classList.add("translate-x-full"); unloadMedia(el); return; }
  unloadMedia(el);                   // old player is replaced below — abort its download
  const m = a.meta || {};
  const idx = state.assets.findIndex((x) => x.id === a.id);
  const isInbox = a.status === "inbox";
  const lorasText = (m.loras || []).map((l) => `${l.name}${l.weight != null ? ` @ ${l.weight}` : ""}`).join("\n");
  const info = [
    ["Original", a.original_name], ["File", a.file_name],
    ["Resolution", m.width ? `${m.width} × ${m.height}` : null],
    ["Duration", m.duration ? fmtDur(m.duration) : null],
    ["Size", fmtSize(m.filesize)],
    ["Created", fmtDate(a.created_at)], ["Added", fmtDate(a.added_at)],
    ["Source (hint)", a.source_hint],
  ].filter(([, v]) => v);

  el.innerHTML = `
  <div class="p-4 space-y-4">
    <div class="flex items-center gap-2">
      <button data-action="drawer-prev" class="px-2 py-1 rounded hover:bg-zinc-800 ${idx <= 0 ? "opacity-30 pointer-events-none" : ""}" title="Previous (←)">←</button>
      <button data-action="drawer-next" class="px-2 py-1 rounded hover:bg-zinc-800 ${idx >= state.assets.length - 1 ? "opacity-30 pointer-events-none" : ""}" title="Next (→)">→</button>
      <div class="flex-1 text-sm text-zinc-300 truncate font-medium" title="${esc(a.original_name)}">${esc(a.original_name)}</div>
      <button data-action="drawer-close" class="px-2 py-1 rounded hover:bg-zinc-800" title="Close (Esc)">✕</button>
    </div>

    ${drawerPreview(a)}

    <div class="flex items-center gap-3">
      ${starsHtml(a, "text-2xl")}
      ${favHtml(a, "text-2xl")}
      <div class="flex-1"></div>
      <button data-action="drawer-trash" data-id="${a.id}" class="px-3 py-1.5 rounded-lg bg-red-950 border border-red-900 text-red-300 hover:bg-red-900 text-sm">Delete</button>
    </div>

    <div class="text-xs text-zinc-500">
      Path (click to open in Explorer):<br>
      <button data-action="open-folder" data-id="${a.id}" class="text-left text-indigo-400 hover:text-indigo-300 hover:underline break-all">${esc(a.abs_path || "")}</button>
    </div>

    <div class="bg-zinc-950/60 rounded-xl p-3 space-y-2" data-fields>
      <div class="text-[11px] uppercase tracking-wider text-zinc-500 font-semibold">${isInbox ? "Add to library" : "Assignment"}</div>
      <div class="grid grid-cols-2 gap-2">
        ${fieldInputs("data-f", isInbox ? (a._f || {}) : { project: a.project, sub_project: a.sub_project, kind: a.kind, character: a.character, tags: (a.tags || []).join(", ") })}
      </div>
      ${isInbox
        ? `<button data-action="file-one" data-id="${a.id}" class="w-full py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium">Add to library</button>`
        : `<button data-action="drawer-save-assign" data-id="${a.id}" class="w-full py-1.5 rounded-lg bg-zinc-700 hover:bg-zinc-600 text-zinc-100 text-sm">Save assignment (moves file)</button>`}
    </div>

    <div class="bg-zinc-950/60 rounded-xl p-3 space-y-2">
      <div class="flex items-center gap-2">
        <div class="text-[11px] uppercase tracking-wider text-zinc-500 font-semibold flex-1">Metadata</div>
        <button data-action="copy-prompt" data-id="${a.id}" class="text-xs px-2 py-1 rounded border border-zinc-700 hover:bg-zinc-800 text-zinc-300">Copy prompt</button>
        <button data-action="copy-params" data-id="${a.id}" class="text-xs px-2 py-1 rounded border border-zinc-700 hover:bg-zinc-800 text-zinc-300">All parameters</button>
      </div>
      <label class="block"><span class="text-[11px] text-zinc-500">Prompt</span>
        <textarea data-meta="positive_prompt" rows="4" class="block w-full mt-0.5 bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100">${esc(m.positive_prompt ?? "")}</textarea></label>
      <label class="block"><span class="text-[11px] text-zinc-500">Negative prompt</span>
        <textarea data-meta="negative_prompt" rows="2" class="block w-full mt-0.5 bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100">${esc(m.negative_prompt ?? "")}</textarea></label>
      <div class="grid grid-cols-2 gap-2">
        ${metaRow("Model", "model", m.model, true)}
        ${metaRow("Seed", "seed", m.seed)}
        ${metaRow("Sampler", "sampler", m.sampler)}
        ${metaRow("Steps", "steps", m.steps)}
        ${metaRow("CFG", "cfg", m.cfg)}
        ${metaRow("Shift", "shift", m.shift)}
        ${metaRow("Scheduler", "scheduler", m.scheduler)}
      </div>
      <label class="block"><span class="text-[11px] text-zinc-500">LoRAs (one per line: name @ weight)</span>
        <textarea data-meta="loras" rows="2" class="block w-full mt-0.5 bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100">${esc(lorasText)}</textarea></label>
      <label class="block"><span class="text-[11px] text-zinc-500">Notes (free text, e.g. Suno style)</span>
        <textarea data-meta="notes" rows="2" class="block w-full mt-0.5 bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100">${esc(m.notes ?? "")}</textarea></label>
      <button data-action="save-meta" data-id="${a.id}" class="w-full py-1.5 rounded-lg bg-zinc-700 hover:bg-zinc-600 text-zinc-100 text-sm">Save metadata</button>
      ${m.source ? `<div class="text-[11px] text-zinc-600">Source: ${esc(m.source)} · <button data-action="copy-workflow" data-id="${a.id}" class="underline hover:text-zinc-400">Copy workflow JSON</button></div>` : ""}
    </div>

    ${a.kind === "release" ? `<div class="bg-zinc-950/60 rounded-xl p-3 space-y-2">
      <div class="text-[11px] uppercase tracking-wider text-zinc-500 font-semibold">Upload status</div>
      ${state.config.platforms.map((p) => {
        const u = (a.uploads || {})[p] || {};
        return `<div class="flex items-center gap-2 text-sm" data-platform="${esc(p)}">
          <label class="flex items-center gap-1.5 w-24 shrink-0 cursor-pointer">
            <input type="checkbox" data-action="upload-toggle" data-id="${a.id}" data-pf="${esc(p)}" ${u.posted ? "checked" : ""} class="w-4 h-4 accent-indigo-500">
            <span class="${u.posted ? "text-zinc-200" : "text-zinc-500"}">${esc(p)}</span>
          </label>
          <input data-action="upload-url" data-id="${a.id}" data-pf="${esc(p)}" value="${esc(u.url || "")}" placeholder="Post-URL"
            class="flex-1 bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-100 placeholder-zinc-600">
          <span class="text-xs text-zinc-500 w-20 text-right">${esc(u.date || "")}</span>
        </div>`;
      }).join("")}
    </div>` : `<div class="bg-zinc-950/60 rounded-xl p-3 text-[11px] text-zinc-600">Upload tracking is available for assets marked as kind "release".</div>`}

    <div class="bg-zinc-950/60 rounded-xl p-3">
      <div class="text-[11px] uppercase tracking-wider text-zinc-500 font-semibold mb-1.5">File info</div>
      <table class="w-full text-xs">${info.map(([k, v]) =>
        `<tr><td class="text-zinc-500 pr-2 py-0.5 align-top whitespace-nowrap">${esc(k)}</td><td class="text-zinc-300 break-all">${esc(v)}</td></tr>`).join("")}
      </table>
    </div>
  </div>`;
  el.classList.remove("translate-x-full");
}

function openDrawer(id) { state.drawerId = Number(id); renderDrawer(); }
function closeDrawer() {
  state.drawerId = null;
  const el = $("#gv-drawer");
  if (el) { el.classList.add("translate-x-full"); unloadMedia(el); }
}
function drawerNav(dir) {
  const idx = state.assets.findIndex((x) => x.id === state.drawerId);
  const next = state.assets[idx + dir];
  if (next) openDrawer(next.id);
}

/* ---------- asset mutations ---------- */
async function patchAsset(id, patch, silent = false) {
  try {
    const updated = await jpatch(`/api/assets/${id}`, patch);
    const i = state.assets.findIndex((a) => a.id === Number(id));
    if (i >= 0) state.assets[i] = { ...state.assets[i], ...updated, _f: state.assets[i]._f };
    if (!silent) toast("Saved");
    return updated;
  } catch (e) { toast("Saving failed: " + e.message, true); throw e; }
}

async function setRating(id, val) {
  const a = assetById(id);
  if (!a) return;
  await patchAsset(id, { rating: a.rating === val ? 0 : val }, true);
  renderMain();
  if (state.drawerId === Number(id)) renderDrawer();
}

async function toggleFav(id) {
  const a = assetById(id);
  if (!a) return;
  await patchAsset(id, { favorite: !a.favorite }, true);
  if (state.view === "favorites") { await refresh(); return; }
  renderMain();
  if (state.drawerId === Number(id)) renderDrawer();
}

function parseLoras(text) {
  return (text || "").split("\n").map((line) => {
    const t = line.trim();
    if (!t) return null;
    const m = t.match(/^(.*?)\s*@\s*([0-9.+-]+)\s*$/);
    return m ? { name: m[1].trim(), weight: parseFloat(m[2]) } : { name: t, weight: null };
  }).filter(Boolean);
}

async function saveMeta(id) {
  const el = $("#gv-drawer");
  const meta = {};
  $$("[data-meta]", el).forEach((inp) => {
    const k = inp.dataset.meta, v = inp.value.trim();
    if (k === "loras") meta[k] = parseLoras(v);
    else if (k === "steps") meta[k] = v ? parseInt(v, 10) : null;
    else if (k === "cfg" || k === "shift") meta[k] = v ? parseFloat(v) : null;
    else meta[k] = v || null;
  });
  await patchAsset(id, { meta });
  await refresh();
}

async function saveAssignment(id) {
  const scope = $("#gv-drawer [data-fields]");
  const get = (k) => { const i = $(`[data-f="${k}"]`, scope); return i ? i.value.trim() : ""; };
  if (!get("project") || !get("kind")) { toast("Project and kind are required", true); return; }
  releaseAsset(id);
  await patchAsset(id, {
    project: get("project"), sub_project: get("sub_project") || null, kind: get("kind"),
    character: get("character") || null, tags: splitTags(get("tags")),
  });
  await refresh();
}

async function trashAsset(id) {
  const a = assetById(id);
  if (!a) return;
  if (!confirm(`Move "${a.original_name}" to the trash?`)) return;
  try {
    releaseAsset(id);
    await jpost(`/api/assets/${id}/trash`);
    toast("Moved to trash");
    closeDrawer();
    await refresh();
  } catch (e) { toast("Delete failed: " + e.message, true); }
}

async function restoreAsset(id) {
  try {
    await jpost(`/api/assets/${id}/restore`);
    toast("Restored");
    await refresh();
  } catch (e) { toast("Restore failed: " + e.message, true); }
}

async function purgeAsset(id) {
  const a = assetById(id);
  if (!a) return;
  if (!confirm(`Delete "${a.original_name}" FOREVER?\n\nThis cannot be undone.`)) return;
  try {
    releaseAsset(id);
    await jdel(`/api/assets/${id}`);
    toast("Deleted forever");
    await refresh();
  } catch (e) { toast("Delete failed: " + e.message, true); }
}

/* ---------- audio hover playback ---------- */
let audioEl = null, audioPlayingId = null;
function toggleAudio(id) {
  const a = assetById(id);
  if (!a) return;
  if (audioPlayingId === Number(id) && audioEl && !audioEl.paused) {
    audioEl.pause(); audioPlayingId = null; return;
  }
  if (!audioEl) audioEl = new Audio();
  audioEl.src = a.media_url;
  audioEl.play().catch(() => {});
  audioPlayingId = Number(id);
}

/* ---------- global event wiring ---------- */
function initEvents() {
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-action]");
    if (!t) return;
    const { action, id } = t.dataset;
    if (action === "rate") { e.stopPropagation(); setRating(id, Number(t.dataset.val)); }
    else if (action === "fav") { e.stopPropagation(); toggleFav(id); }
    else if (action === "open-drawer") openDrawer(id);
    else if (action === "drawer-close") closeDrawer();
    else if (action === "drawer-prev") drawerNav(-1);
    else if (action === "drawer-next") drawerNav(1);
    else if (action === "drawer-trash") trashAsset(id);
    else if (action === "file-one") fileOne(id);
    else if (action === "save-meta") saveMeta(id);
    else if (action === "drawer-save-assign") saveAssignment(id);
    else if (action === "restore") restoreAsset(id);
    else if (action === "bulk-trash") bulkAction("trash");
    else if (action === "bulk-restore") bulkAction("restore");
    else if (action === "bulk-purge") bulkAction("purge");
    else if (action === "rename-project") { e.stopPropagation(); renameProject(t.dataset.val); }
    else if (action === "purge") purgeAsset(id);
    else if (action === "audio-toggle") { e.stopPropagation(); toggleAudio(id); }
    else if (action === "copy-prompt") {
      const a = assetById(id);
      copyText((a.meta || {}).positive_prompt || "", "Prompt");
    }
    else if (action === "copy-params") copyText(paramsBlock(assetById(id)), "Parameters");
    else if (action === "copy-workflow") {
      try {
        const full = await api(`/api/assets/${id}`);
        copyText((full.meta || {}).raw_graph || "", "Workflow JSON");
      } catch (err) { toast("Could not load workflow", true); }
    }
    else if (action === "open-folder") {
      try { await jpost(`/api/assets/${id}/open-folder`); }
      catch (err) { toast("Could not open folder: " + err.message, true); }
    }
    else if (action === "wm-pos") { state.release.wm.position = t.dataset.pos; renderMain(); }
    else if (action === "wm-preview") wmPreview();
    else if (action === "render-start") startRender();
    else if (action === "clip-edit") {
      const c = state.release.clips[Number(t.dataset.idx)];
      state.release.editId = c && state.release.editId !== c.id ? c.id : null;
      state.release.rangePlay = false;
      renderMain();
      const ed = $("#gv-trim-editor");
      if (ed) ed.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    else if (action === "clip-edit-close") { state.release.editId = null; renderMain(); }
    else if (action.startsWith("trim-")) trimAction(action, t);
    else if (action === "clip-remove") {
      const removed = state.release.clips[Number(t.dataset.idx)];
      if (removed && removed.id === state.release.editId) state.release.editId = null;
      state.release.clips.splice(Number(t.dataset.idx), 1);
      loadReleaseData(); renderMain();
    }
    else if (action === "clip-up" || action === "clip-down") {
      const i = Number(t.dataset.idx), j = action === "clip-up" ? i - 1 : i + 1;
      const clips = state.release.clips;
      if (j >= 0 && j < clips.length) { [clips[i], clips[j]] = [clips[j], clips[i]]; renderMain(); }
    }
    else if (action === "job-cancel") {
      try { await jpost(`/api/render/jobs/${t.dataset.job}/cancel`); await pollJobs(); }
      catch (err) { toast("Cancel failed: " + err.message, true); }
    }
    else if (action === "job-remove") { await jdel(`/api/render/jobs/${t.dataset.job}`); await pollJobs(); }
    else if (action === "job-open-asset") {
      state.view = "library"; updateTopbar(); await refresh(); openDrawer(t.dataset.id);
    }
    else if (action === "copy-path") copyText(t.dataset.path, "Path");
  });

  // release view: inline nav + form bindings
  document.addEventListener("click", (e) => {
    const nav = e.target.closest("[data-nav-inline]");
    if (nav) setView(nav.dataset.navInline);
  });
  document.addEventListener("input", (e) => {
    const t = e.target;
    if (t.dataset.wm) {
      const k = t.dataset.wm;
      state.release.wm[k] = t.type === "checkbox" ? t.checked : t.value;
      if (k === "opacity") { const l = $("#gv-op-lbl"); if (l) l.textContent = `${t.value} %`; }
      if (k === "enabled") renderMain();
    }
    if (t.dataset.out !== undefined && t.dataset.out !== "") state.release.out[t.dataset.out] = t.value;
    if (t.dataset.trim) {
      const c = state.release.clips[Number(t.dataset.idx)];
      if (c) {
        const v = Math.max(0, parseFloat(t.value) || 0);
        if (t.dataset.trim === "start") c.trimStart = v; else c.trimEnd = v;
        const len = $(`[data-cliplen="${t.dataset.idx}"]`);
        if (len) len.innerHTML = clipLenHtml(c);
        const tot = $("#gv-release-total");
        if (tot) tot.textContent = `total ${fmtSec(state.release.clips.reduce((sum, x) => sum + clipEffective(x), 0))}`;
        const note = $("#gv-compat");
        if (note) note.innerHTML = releaseCompatNote();
        if (c.id === state.release.editId) drawTrimBar();
      }
    }
  });
  document.addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset.mode !== undefined) { state.release.mode = t.value; renderMain(); }
    if (t.dataset.outtype !== undefined) { state.release.out.type = t.value; renderMain(); }
    if (t.dataset.quality !== undefined) state.release.quality = t.value;
    if (t.dataset.audio !== undefined) { state.release.audio = t.value; renderMain(); }
  });

  // release view: drag & drop concat order
  let dragIdx = null;
  document.addEventListener("dragstart", (e) => {
    if (e.target.closest && e.target.closest("input")) { e.preventDefault(); return; }
    const row = e.target.closest(".gv-clip-row");
    if (row) { dragIdx = Number(row.dataset.idx); e.dataTransfer.effectAllowed = "move"; }
  });
  document.addEventListener("dragover", (e) => {
    if (dragIdx != null && e.target.closest(".gv-clip-row")) e.preventDefault();
  });
  document.addEventListener("drop", (e) => {
    const row = e.target.closest(".gv-clip-row");
    if (!row || dragIdx == null) return;
    e.preventDefault();
    const to = Number(row.dataset.idx);
    const clips = state.release.clips;
    const [moved] = clips.splice(dragIdx, 1);
    clips.splice(to, 0, moved);
    dragIdx = null;
    renderMain();
  });
  document.addEventListener("dragend", () => { dragIdx = null; });

  // render job polling — only while relevant
  setInterval(() => {
    const active = state.renderJobs.some((j) => j.status === "queued" || j.status === "running");
    if (state.view === "release" || active) pollJobs();
  }, 2500);

  // facet clicks (sidebar)
  document.addEventListener("click", (e) => {
    const c = e.target.closest("[data-collapse]");
    if (c) {
      const key = c.dataset.collapse;
      state.collapsed.has(key) ? state.collapsed.delete(key) : state.collapsed.add(key);
      localStorage.setItem("gv-collapsed", JSON.stringify([...state.collapsed]));
      renderMain();
      return;
    }
    const t = e.target.closest("[data-facet]");
    if (!t) return;
    const key = t.dataset.facet, val = t.dataset.val;
    state.filters[key] = state.filters[key] === val ? null : val;
    if (key === "project") state.filters.sub_project = null;
    refresh();
  });

  // inbox selection checkboxes
  document.addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset.action === "select") {
      const id = Number(t.dataset.id);
      if (t.checked) state.selection.add(id); else state.selection.delete(id);
      renderMain();
    } else if (t.dataset.action === "upload-toggle") {
      const pf = t.dataset.pf;
      patchAsset(t.dataset.id, { uploads: { [pf]: { posted: t.checked } } }, true)
        .then(() => { if (state.drawerId) renderDrawer(); });
    } else if (t.dataset.action === "upload-url") {
      const pf = t.dataset.pf;
      patchAsset(t.dataset.id, { uploads: { [pf]: { url: t.value } } }, true);
    }
  });

  // per-card inbox field edits — stored on the asset, applied when filing
  document.addEventListener("input", (e) => {
    const t = e.target;
    if (t.dataset.bulklib !== undefined && t.dataset.bulklib !== "") {
      state.bulkLib[t.dataset.bulklib] = t.value;
      return;
    }
    if (t.dataset.bulk !== undefined && t.dataset.bulk !== "") {
      state.bulk[t.dataset.bulk] = t.value;
      return;
    }
    if (t.dataset.f) {
      const card = t.closest(".gv-inbox-card, #gv-drawer");
      if (!card) return;
      const id = card.id === "gv-drawer" ? state.drawerId : Number(card.dataset.id);
      const a = assetById(id);
      if (a && a.status === "inbox") {
        a._f = a._f || {};
        a._f[t.dataset.f] = t.value;
      }
    }
  });

  // video hover preview
  document.addEventListener("mouseover", (e) => {
    const thumb = e.target.closest(".gv-thumb[data-video]");
    if (thumb) {
      state.focusId = Number(thumb.dataset.id);
      if (!thumb.querySelector("video")) {
        const v = document.createElement("video");
        v.src = thumb.dataset.video;
        v.muted = true; v.loop = true; v.autoplay = true; v.playsInline = true;
        v.preload = "metadata";
        v.className = "absolute inset-0 w-full h-full object-cover pointer-events-none";
        thumb.appendChild(v);
      }
      return;
    }
    const card = e.target.closest(".gv-card");
    if (card && card.dataset.id) state.focusId = Number(card.dataset.id);
  });
  document.addEventListener("mouseout", (e) => {
    const thumb = e.target.closest(".gv-thumb[data-video]");
    if (!thumb) return;
    if (e.relatedTarget && thumb.contains(e.relatedTarget)) return;
    thumb.querySelectorAll("video").forEach((v) => { unloadMedia(v); v.remove(); });
  });

  // keyboard shortcuts — only when no text field is focused
  document.addEventListener("keydown", (e) => {
    const ae = document.activeElement;
    const typing = ae && (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA" || ae.tagName === "SELECT" || ae.isContentEditable);
    if (e.key === "Escape") {
      if (comboActive) { hideCombo(); return; }
      if (typing) { ae.blur(); return; }
      closeDrawer(); return;
    }
    if (typing) return;
    const targetId = state.drawerId != null ? state.drawerId : state.focusId;
    if (e.key >= "1" && e.key <= "5" && targetId != null) { setRating(targetId, Number(e.key)); e.preventDefault(); }
    else if (e.key.toLowerCase() === "f" && targetId != null) { toggleFav(targetId); e.preventDefault(); }
    else if (e.key === "ArrowLeft" && state.drawerId != null) { drawerNav(-1); e.preventDefault(); }
    else if (e.key === "ArrowRight" && state.drawerId != null) { drawerNav(1); e.preventDefault(); }
  });

  // drag & drop upload
  let dragDepth = 0;
  window.addEventListener("dragenter", (e) => {
    if (![...(e.dataTransfer?.types || [])].includes("Files")) return;
    dragDepth++;
    $("#gv-drop-overlay").classList.remove("hidden");
    $("#gv-drop-overlay").classList.add("flex");
  });
  window.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) { $("#gv-drop-overlay").classList.add("hidden"); $("#gv-drop-overlay").classList.remove("flex"); }
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", async (e) => {
    e.preventDefault();
    dragDepth = 0;
    $("#gv-drop-overlay").classList.add("hidden");
    $("#gv-drop-overlay").classList.remove("flex");
    const files = [...(e.dataTransfer?.files || [])];
    if (!files.length) return;
    toast(`Uploading ${files.length} file(s)…`);
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    try {
      const r = await api("/api/upload", { method: "POST", body: fd });
      const failed = r.failed || [];
      toast(`${r.imported.length} file(s) added to the inbox${r.skipped.length ? `, ${r.skipped.length} skipped (unsupported format?)` : ""}`
        + (failed.length ? `, ${failed.length} failed: ${failed.map((f) => f.name).join(", ")}` : ""), failed.length > 0);
      if (state.view !== "inbox") setView("inbox"); else refresh();
    } catch (err) { toast("Upload failed: " + err.message, true); }
  });

  // background refresh: keep counts fresh without stealing focus
  setInterval(() => {
    const ae = document.activeElement;
    const typing = ae && (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA" || ae.tagName === "SELECT");
    if (typing || state.drawerId != null || document.hidden || state.view === "release") return;
    refresh();
  }, 7000);
}

/* ---------- boot ---------- */
(async function boot() {
  try {
    state.config = await api("/api/config");
  } catch {
    document.body.innerHTML = '<div class="p-8 text-red-300">Backend not reachable.</div>';
    return;
  }
  document.title = `genvault v${state.config.version}`;
  renderShell();
  initCombo();
  initEvents();
  await refresh();
  pollJobs();
})();

// Claude OneClick — UI controller.
// Talks to the local server at the same origin. The server stamps a CSRF
// cookie on the first GET; we read it back and echo it on every mutation.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

let state = null;
let selected = null; // currently-selected preset name (may differ from active)

// --- helpers ---------------------------------------------------------------

function csrf() {
  const m = document.cookie.match(/(?:^|;\s*)csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

async function api(method, path, body) {
  const opts = { method, headers: { "Accept": "application/json" } };
  if (method !== "GET") opts.headers["X-CSRF"] = csrf();
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const text = await r.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { _raw: text }; }
  if (!r.ok) {
    const msg = (data && data.error && (data.error.message || data.error)) || data.error || `HTTP ${r.status}`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

// Cross-browser fallback for the :has(dialog[open]) CSS rules: explicitly
// add a class to <body> whenever any <dialog> opens, remove it when the
// last dialog closes. Lets the .coc-dialog-open style hide floaters
// (footer, wiring popover, banners, toasts) on browsers without :has().
function _refreshDialogState() {
  const anyOpen = !!document.querySelector("dialog[open]");
  document.body.classList.toggle("coc-dialog-open", anyOpen);
}
// Patch every <dialog> we know about: showModal/show/close all flow
// through these. Wrap once at startup; new dialogs added later still
// fire the close event.
for (const d of document.querySelectorAll("dialog")) {
  const _show = d.showModal.bind(d);
  d.showModal = function (...args) {
    const r = _show(...args);
    _refreshDialogState();
    return r;
  };
  d.addEventListener("close", _refreshDialogState);
  d.addEventListener("cancel", _refreshDialogState);
}

function toast(msg, kind) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast show" + (kind === "error" ? " error" : "");
  setTimeout(() => { el.className = "toast"; }, 2400);
}

// --- state load/save -------------------------------------------------------

// `force` redraws the detail panel (clobbers form fields). The 5-second
// background poll passes force=false so it only updates the toggle/sidebar
// without stomping whatever the user is typing.
async function refresh(force = true) {
  state = await api("GET", "/api/state");
  if (!selected) selected = state.active || (state.presets[0] && state.presets[0].name);
  renderToggle();
  renderSidebar();
  if (force) renderDetail();
  showBanner();
}

function renderToggle() {
  $("#toggle").checked = !!state.enabled;
  $("#state-text").textContent = state.enabled ? "ON" : "OFF";
  $("#status-dot").className = "dot" + (state.enabled ? " on" : "");
  $("#status-dot").title = state.enabled ? "ON — Claude Code is routed" : "OFF — using Anthropic default";
  const active = state.presets.find(p => p.name === state.active);
  $("#active-pill").textContent = active ? (active.label || active.name) : "(none)";

  // Proxy pill: show only when meaningful (toggle ON + openai preset).
  const proxyPill = $("#proxy-pill");
  if (state.enabled && active && active.format === "openai") {
    proxyPill.classList.remove("hidden");
    if (state.proxy && state.proxy.running) {
      proxyPill.textContent = `proxy: running on :${state.proxy.port}`;
      proxyPill.className = "pill secondary ok";
    } else {
      proxyPill.textContent = "proxy: starting…";
      proxyPill.className = "pill secondary warn";
    }
  } else {
    proxyPill.classList.add("hidden");
  }
}

function renderSidebar() {
  const ul = $("#preset-list");
  ul.innerHTML = "";
  const q = ($("#preset-search") && $("#preset-search").value || "").toLowerCase().trim();

  // Group presets by `group` field; user-created presets fall under
  // "Custom". Section order is fixed for stability.
  const SECTION_ORDER = ["DeepSeek", "NVIDIA NIMs", "Other hosted", "Local", "Custom", "Default"];
  const groups = {};
  for (const p of state.presets) {
    if (q && !((p.label || p.name).toLowerCase().includes(q) ||
               p.name.toLowerCase().includes(q) ||
               (p.base_url || "").toLowerCase().includes(q) ||
               (p.subtitle || "").toLowerCase().includes(q))) continue;
    const gname = p.builtin ? (p.group || "Other hosted") : "Custom";
    (groups[gname] ||= []).push(p);
  }

  let visible = 0;
  for (const section of SECTION_ORDER) {
    const items = groups[section];
    if (!items || !items.length) continue;

    // Section header. Built-in provider sections (everything except
    // Custom/Default) get a "↻ Browse" button that fetches the live
    // catalog from the provider on click.
    const head = document.createElement("li");
    head.className = "section-head";
    const browsable = !["Custom", "Default"].includes(section);
    head.innerHTML = browsable
      ? `<span>${escape(section)}</span>
         <span class="section-tools">
           <button class="add-custom-btn" data-add-group="${escape(section)}" title="Add a custom model from this provider">+ Custom</button>
           <button class="browse-btn" data-group="${escape(section)}" title="Fetch live model list from the provider">↻ Browse</button>
         </span>`
      : `<span>${escape(section)}</span>`;
    ul.appendChild(head);

    for (const p of items) {
      visible++;
      const li = document.createElement("li");
      li.className = "preset-card" +
        (p.name === selected ? " selected" : "") +
        (p.name === state.active ? " active" : "");
      const tags = (p.tags || [])
        .map(t => `<span class="tagchip">${escape(t)}</span>`).join("");
      const pricing = p.pricing
        ? `<span class="pricechip ${escape(p.pricing)}">${escape(pricingLabel(p.pricing))}</span>`
        : "";
      const dot = p.format === "anthropic"
        ? '<span class="key-dot none" title="no key needed"></span>'
        : (p.api_key_set
            ? '<span class="key-dot ok" title="API key saved"></span>'
            : '<span class="key-dot warn" title="API key not set"></span>');
      li.innerHTML = `
        <div class="card-row1">
          ${dot}
          <span class="preset-name">${escape(p.label || p.name)}</span>
          <span class="card-tags">${pricing}${tags}</span>
        </div>
        <div class="card-row2 muted">${escape(p.subtitle || "")}</div>
      `;
      li.onclick = () => { selected = p.name; renderSidebar(); renderDetail(); };
      ul.appendChild(li);
    }

    // "more from provider" expansion area — populated when the user
    // clicks ↻ Browse.
    const more = document.createElement("li");
    more.className = "section-extras";
    more.dataset.group = section;
    ul.appendChild(more);

    // Re-attach previously fetched live models if we have them in
    // memory so they survive search/redraw.
    if (browsable && _liveCatalog[section]) renderLiveCatalog(section, _liveCatalog[section]);
  }
  $("#side-empty").classList.toggle("hidden", visible > 0);
}

// In-process cache of the live catalog by group, so re-renders don't
// re-fetch and don't lose the expanded state.
const _liveCatalog = {};

function renderLiveCatalog(group, models) {
  const host = document.querySelector(`.section-extras[data-group="${cssEscape(group)}"]`);
  if (!host) return;
  if (!models.length) {
    host.innerHTML = `<div class="extras-empty">No additional models from this provider.</div>`;
    return;
  }
  // Group by vendor (the part before the first '/'). Models without a
  // slash go into a default "—" bucket (e.g. Ollama tags like
  // 'llama3.1:70b').
  const byVendor = {};
  for (const m of models) {
    const idx = m.indexOf("/");
    const vendor = idx > 0 ? m.slice(0, idx) : "—";
    (byVendor[vendor] ||= []).push(m);
  }
  const vendors = Object.keys(byVendor).sort();

  const seed = state.presets.find(p => p.group === group && p.builtin) || {};
  let html = `<div class="extras-head">${models.length} more from provider</div>`;
  for (const v of vendors) {
    html += `<div class="extras-vendor">${escape(v)} <span class="muted">·</span> ${byVendor[v].length}</div>`;
    html += byVendor[v].map(m => {
      const pricing = inferPricing(group, m);
      const chip = pricing
        ? `<span class="pricechip ${escape(pricing)}" title="best-guess (provider doesn't expose pricing in /v1/models)">${escape(pricingLabel(pricing))}?</span>`
        : "";
      return `
        <div class="extra-card" data-model="${escape(m)}" data-seed="${escape(seed.name || "")}">
          <span class="key-dot none"></span>
          <span class="preset-name mono">${escape(m)}</span>
          ${chip}
        </div>
      `;
    }).join("");
  }
  host.innerHTML = html;
  for (const card of host.querySelectorAll(".extra-card")) {
    card.onclick = () => useLiveModel(card.dataset.seed, card.dataset.model);
  }
}

function cssEscape(s) {
  return String(s).replace(/["\\]/g, "\\$&");
}

async function browseProvider(group) {
  const btn = document.querySelector(`.browse-btn[data-group="${cssEscape(group)}"]`);
  if (btn) { btn.disabled = true; btn.textContent = "↻ Loading…"; }
  try {
    const r = await api("GET", `/api/provider-models?group=${encodeURIComponent(group)}`);
    _liveCatalog[group] = r.extra_models || [];
    renderLiveCatalog(group, _liveCatalog[group]);
    toast(`Loaded ${r.all_models.length} models from ${group}`);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "↻ Browse"; }
  }
}

async function useLiveModel(seedPresetName, model) {
  // Create (or update) a custom preset that points at the same provider
  // as the seed but uses this exact model id, then select it.
  const seed = state.presets.find(p => p.name === seedPresetName);
  if (!seed) { toast("seed preset missing", "error"); return; }
  const safeModel = String(model).replace(/[^A-Za-z0-9_-]+/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
  const name = `${seed.group.toLowerCase().replace(/\s+/g, "-")}-${safeModel}`.slice(0, 60);
  try {
    await api("POST", "/api/preset", {
      name,
      label: `${seed.label.split(" — ")[0]} · ${model}`,
      base_url: seed.base_url,
      model,
      small_fast_model: seed.small_fast_model || model,
      format: seed.format,
      notes: `Created from the live catalog of ${seed.group}.`,
    });
    selected = name;
    await refresh();
    toast(`Created preset for ${model}`);
  } catch (err) { toast(err.message, "error"); }
}

// Click delegation for the dynamic ↻ Browse and + Custom buttons.
document.addEventListener("click", async (e) => {
  const browse = e.target.closest && e.target.closest(".browse-btn");
  if (browse && browse.dataset.group) { e.stopPropagation(); browseProvider(browse.dataset.group); return; }
  const addCustom = e.target.closest && e.target.closest(".add-custom-btn");
  if (addCustom && addCustom.dataset.addGroup) {
    e.stopPropagation();
    const g = addCustom.dataset.addGroup;
    const seed = state.presets.find(p => p.group === g && p.builtin) || {};
    const model = prompt(`Enter the upstream model id from ${g} (e.g. "deepseek-ai/deepseek-v4-pro"):`);
    if (!model) return;
    await useLiveModel(seed.name || "", model.trim());
  }
});

function getSelected() {
  return state.presets.find(p => p.name === selected) || null;
}

function renderDetail() {
  const p = getSelected();
  if (!p) return;
  $("#preset-title").textContent = p.label || p.name;
  $("#preset-notes").textContent = p.notes || "";

  $("#f-label").value = p.label || "";
  $("#f-base_url").value = p.base_url || "";
  $("#f-api_key").value = "";
  $("#f-api_key").placeholder = p.api_key_set ? "(saved — leave blank to keep)" : "sk-...";
  // Visible "saved" badge next to the API-key label.
  const ks = $("#api-key-status");
  if (p.api_key_set) {
    ks.textContent = "✓ saved"; ks.classList.remove("empty");
  } else {
    ks.textContent = "• not set yet"; ks.classList.add("empty");
  }
  for (const r of $$('input[name=format]')) r.checked = (r.value === (p.format || "openai"));

  $("#f-model").value = p.model || "";
  $("#f-small_fast_model").value = p.small_fast_model || "";

  const samp = p.sampling || {};
  $("#f-s-temperature").value = samp.temperature ?? "";
  $("#f-s-top_p").value = samp.top_p ?? "";
  $("#f-s-top_k").value = samp.top_k ?? "";
  $("#f-s-max_tokens").value = samp.max_tokens ?? "";
  $("#f-request_timeout_seconds").value = p.request_timeout_seconds ?? "";
  $("#f-retries").value = p.retries ?? "";
  $("#f-retry_backoff").value = p.retry_backoff ?? "";

  $("#f-system_prompt_prefix").value = p.system_prompt_prefix || "";
  $("#f-system_prompt_suffix").value = p.system_prompt_suffix || "";
  $("#f-disable_streaming").checked = !!p.disable_streaming;
  $("#f-prompt_cache_passthrough").checked = !!p.prompt_cache_passthrough;
  $("#f-reasoning_enabled").checked = !!p.reasoning_enabled;
  setReasoningEffortFromName(p.reasoning_effort || "medium");
  $("#f-extra_body").value = p.extra_body && Object.keys(p.extra_body).length ? JSON.stringify(p.extra_body, null, 2) : "";
  $("#f-notes").value = p.notes || "";

  renderHeaders(p.extra_headers || {});
  renderAliases(p.model_aliases || {});

  $("#btn-delete").style.display = p.builtin ? "none" : "";
}

function renderHeaders(headers) {
  const root = $("#headers");
  root.innerHTML = "";
  for (const [k, v] of Object.entries(headers)) addHeaderRow(k, v);
}
function addHeaderRow(k = "", v = "") {
  const row = document.createElement("div");
  row.className = "kv";
  row.innerHTML = `<input class="hk" placeholder="header" value="${escape(k)}"><input class="hv" placeholder="value" value="${escape(v)}"><button class="x" title="remove">✕</button>`;
  row.querySelector(".x").onclick = () => row.remove();
  $("#headers").appendChild(row);
}

function renderAliases(aliases) {
  const root = $("#aliases");
  root.innerHTML = "";
  for (const [k, v] of Object.entries(aliases)) addAliasRow(k, v);
}
function addAliasRow(k = "", v = "") {
  const row = document.createElement("div");
  row.className = "kv";
  row.innerHTML = `<input class="ak" placeholder="claude-sonnet-4" value="${escape(k)}"><input class="av" placeholder="upstream-model-id" value="${escape(v)}"><button class="x" title="remove">✕</button>`;
  row.querySelector(".x").onclick = () => row.remove();
  $("#aliases").appendChild(row);
}

function escape(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }

function pricingLabel(p) {
  return ({"free": "free", "free-tier": "free tier", "paid": "paid", "varies": "varies"})[p] || p;
}

// Best-effort pricing inference for live-fetched models. Returns one of
// "free" | "free-tier" | "paid" | null. Patterns are conservative and
// based on what each provider publishes as free vs metered. NEVER
// authoritative — providers change tiers without notice; we mark with a
// "?" suffix in the UI to make that clear.
function inferPricing(group, modelId) {
  const id = String(modelId || "").toLowerCase();
  if (group === "Local") return "free";
  if (group === "NVIDIA NIMs") {
    // Anything claiming "v4" on DeepSeek/GLM/Nemotron-Ultra is paid.
    if (id.includes("deepseek-v4")) return "paid";
    if (id.includes("deepseek-r2")) return "paid";
    if (id.startsWith("zhipuai/glm-4.5") || id.startsWith("zhipuai/glm-4.6")) return "paid";
    if (id.includes("405b")) return "paid";  // 405B-class models bill metered
    // Smaller open weights and the long-tail are typically free credits.
    if (id.startsWith("meta/llama-")) return "free-tier";
    if (id.startsWith("nvidia/llama-") || id.includes("nemotron")) return "free-tier";
    if (id.startsWith("deepseek-ai/deepseek-v3") ||
        id.startsWith("deepseek-ai/deepseek-r1") ||
        id.startsWith("deepseek-ai/deepseek-coder")) return "free-tier";
    if (id.startsWith("google/gemma") || id.startsWith("microsoft/phi") ||
        id.startsWith("mistralai/")) return "free-tier";
    return null;  // unknown → no badge
  }
  if (group === "DeepSeek") {
    return id.includes("v4") ? "paid" : "paid";  // DeepSeek's own API is all paid
  }
  if (group === "Other hosted") return "paid";  // Groq/Together/Fireworks bill all
  return null;
}

const REASONING_LEVELS = ["low", "medium", "high"];
function reasoningEffortName(v) { return REASONING_LEVELS[Math.max(0, Math.min(2, Number(v) - 1))] || "medium"; }
function setReasoningEffortFromName(name) {
  const idx = REASONING_LEVELS.indexOf(name);
  const slider = $("#f-reasoning_effort");
  slider.value = String((idx >= 0 ? idx : 1) + 1);
  $("#f-reasoning_effort_label").textContent = REASONING_LEVELS[idx >= 0 ? idx : 1];
}
// Live label update as the slider moves.
document.addEventListener("input", (e) => {
  if (e.target && e.target.id === "f-reasoning_effort") {
    $("#f-reasoning_effort_label").textContent = reasoningEffortName(e.target.value);
  }
});

function collectKVs(rootSel, kCls, vCls) {
  const out = {};
  for (const row of $$(rootSel + " .kv")) {
    const k = row.querySelector("." + kCls).value.trim();
    const v = row.querySelector("." + vCls).value;
    if (k) out[k] = v;
  }
  return out;
}

// --- actions ---------------------------------------------------------------

$("#toggle").addEventListener("change", async (e) => {
  try {
    await api("POST", "/api/toggle", { enabled: e.target.checked });
    await refresh();
    toast(e.target.checked ? "Routed to " + ($("#active-pill").textContent) : "Back to Anthropic default");
  } catch (err) { toast(err.message, "error"); refresh(); }
});

$("#btn-use").addEventListener("click", async () => {
  const p = getSelected(); if (!p) return;
  try { await api("POST", "/api/use", { name: p.name }); await refresh(); toast(`Using: ${p.label || p.name}`); }
  catch (err) { toast(err.message, "error"); }
});

$("#btn-save").addEventListener("click", async () => {
  const p = getSelected(); if (!p) return;
  const body = collectForm(p.name);
  try { await api("POST", "/api/preset", body); await refresh(); toast("Saved"); }
  catch (err) { toast(err.message, "error"); }
});

$("#btn-save-key").addEventListener("click", async () => {
  const p = getSelected(); if (!p) return;
  const key = $("#f-api_key").value;
  if (!key) { toast("Enter a key first", "error"); return; }
  try {
    const r = await api("POST", "/api/keys", { name: p.name, api_key: key });
    $("#f-api_key").value = "";
    await refresh();
    if (r && r.updated && r.updated.length > 1) {
      toast(`API key saved for ${r.updated.length} ${escape(p.group || "")} presets`);
    } else {
      toast("API key saved");
    }
  } catch (err) { toast(err.message, "error"); }
});

$("#btn-test").addEventListener("click", async () => {
  const p = getSelected(); if (!p) return;
  const out = $("#test-result");
  out.className = "muted small";
  out.textContent = "Testing… (sending a tiny request to the upstream)";
  try {
    const r = await api("POST", "/api/test-preset", { name: p.name });
    if (r.ok) {
      out.className = "small ok";
      out.innerHTML = `✓ <b>OK</b> in ${r.latency_ms}ms · ${escape(p.label || p.name)} replied: <i>"${escape(r.response_text || "(empty)")}"</i>`;
    } else {
      out.className = "small error";
      out.textContent = "✗ " + (r.error || "test failed");
    }
    refreshUsage();
  } catch (err) {
    out.className = "small error";
    out.textContent = "✗ " + err.message;
  }
});

$("#btn-delete").addEventListener("click", async () => {
  const p = getSelected(); if (!p || p.builtin) return;
  const ok = await confirmDialog(`Delete "${p.label || p.name}"?`,
    `This removes the preset and any saved API key. You can recreate it later.`,
    "Delete");
  if (!ok) return;
  try { await api("DELETE", "/api/preset/" + encodeURIComponent(p.name)); selected = null; await refresh(); toast("Deleted"); }
  catch (err) { toast(err.message, "error"); }
});

function confirmDialog(title, body, okLabel = "OK") {
  return new Promise(resolve => {
    const dlg = $("#confirm-dialog");
    $("#confirm-title").textContent = title;
    $("#confirm-body").textContent = body;
    $("#confirm-ok").textContent = okLabel;
    dlg.addEventListener("close", function once() {
      dlg.removeEventListener("close", once);
      resolve(dlg.returnValue === "ok");
    });
    dlg.showModal();
  });
}

$("#btn-new").addEventListener("click", async () => {
  const name = prompt("Preset id (alnum, - and _):");
  if (!name) return;
  const body = { name, label: name, base_url: "", api_key: "", model: "", small_fast_model: "", format: "openai", notes: "" };
  try { await api("POST", "/api/preset", body); selected = name; await refresh(); toast("Created"); }
  catch (err) { toast(err.message, "error"); }
});

$("#btn-refresh-models").addEventListener("click", async () => {
  const p = getSelected(); if (!p) return;
  $("#models-status").textContent = "Loading…";
  try {
    const r = await api("GET", `/api/models?name=${encodeURIComponent(p.name)}&force=1`);
    const dl = $("#models-list");
    dl.innerHTML = "";
    for (const m of r.models) {
      const opt = document.createElement("option");
      opt.value = m;
      dl.appendChild(opt);
    }
    $("#models-status").textContent = `${r.models.length} models loaded${r.cached ? " (cached)" : ""}`;
  } catch (err) { $("#models-status").textContent = "Error: " + err.message; }
});

$("#btn-add-header").addEventListener("click", () => addHeaderRow());
$("#btn-add-alias").addEventListener("click", () => addAliasRow());

// Live preset filter.
$("#preset-search").addEventListener("input", () => renderSidebar());

// Cmd/Ctrl+Enter saves the current preset from any input.
window.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
    e.preventDefault();
    $("#btn-save").click();
  }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "n" && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
    e.preventDefault();
    $("#btn-new").click();
  }
});

$("#btn-refresh-log").addEventListener("click", refreshLog);
$("#btn-restart-proxy").addEventListener("click", async () => {
  try { await api("POST", "/api/proxy/restart", {}); toast("Proxy restarted"); refreshLog(); }
  catch (err) { toast(err.message, "error"); }
});

async function refreshLog() {
  try {
    const r = await api("GET", "/api/log/proxy?lines=300");
    $("#log-tail").textContent = (r.lines || []).join("\n") || "(empty)";
    $("#log-status").textContent = state && state.proxy && state.proxy.running ? "proxy: running" : "proxy: stopped";
  } catch (err) { $("#log-status").textContent = "log error: " + err.message; }
}

// --- tabs ------------------------------------------------------------------

for (const t of $$(".tab")) {
  t.addEventListener("click", () => {
    for (const x of $$(".tab")) x.classList.toggle("active", x === t);
    for (const p of $$(".panel")) p.classList.toggle("active", p.id === "panel-" + t.dataset.tab);
    if (t.dataset.tab === "logs") refreshLog();
    if (t.dataset.tab === "wiring") refreshDiagnose();
    if (t.dataset.tab === "export") renderExportSnippets();
  });
}

// --- Persistent wiring strip (always visible below the topbar) -----------

let _stripInterval = null;
function startStripPolling() {
  if (_stripInterval) return;
  _stripInterval = setInterval(() => doStripDiagnose().catch(() => {}), 6000);
}

async function doStripDiagnose() {
  try {
    const r = await api("GET", "/api/diagnose");
    paintStrip(r);
    // Also paint the in-tab Wiring panel if it's open.
    if (document.querySelector("#panel-wiring.active")) paintDiagnose(r);
  } catch (_) { /* ignore — keep last good state */ }
}

function paintStrip(r) {
  const sumEl = $("#wstrip-summary");
  const overall = r.overall || "off";
  sumEl.className = "wstrip-summary " + overall;
  const summary = {
    ok:    `Routed → ${r.active_label || r.active}`,
    warn:  `Working with warnings`,
    error: `Broken — see node`,
    off:   `OFF`,
  };
  sumEl.textContent = summary[overall] || overall;

  setStripNode("wstrip-provider", r.nodes && r.nodes.provider);
  setStripNode("wstrip-proxy",    r.nodes && r.nodes.proxy);
  setStripNode("wstrip-claude",   r.nodes && r.nodes.claude);
  setStripWire("wstrip-wire-pp",  r.wires && r.wires.provider_proxy);
  setStripWire("wstrip-wire-pc",  r.wires && r.wires.proxy_claude);

  // Stash details so the popover can read them on click.
  _stripDiag = r;
}
let _stripDiag = null;

function setStripNode(id, info) {
  const el = $("#" + id); if (!el) return;
  for (const c of _statusClasses) el.classList.remove(c);
  if (info && info.status) el.classList.add(info.status);
}
function setStripWire(id, info) {
  const el = $("#" + id); if (!el) return;
  for (const c of _statusClasses) el.classList.remove(c);
  if (info && info.status) el.classList.add(info.status);
}

// Click handlers for the strip — open a popover with the detail.
const _stripKeyToTitle = {
  provider: "Provider", proxy: "OneClick proxy", claude: "Claude Code",
  provider_proxy: "Wire: Provider ↔ Proxy", proxy_claude: "Wire: Proxy ↔ Claude Code",
};
document.addEventListener("click", (e) => {
  const node = e.target.closest && e.target.closest("[data-key]");
  const popover = $("#wstrip-popover");
  if (!node || !popover) return;
  if (popover.contains(e.target)) return;
  // Only intercept clicks on wiring-strip nodes/wires.
  if (!node.closest("#wiring-strip")) return;
  const key = node.dataset.key;
  if (!_stripDiag) return;
  const info = (_stripDiag.nodes && _stripDiag.nodes[key]) || (_stripDiag.wires && _stripDiag.wires[key]);
  if (!info) return;
  $("#wpop-title").textContent = _stripKeyToTitle[key] || key;
  const det = $("#wpop-detail");
  det.textContent = info.detail || "—";
  det.className = "wpop-detail " + (info.status || "off");
  // Position near the clicked element.
  const rect = node.getBoundingClientRect();
  popover.style.top = (rect.bottom + 8) + "px";
  popover.style.left = Math.max(12, Math.min(rect.left, window.innerWidth - 380)) + "px";
  popover.classList.remove("hidden");
});
$("#wpop-close").addEventListener("click", () => $("#wstrip-popover").classList.add("hidden"));
$("#wpop-resync").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true; const oldText = btn.textContent; btn.textContent = "Syncing…";
  try {
    const r = await fetch("/api/resync", { method: "POST", headers: { "X-CSRF-Token": csrf } });
    const j = await r.json();
    if (!r.ok || !j.ok) throw new Error(j.error || ("HTTP " + r.status));
    toast("System resynced — open a NEW terminal so Claude Code picks it up", "ok");
    $("#wstrip-popover").classList.add("hidden");
    refreshStrip();
  } catch (err) {
    toast("Resync failed: " + err.message, "error");
  } finally {
    btn.disabled = false; btn.textContent = oldText;
  }
});

// ---- Usage footer --------------------------------------------------------

async function refreshUsage() {
  try {
    const r = await api("GET", "/api/usage?days=1");
    const total = r.total || {};
    const presets = r.presets || {};
    const reqs = total.requests || 0;
    if (!reqs) {
      $("#usage-summary").textContent = "No requests today yet.";
      return;
    }
    const fmt = (n) => n >= 1000 ? (n / 1000).toFixed(1) + "K" : String(n);
    const byPreset = Object.entries(presets)
      .sort(([, a], [, b]) => b.requests - a.requests)
      .map(([name, b]) => `${escape(name)}: ${fmt(b.input)}+${fmt(b.output)} (${b.requests}r)`)
      .slice(0, 3)
      .join(" · ");
    $("#usage-summary").textContent =
      `Today: ${fmt(total.input)} in / ${fmt(total.output)} out · ${reqs} requests · ${byPreset}`;
  } catch (_) { /* ignore */ }
}
$("#usage-refresh").addEventListener("click", refreshUsage);
setInterval(refreshUsage, 30000);
// Collapsible usage card — remembers state in localStorage.
const usageCard = $("#usage-footer");
const usageCollapseBtn = $("#usage-collapse");
function applyUsageCollapsed(collapsed) {
  usageCard.classList.toggle("collapsed", collapsed);
  usageCollapseBtn.textContent = collapsed ? "+" : "—";
  usageCollapseBtn.title = collapsed ? "Expand" : "Minimize";
}
applyUsageCollapsed(localStorage.getItem("coc.usage.collapsed") === "1");
usageCollapseBtn.addEventListener("click", () => {
  const next = !usageCard.classList.contains("collapsed");
  localStorage.setItem("coc.usage.collapsed", next ? "1" : "0");
  applyUsageCollapsed(next);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("#wstrip-popover").classList.add("hidden");
});

// --- Wiring (self-diagnostic) ---------------------------------------------

let _wiringInterval = null;

async function refreshDiagnose() {
  // Auto-poll every 4s while the wiring tab is the active panel.
  if (_wiringInterval) clearInterval(_wiringInterval);
  _wiringInterval = setInterval(() => {
    if (document.querySelector("#panel-wiring.active")) doDiagnose();
    else { clearInterval(_wiringInterval); _wiringInterval = null; }
  }, 4000);
  await doDiagnose();
}

async function doDiagnose() {
  try {
    const r = await api("GET", "/api/diagnose");
    paintDiagnose(r);
  } catch (err) {
    $("#wiring-overall").className = "wiring-overall error";
    $("#wiring-overall").textContent = "diagnostic call failed: " + err.message;
  }
}

function paintDiagnose(r) {
  const overallEl = $("#wiring-overall");
  const overall = r.overall || "off";
  overallEl.className = "wiring-overall " + overall;
  const summary = {
    ok:    `✓ All wired up — Claude Code is routed to ${r.active_label || r.active}${r.active_model ? " (" + r.active_model + ")" : ""}`,
    warn:  `⚠ Mostly working — see the amber section below`,
    error: `✗ Something's broken — see the red section below`,
    off:   `Not currently routing (toggle is OFF or the Anthropic default preset is active)`,
  };
  overallEl.textContent = summary[overall] || overall;

  // Nodes
  setNode("provider", r.nodes && r.nodes.provider);
  setNode("proxy", r.nodes && r.nodes.proxy);
  setNode("claude", r.nodes && r.nodes.claude);
  setWire("provider_proxy", r.wires && r.wires.provider_proxy);
  setWire("proxy_claude", r.wires && r.wires.proxy_claude);

  // Raw JSON for support / curiosity.
  $("#wiring-raw").textContent = JSON.stringify(r, null, 2);
}

const _statusClasses = ["ok", "warn", "error", "off"];

function setNode(id, info) {
  const el = $("#wnode-" + id);
  if (!el) return;
  for (const c of _statusClasses) el.classList.remove(c);
  if (info && info.status) el.classList.add(info.status);
  $(`#wnode-${id}-detail`).textContent = (info && info.detail) || "—";
}

function setWire(id, info) {
  const el = $("#wwire-" + id);
  if (!el) return;
  for (const c of _statusClasses) el.classList.remove(c);
  if (info && info.status) el.classList.add(info.status);
  const detailEl = $(`#wwire-${id}-detail`);
  if (detailEl) detailEl.textContent = (info && info.detail) || "";
}

$("#btn-rediagnose")?.addEventListener("click", doDiagnose);

// --- "Copy to other tools" panel ------------------------------------------

function renderExportSnippets() {
  const p = getSelected();
  if (!p) return;
  const base = (p.base_url || "").replace(/\/$/, "");
  const model = p.model || "";
  const small = p.small_fast_model || model;
  // The browser doesn't have access to the saved API key (server redacts).
  // Use a placeholder that the user replaces — it's safer than echoing the
  // key into the DOM anyway.
  const KEY = "<your-api-key>";
  const prov = (p.label || p.name);

  $("#export-roo").textContent = JSON.stringify({
    "roo-cline.apiProvider": "openai",
    "roo-cline.openAiBaseUrl": base,
    "roo-cline.openAiApiKey": KEY,
    "roo-cline.openAiModelId": model,
  }, null, 2);

  $("#export-cline").textContent = JSON.stringify({
    "cline.apiProvider": "openai",
    "cline.openAiBaseUrl": base,
    "cline.openAiApiKey": KEY,
    "cline.openAiModelId": model,
  }, null, 2);

  $("#export-continue").textContent = JSON.stringify({
    "title": prov,
    "provider": "openai",
    "model": model,
    "apiKey": KEY,
    "apiBase": base,
  }, null, 2);

  $("#export-cursor").textContent =
    `Custom OpenAI API\n` +
    `Base URL: ${base}\n` +
    `API key: ${KEY}\n` +
    `Model: ${model}\n` +
    `Verify "Custom" is selected as the model provider.`;

  $("#export-codex").textContent =
    `# OpenAI Python SDK / Codex CLI / generic OPENAI_*\n` +
    `export OPENAI_API_KEY="${KEY}"\n` +
    `export OPENAI_BASE_URL="${base}/v1"\n` +
    `export OPENAI_MODEL="${model}"`;

  $("#export-aider").textContent =
    `export OPENAI_API_KEY="${KEY}"\n` +
    `export OPENAI_API_BASE="${base}/v1"\n` +
    `aider --model openai/${model}`;

  $("#export-shell").textContent =
    `# Anthropic-flavored (Claude Code, Claude SDK)\n` +
    `export ANTHROPIC_BASE_URL="http://127.0.0.1:${state.proxy.port}"\n` +
    `export ANTHROPIC_AUTH_TOKEN="claude-oneclick"\n` +
    `export ANTHROPIC_MODEL="${model}"\n` +
    `export ANTHROPIC_SMALL_FAST_MODEL="${small}"\n\n` +
    `# OpenAI-flavored (everything else)\n` +
    `export OPENAI_API_KEY="${KEY}"\n` +
    `export OPENAI_BASE_URL="${base}/v1"\n` +
    `export OPENAI_MODEL="${model}"`;
}

// Copy-to-clipboard buttons.
document.addEventListener("click", async (e) => {
  const btn = e.target.closest && e.target.closest("[data-copy]");
  if (!btn) return;
  const target = $("#" + btn.dataset.copy);
  if (!target) return;
  try {
    await navigator.clipboard.writeText(target.textContent);
    const original = btn.textContent;
    btn.textContent = "✓ Copied";
    setTimeout(() => { btn.textContent = original; }, 1400);
  } catch (err) {
    toast("Copy failed: " + err.message, "error");
  }
});

// --- settings dialog -------------------------------------------------------

const dlg = $("#settings-dialog");
async function populateVersionInfo() {
  const info = $("#set-version-info");
  const status = $("#set-update-status");
  info.textContent = "loading…";
  status.textContent = "";
  try {
    const r = await api("GET", "/api/update/check");
    const cur = (r.current_sha || "").slice(0, 7);
    const branchLabel = r.branch ? ` · branch: ${escape(r.branch)}` : "";
    info.textContent = `${r.version || "0.0.0"}${cur ? " · " + cur : ""}${branchLabel}${r.is_git ? "" : " (not a git checkout)"}`;
    if (r.error) {
      status.textContent = r.error;
    } else if (r.has_update) {
      const ahead = r.ahead_by ? ` (${r.ahead_by} commits ahead)` : "";
      status.innerHTML = `<b class="warn-text">Update available</b> — ${(r.latest_sha || "").slice(0, 7)}${ahead}${r.latest_message ? ` — “${escape(r.latest_message)}”` : ""}`;
    } else {
      status.textContent = "You're up to date.";
    }
  } catch (err) {
    info.textContent = "error";
    status.textContent = err.message;
  }
}

$("#set-update-check").addEventListener("click", async () => {
  const btn = $("#set-update-check");
  const apply = $("#set-update-apply");
  btn.disabled = true; btn.textContent = "Checking…";
  apply.classList.add("hidden");
  try {
    const r = await api("GET", "/api/update/check?force=1");
    const cur = (r.current_sha || "").slice(0, 7);
    $("#set-version-info").textContent = `${r.version}${cur ? " · " + cur : ""}${r.is_git ? "" : " (not a git checkout — self-update disabled)"}`;
    if (r.has_update) {
      const latest = (r.latest_sha || "").slice(0, 7);
      const msg = r.latest_message ? ` · "${escape(r.latest_message)}"` : "";
      $("#set-update-status").innerHTML = `<b class="warn-text">Update available</b> — ${cur} → ${latest}${msg}`;
      apply.classList.remove("hidden");
      // Also unblock the topbar banner if the user previously dismissed
      // this same SHA.
      localStorage.removeItem(UPDATE_DISMISSED_KEY);
      checkForUpdate(true);
    } else if (r.error) {
      $("#set-update-status").textContent = r.error;
    } else {
      $("#set-update-status").innerHTML = '<b style="color: var(--accent-2)">✓ You\'re up to date</b>';
    }
  } catch (err) {
    $("#set-update-status").textContent = err.message;
  } finally {
    btn.disabled = false; btn.textContent = "Check for updates";
  }
});

$("#set-update-apply").addEventListener("click", async () => {
  const apply = $("#set-update-apply");
  const ok = await confirmDialog(
    "Apply update?",
    "This runs `git pull --ff-only` followed by `pip install --user -e .`. Anything in flight finishes first; the proxy is restarted after.",
    "Update now"
  );
  if (!ok) return;
  apply.disabled = true; apply.textContent = "Updating…";
  $("#set-update-status").textContent = "running git pull + pip install…";
  try {
    const r = await api("POST", "/api/update/apply", {});
    if (r.ok) {
      $("#set-update-status").innerHTML = '<b style="color: var(--accent-2)">✓ Updated. Reloading…</b>';
      setTimeout(() => location.reload(), 1500);
    } else {
      $("#set-update-status").textContent = r.error || "update failed";
      apply.disabled = false; apply.textContent = "Update now";
    }
  } catch (err) {
    $("#set-update-status").textContent = err.message;
    apply.disabled = false; apply.textContent = "Update now";
  }
});

$("#btn-settings").addEventListener("click", async () => {
  // Show the dialog FIRST. If any of the populate calls below blow up
  // (e.g. the running server is older than the UI bundle and a new
  // /api/* endpoint returns 404), the dialog must still be visible —
  // a non-fatal error somewhere shouldn't lock the user out of the
  // settings.
  try { dlg.showModal(); } catch (_) { /* already open */ }

  // Populate from current state. Each step is wrapped: a failing
  // network call updates that one row's status text and continues.
  try {
    $("#set-ui-port").value = state.ui.port || 47823;
    $("#set-proxy-port").value = state.proxy.port || 47824;
    $("#set-theme").value = state.ui.theme || "auto";
    $("#set-log_level").value = state.log_level || "info";
    $("#set-autostart").checked = !!state.autostart_proxy;
    $("#set-open_browser").checked = !!state.ui.open_browser_on_launch;
    $("#set-skip_login").checked = !!state.skip_vscode_login;
    $("#set-cache_ttl").value = (state.model_discovery && state.model_discovery.cache_ttl_seconds) || 600;
  } catch (err) { console.warn("settings populate (state) failed:", err); }

  try {
    const a = await api("GET", "/api/autostart");
    $("#set-autostart-boot").checked = !!a.enabled;
    $("#set-autostart-where").textContent = a.location ? `(${a.location})` : "";
  } catch (_) {
    $("#set-autostart-boot").checked = false;
    $("#set-autostart-where").textContent = "(server didn't expose /api/autostart — try restarting `claude-oneclick ui`)";
  }
  populateVersionInfo().catch(e => console.warn("version info:", e));
  populateDesktopStatus().catch(e => console.warn("desktop status:", e));
  populateKeychainStatus().catch(e => console.warn("keychain status:", e));
});

async function populateKeychainStatus() {
  try {
    const r = await api("GET", "/api/keychain/status");
    $("#set-use-keychain").checked = !!r.enabled;
    $("#set-keychain-status").textContent = r.available
      ? `Backend: ${r.backend}${r.enabled ? " (enabled)" : " (available — toggle on to use)"}`
      : `No keychain backend on this OS (${r.backend}). Falls back to plaintext config.json.`;
    $("#set-use-keychain").disabled = !r.available;
  } catch (err) {
    $("#set-keychain-status").textContent = err.message;
  }
}

$("#set-use-keychain").addEventListener("change", async (e) => {
  try {
    await api("POST", "/api/keychain/toggle", { enabled: e.target.checked });
    toast(e.target.checked ? "Keychain mode enabled" : "Keychain mode disabled");
    populateKeychainStatus();
  } catch (err) {
    toast(err.message, "error");
    e.target.checked = !e.target.checked;
  }
});

async function populateDesktopStatus() {
  try {
    const s = await api("GET", "/api/desktop/status");
    $("#set-desktop-dev").checked = !!s.enabled;
    const where = s.config_path ? `config: ${s.config_path}` : `(${s.detail || "no config file detected"})`;
    $("#set-desktop-status").textContent = where;
  } catch (err) {
    $("#set-desktop-status").textContent = err.message;
  }
}

$("#set-desktop-dev").addEventListener("change", async (e) => {
  const enabled = e.target.checked;
  if (enabled) {
    const ok = await confirmDialog(
      "Route Claude Desktop's chat UI to OneClick?",
      "This writes Claude Desktop's developer config (third-party inference) to point at the local proxy. Anthropic's chat features that depend on the official endpoint will be disabled. Reverting restores your previous config exactly.",
      "Enable"
    );
    if (!ok) { e.target.checked = false; return; }
  }
  try {
    const r = await api("POST", "/api/desktop/toggle", { enabled });
    if (r.ok) {
      toast(enabled ? "Claude Desktop pointed at OneClick" : "Claude Desktop reverted");
      populateDesktopStatus();
    } else {
      toast(r.error || "failed", "error");
      e.target.checked = !enabled;
    }
  } catch (err) {
    toast(err.message, "error");
    e.target.checked = !enabled;
  }
});

// ---- Secret developer menu (5 fast clicks on the version label) ----------

let _secretClicks = 0;
let _secretTimer = null;
$("#set-version-info").addEventListener("click", () => {
  _secretClicks++;
  if (_secretTimer) clearTimeout(_secretTimer);
  _secretTimer = setTimeout(() => { _secretClicks = 0; }, 1200);
  if (_secretClicks >= 5) {
    _secretClicks = 0;
    $("#settings-secret").classList.toggle("hidden");
    toast($("#settings-secret").classList.contains("hidden")
      ? "Developer menu hidden"
      : "⚠ Developer menu unlocked");
  }
});

$("#btn-save-settings").addEventListener("click", async (e) => {
  e.preventDefault();
  const body = {
    log_level: $("#set-log_level").value,
    autostart_proxy: $("#set-autostart").checked,
    skip_vscode_login: $("#set-skip_login").checked,
    ui: {
      port: Number($("#set-ui-port").value) || 47823,
      theme: $("#set-theme").value,
      open_browser_on_launch: $("#set-open_browser").checked,
    },
    proxy: { port: Number($("#set-proxy-port").value) || 47824 },
    model_discovery: { cache_ttl_seconds: Number($("#set-cache_ttl").value) || 600 },
  };
  try {
    await api("POST", "/api/settings", body);
    // Boot-time autostart toggle goes through its own endpoint because
    // it's a per-OS persistence side-effect, not config.json content.
    await api("POST", "/api/autostart", { enabled: $("#set-autostart-boot").checked });
    dlg.close();
    await refresh();
    toast("Settings saved");
  }
  catch (err) { toast(err.message, "error"); }
});

$("#btn-export").addEventListener("click", async () => {
  try {
    const cfg = await api("GET", "/api/export");
    const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "claude-oneclick-config.json";
    a.click();
  } catch (err) { toast(err.message, "error"); }
});

$("#btn-import").addEventListener("click", () => {
  const inp = document.createElement("input");
  inp.type = "file"; inp.accept = "application/json";
  inp.onchange = async () => {
    const file = inp.files[0]; if (!file) return;
    const text = await file.text();
    try {
      const obj = JSON.parse(text);
      await api("POST", "/api/import", obj);
      await refresh();
      toast("Imported");
    } catch (err) { toast(err.message, "error"); }
  };
  inp.click();
});

// --- form collection -------------------------------------------------------

function collectForm(name) {
  const num = (id) => { const v = $(id).value; return v === "" ? null : Number(v); };
  return {
    name,
    label: $("#f-label").value || name,
    base_url: $("#f-base_url").value.trim(),
    model: $("#f-model").value.trim(),
    small_fast_model: $("#f-small_fast_model").value.trim(),
    format: document.querySelector('input[name=format]:checked')?.value || "openai",
    notes: $("#f-notes").value,
    extra_headers: collectKVs("#headers", "hk", "hv"),
    model_aliases: collectKVs("#aliases", "ak", "av"),
    sampling: {
      temperature: num("#f-s-temperature"),
      top_p: num("#f-s-top_p"),
      top_k: num("#f-s-top_k"),
      max_tokens: num("#f-s-max_tokens"),
    },
    request_timeout_seconds: num("#f-request_timeout_seconds"),
    retries: num("#f-retries"),
    retry_backoff: num("#f-retry_backoff"),
    system_prompt_prefix: $("#f-system_prompt_prefix").value,
    system_prompt_suffix: $("#f-system_prompt_suffix").value,
    disable_streaming: $("#f-disable_streaming").checked,
    prompt_cache_passthrough: $("#f-prompt_cache_passthrough").checked,
    reasoning_enabled: $("#f-reasoning_enabled").checked,
    reasoning_effort: reasoningEffortName($("#f-reasoning_effort").value),
    extra_body: parseExtraBody(),
  };
}

function parseExtraBody() {
  const raw = ($("#f-extra_body").value || "").trim();
  if (!raw) return {};
  try {
    const obj = JSON.parse(raw);
    if (obj && typeof obj === "object" && !Array.isArray(obj)) return obj;
    throw new Error("must be a JSON object");
  } catch (e) {
    throw new Error("Extra request body: " + e.message);
  }
}

// --- keyboard shortcut: Space toggles -------------------------------------

window.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
    e.preventDefault();
    $("#toggle").checked = !$("#toggle").checked;
    $("#toggle").dispatchEvent(new Event("change"));
  }
});

// --- onboarding wizard ----------------------------------------------------

const WIZ_DISMISSED_KEY = "coc.wizard_dismissed";
let wizSelectedPreset = null;

function shouldShowWizard() {
  if (localStorage.getItem(WIZ_DISMISSED_KEY) === "1") return false;
  if (!state) return false;
  // If the user already has a non-default active preset with a key set, they don't need it.
  const active = state.presets.find(p => p.name === state.active);
  if (active && active.api_key_set && state.active !== "anthropic") return false;
  // If they've already created any custom preset, also skip.
  if (state.presets.some(p => !p.builtin)) return false;
  return true;
}

function showWizard() {
  $("#onboarding").classList.remove("hidden");
  hideBanner();
}
function hideWizard() {
  $("#onboarding").classList.add("hidden");
}

function showBanner() {
  if (localStorage.getItem(WIZ_DISMISSED_KEY) === "1" && !shouldShowBannerForState()) {
    $("#banner").classList.add("hidden");
    return;
  }
  if (shouldShowBannerForState()) {
    $("#banner").classList.remove("hidden");
  } else {
    $("#banner").classList.add("hidden");
  }
}
function hideBanner() { $("#banner").classList.add("hidden"); }
function shouldShowBannerForState() {
  if (!state) return false;
  if (state.active === "anthropic") {
    $("#banner-text").textContent = "You're on the default Anthropic preset. Pick a custom provider to route Claude Code through.";
    return true;
  }
  const active = state.presets.find(p => p.name === state.active);
  if (active && !active.api_key_set) {
    $("#banner-text").textContent = `Add an API key for "${active.label || active.name}" to start using it.`;
    return true;
  }
  return false;
}

function gotoStep(n) {
  for (const s of $$(".wizard-step")) s.classList.toggle("active", Number(s.dataset.step) === n);
  for (const i of $$(".step-indicator .step")) {
    const idx = Array.prototype.indexOf.call(i.parentNode.children, i) + 1;
    i.classList.toggle("active", idx <= n);
  }
}

function renderWizardProviders() {
  const grid = $("#wiz-providers");
  grid.innerHTML = "";
  const featured = state.presets.filter(p => p.builtin && p.name !== "anthropic");
  for (const p of featured) {
    const card = document.createElement("div");
    card.className = "provider-card";
    card.dataset.name = p.name;
    const tags = (p.tags || []).map(t => `<span class="tagchip">${escape(t)}</span>`).join("");
    card.innerHTML = `
      <div class="pname-row">
        <span class="pname">${escape(p.label || p.name)}</span>
        <span class="ptags">${tags}</span>
      </div>
      <div class="psub">${escape(p.subtitle || "")}</div>
      <div class="purl">${escape(p.base_url || "")}</div>
    `;
    card.onclick = () => {
      wizSelectedPreset = p.name;
      for (const c of $$(".provider-card")) c.classList.toggle("selected", c.dataset.name === p.name);
      $("#wiz-next-2").disabled = false;
      // Tailor the API-key hint by base_url so we don't have to keep a
      // hardcoded list in sync with preset names.
      const KEY_HINTS_BY_HOST = {
        "api.deepseek.com": "Get a DeepSeek key at platform.deepseek.com → API Keys.",
        "integrate.api.nvidia.com": "Get an NVIDIA key at build.nvidia.com → your account → API keys.",
        "openrouter.ai": "Get an OpenRouter key at openrouter.ai/keys.",
        "api.groq.com": "Get a Groq key at console.groq.com/keys.",
        "api.together.xyz": "Get a Together AI key at api.together.xyz/settings/api-keys.",
        "api.fireworks.ai": "Get a Fireworks key at fireworks.ai/account/api-keys.",
        "localhost": "Local Ollama needs no API key — you can leave this blank.",
        "127.0.0.1": "Local Ollama needs no API key — you can leave this blank.",
      };
      let host = "";
      try { host = new URL(p.base_url).hostname; } catch (_) {}
      $("#wiz-key-hint").textContent = KEY_HINTS_BY_HOST[host] || "Paste your provider's API key.";
      $("#wiz-preset-name").textContent = p.label || p.name;
    };
    grid.appendChild(card);
  }
}

// ---- Wizard step 4: live model picker ------------------------------------

async function loadWizardModels() {
  const status = $("#wiz-model-status");
  const main = $("#wiz-main-model");
  const small = $("#wiz-small-model");
  main.innerHTML = ""; small.innerHTML = "";
  if (!wizSelectedPreset) return;
  const preset = state.presets.find(p => p.name === wizSelectedPreset) || {};
  const defaultMain = preset.model || "";
  const defaultSmall = preset.small_fast_model || preset.model || "";

  // Save the API key first so /api/models can authenticate.
  const key = $("#wiz-api-key").value.trim();
  if (key) {
    try { await api("POST", "/api/keys", { name: wizSelectedPreset, api_key: key }); }
    catch (_) {}
  }

  status.textContent = "Loading models from " + (preset.base_url || "provider") + "…";
  let models = [];
  try {
    const r = await api("GET", `/api/models?name=${encodeURIComponent(wizSelectedPreset)}&force=1`);
    models = r.models || [];
    status.textContent = `${models.length} model${models.length === 1 ? "" : "s"} returned by the provider.`;
  } catch (err) {
    status.textContent = `Couldn't fetch live model list (${err.message}). Falling back to the preset's defaults — you can refresh later on the Models tab.`;
    if (defaultMain) models.push(defaultMain);
    if (defaultSmall && !models.includes(defaultSmall)) models.push(defaultSmall);
  }

  // Populate dropdowns. Pre-select the preset's defaults if present;
  // otherwise pick the first/last as sensible fallbacks.
  for (const m of models) {
    main.appendChild(new Option(m, m));
    small.appendChild(new Option(m, m));
  }
  if (models.includes(defaultMain)) main.value = defaultMain;
  if (models.includes(defaultSmall)) small.value = defaultSmall;
  // If the dropdowns ended up empty (provider returned nothing AND no
  // defaults), let the user type a value.
  if (!models.length) {
    for (const sel of [main, small]) {
      sel.outerHTML = sel.outerHTML.replace("<select", '<input list="wiz-models-list"').replace("</select>", "");
    }
  }
  $("#wiz-model-summary").textContent =
    `${main.value || "(none)"} + ${small.value || main.value || "(none)"}`;
}

// Update the summary live as the user changes selections.
document.addEventListener("change", (e) => {
  if (e.target && (e.target.id === "wiz-main-model" || e.target.id === "wiz-small-model")) {
    const m = $("#wiz-main-model").value;
    const s = $("#wiz-small-model").value || m;
    $("#wiz-model-summary").textContent = `${m || "(none)"} + ${s || "(none)"}`;
  }
});

for (const b of $$("[data-next]")) b.addEventListener("click", () => {
  const target = Number(b.dataset.next);
  if (target === 2 && !$("#wiz-providers").children.length) renderWizardProviders();
  if (target === 4) loadWizardModels();
  gotoStep(target);
});
for (const b of $$("[data-prev]")) b.addEventListener("click", () => gotoStep(Number(b.dataset.prev)));

$("#wiz-skip").addEventListener("click", () => { localStorage.setItem(WIZ_DISMISSED_KEY, "1"); hideWizard(); showBanner(); });

$("#wiz-finish").addEventListener("click", async () => {
  if (!wizSelectedPreset) return;
  try {
    // Save the API key (re-save on finish in case the user edited step 3).
    const key = $("#wiz-api-key").value;
    if (key) await api("POST", "/api/keys", { name: wizSelectedPreset, api_key: key });
    // Persist the model picks if the user changed them in step 4.
    const mainEl = $("#wiz-main-model"); const smallEl = $("#wiz-small-model");
    const mainVal = (mainEl && mainEl.value) || "";
    const smallVal = (smallEl && smallEl.value) || mainVal;
    if (mainVal) {
      await api("POST", "/api/preset", {
        name: wizSelectedPreset,
        model: mainVal,
        small_fast_model: smallVal,
      });
    }
    await api("POST", "/api/use", { name: wizSelectedPreset });
    await api("POST", "/api/toggle", { enabled: true });
    localStorage.setItem(WIZ_DISMISSED_KEY, "1");
    hideWizard();
    selected = wizSelectedPreset;
    await refresh();
    toast(`Routed to ${$("#active-pill").textContent}. Open a new terminal to use it.`);
  } catch (err) { toast(err.message, "error"); }
});

$("#banner-cta").addEventListener("click", () => { localStorage.removeItem(WIZ_DISMISSED_KEY); showWizard(); });
$("#banner-dismiss").addEventListener("click", () => { localStorage.setItem(WIZ_DISMISSED_KEY, "1"); hideBanner(); });

// ---- Update checker -----------------------------------------------------

const UPDATE_DISMISSED_KEY = "coc.update_dismissed_sha";

async function checkForUpdate(force = false) {
  try {
    const info = await api("GET", `/api/update/check${force ? "?force=1" : ""}`);
    const dismissed = localStorage.getItem(UPDATE_DISMISSED_KEY) || "";
    if (info.has_update && info.latest_sha && info.latest_sha !== dismissed) {
      const short = info.latest_sha.slice(0, 7);
      const cur = (info.current_sha || "").slice(0, 7);
      const msg = info.latest_message ? ` — "${info.latest_message}"` : "";
      $("#update-text").innerHTML = `<b>Update available</b> (${cur} → ${short})${escape(msg)}`;
      $("#update-banner").classList.remove("hidden");
      $("#update-banner").dataset.sha = info.latest_sha;
    } else {
      $("#update-banner").classList.add("hidden");
    }
    return info;
  } catch (_) {
    return null;
  }
}

$("#update-dismiss").addEventListener("click", () => {
  const sha = $("#update-banner").dataset.sha;
  if (sha) localStorage.setItem(UPDATE_DISMISSED_KEY, sha);
  $("#update-banner").classList.add("hidden");
});

$("#update-apply").addEventListener("click", async () => {
  const ok = await confirmDialog(
    "Apply update?",
    "This runs `git pull` and re-installs the package. Anything in flight " +
    "(an open Claude Code session, a streaming proxy request) finishes first; " +
    "the proxy restarts after.",
    "Update now"
  );
  if (!ok) return;
  $("#update-apply").disabled = true;
  $("#update-text").textContent = "Updating…";
  try {
    const r = await api("POST", "/api/update/apply", {});
    if (r.ok) {
      toast("Updated. Reloading the UI…");
      setTimeout(() => location.reload(), 1200);
    } else {
      toast(r.error || "update failed", "error");
      $("#update-apply").disabled = false;
    }
  } catch (err) {
    toast(err.message, "error");
    $("#update-apply").disabled = false;
  }
});

// --- bootstrap -------------------------------------------------------------

(async () => {
  try {
    await refresh();
    if (shouldShowWizard()) showWizard();
    else showBanner();
    // Kick off an update check on first load (cached server-side for 1h).
    checkForUpdate(false);
    // Persistent wiring strip — paint once now, then poll every 6s.
    doStripDiagnose();
    startStripPolling();
    // Usage footer — paint once.
    refreshUsage();
  } catch (err) { toast(err.message, "error"); }
})();
setInterval(() => refresh(false).catch(() => {}), 5000);
// Re-check for updates every 6 hours.
setInterval(() => checkForUpdate(false).catch(() => {}), 6 * 60 * 60 * 1000);

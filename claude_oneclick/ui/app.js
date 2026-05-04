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
  const active = state.presets.find(p => p.name === state.active);
  $("#active-pill").textContent = active ? (active.label || active.name) : "(none)";
}

function renderSidebar() {
  const ul = $("#preset-list");
  ul.innerHTML = "";
  for (const p of state.presets) {
    const li = document.createElement("li");
    li.className = (p.name === selected ? "selected " : "") + (p.name === state.active ? "active" : "");
    li.innerHTML = `
      <span class="preset-name">${escape(p.label || p.name)}</span>
      <span class="preset-base">${escape(p.base_url || (p.format === "anthropic" ? "(anthropic default)" : "(no base URL)"))}</span>
      <span class="badge">${p.builtin ? "built-in" : "custom"}${p.api_key_set ? " · key set" : ""}</span>
    `;
    li.onclick = () => { selected = p.name; renderSidebar(); renderDetail(); };
    ul.appendChild(li);
  }
}

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
  try { await api("POST", "/api/keys", { name: p.name, api_key: key }); $("#f-api_key").value = ""; await refresh(); toast("API key saved"); }
  catch (err) { toast(err.message, "error"); }
});

$("#btn-delete").addEventListener("click", async () => {
  const p = getSelected(); if (!p || p.builtin) return;
  if (!confirm(`Delete preset '${p.name}'?`)) return;
  try { await api("DELETE", "/api/preset/" + encodeURIComponent(p.name)); selected = null; await refresh(); toast("Deleted"); }
  catch (err) { toast(err.message, "error"); }
});

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
  });
}

// --- settings dialog -------------------------------------------------------

const dlg = $("#settings-dialog");
$("#btn-settings").addEventListener("click", () => {
  $("#set-ui-port").value = state.ui.port || 47823;
  $("#set-proxy-port").value = state.proxy.port || 47824;
  $("#set-theme").value = state.ui.theme || "auto";
  $("#set-log_level").value = state.log_level || "info";
  $("#set-autostart").checked = !!state.autostart_proxy;
  $("#set-open_browser").checked = !!state.ui.open_browser_on_launch;
  $("#set-skip_login").checked = !!state.skip_vscode_login;
  $("#set-cache_ttl").value = (state.model_discovery && state.model_discovery.cache_ttl_seconds) || 600;
  dlg.showModal();
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
  try { await api("POST", "/api/settings", body); dlg.close(); await refresh(); toast("Settings saved"); }
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
  };
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
    card.innerHTML = `<div class="pname">${escape(p.label || p.name)}</div><div class="pmeta">${escape(p.base_url || "")}</div>`;
    card.onclick = () => {
      wizSelectedPreset = p.name;
      for (const c of $$(".provider-card")) c.classList.toggle("selected", c.dataset.name === p.name);
      $("#wiz-next-2").disabled = false;
      // Tailor the API-key hint by provider.
      const hints = {
        "deepseek": "Get a DeepSeek key at platform.deepseek.com → API Keys.",
        "deepseek-v4-pro": "Get a DeepSeek key at platform.deepseek.com → API Keys. Pro is the larger reasoning model.",
        "deepseek-v4-flash": "Get a DeepSeek key at platform.deepseek.com → API Keys. Flash is the smaller, faster reasoning model.",
        "deepseek-reasoner": "Get a DeepSeek key at platform.deepseek.com → API Keys.",
        "nvidia-nims-llama": "Get an NVIDIA key at build.nvidia.com → your account → API keys.",
        "nvidia-nims-nemotron": "Get an NVIDIA key at build.nvidia.com → your account → API keys.",
        "nvidia-nims-deepseek-r1": "Get an NVIDIA key at build.nvidia.com → your account → API keys.",
        "openrouter": "Get an OpenRouter key at openrouter.ai/keys.",
        "groq": "Get a Groq key at console.groq.com/keys.",
        "together": "Get a Together AI key at api.together.xyz/settings/api-keys.",
        "fireworks": "Get a Fireworks key at fireworks.ai/account/api-keys.",
        "ollama": "Local Ollama needs no API key — you can leave this blank.",
      };
      $("#wiz-key-hint").innerHTML = hints[p.name] || `Paste your provider's API key.`;
      $("#wiz-preset-name").textContent = p.label || p.name;
    };
    grid.appendChild(card);
  }
}

for (const b of $$("[data-next]")) b.addEventListener("click", () => {
  const target = Number(b.dataset.next);
  if (target === 2 && !$("#wiz-providers").children.length) renderWizardProviders();
  gotoStep(target);
});
for (const b of $$("[data-prev]")) b.addEventListener("click", () => gotoStep(Number(b.dataset.prev)));

$("#wiz-skip").addEventListener("click", () => { localStorage.setItem(WIZ_DISMISSED_KEY, "1"); hideWizard(); showBanner(); });

$("#wiz-finish").addEventListener("click", async () => {
  if (!wizSelectedPreset) return;
  try {
    const key = $("#wiz-api-key").value;
    if (key) await api("POST", "/api/keys", { name: wizSelectedPreset, api_key: key });
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

// --- bootstrap -------------------------------------------------------------

(async () => {
  try {
    await refresh();
    if (shouldShowWizard()) showWizard();
    else showBanner();
  } catch (err) { toast(err.message, "error"); }
})();
setInterval(() => refresh(false).catch(() => {}), 5000);

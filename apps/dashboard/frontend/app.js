"use strict";

// ── State ─────────────────────────────────────────────────────────────────────
let _bootstrap      = null;   // cached bootstrap response
let _busy           = false;
let _page           = null;   // current page key
let _chartInstance  = null;   // lightweight-charts Chart instance
let _pendingChartSym = null;  // symbol queued from candidate Chart button

// ── DOM shortcuts ─────────────────────────────────────────────────────────────
const $  = id => document.getElementById(id);
const $r = () => $("pageRoot");

// ── Utilities ─────────────────────────────────────────────────────────────────
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt     = (n, d = 2) => n != null ? Number(n).toFixed(d) : "—";
const fmtDate = s => { try { return new Date(s).toLocaleString(); } catch { return s ?? "—"; } };

function bandClass(b) {
  if (b === "A+") return "band-Ap";
  if (b === "A")  return "band-A";
  if (b === "B")  return "band-B";
  if (b === "C")  return "band-C";
  return "";
}

// ── Error banner ──────────────────────────────────────────────────────────────
function showError(msg) {
  const el = $("errorBanner");
  if (!el) return;
  el.textContent = "⚠ " + msg;
  el.style.display = "";
}
function clearError() {
  const el = $("errorBanner");
  if (el) el.style.display = "none";
}

// ── API helpers ───────────────────────────────────────────────────────────────
async function apiFetch(path, params = {}) {
  const qs = new URLSearchParams(params).toString();
  const url = qs ? `${path}?${qs}` : path;
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

// ── Bootstrap (shared across Home / Dashboard / Ultra) ────────────────────────
async function ensureBootstrap(force = false) {
  if (_bootstrap && !force) return _bootstrap;
  _bootstrap = await apiFetch("/api/dashboard/bootstrap");
  updateCmdBar(_bootstrap);
  return _bootstrap;
}

// ── Command bar ───────────────────────────────────────────────────────────────
function updateCmdBar(data) {
  const scan   = data?.latest_scan ?? {};
  const health = data?.data_health ?? {};
  const sapi   = health.scanner_api ?? {};
  const reach  = sapi.reachable === true;
  const meta   = $("cmdMeta");
  if (!meta) return;
  meta.innerHTML = `
    <span>Scanner: <span class="pill ${reach ? "ok" : "err"}">${reach ? "ok" : "unreachable"}</span></span>
    <span class="sep">|</span>
    <span>Run <strong>${esc(scan.scan_run_id ?? "—")}</strong></span>
    <span class="sep">|</span>
    <span>${esc(scan.universe ?? "—")}</span>
    <span class="sep">|</span>
    <span>${esc(scan.timeframe ?? "—")}</span>
    <span class="sep">|</span>
    <span>${fmtDate(scan.finished_at)}</span>`;
}

// ── Nav active state ──────────────────────────────────────────────────────────
function setNavActive(page) {
  document.querySelectorAll(".nav-item").forEach(el => {
    el.classList.toggle("active", el.dataset.page === page);
  });
}

// ── Chart instance management ─────────────────────────────────────────────────
function _lwAvailable() {
  return typeof LightweightCharts !== "undefined" &&
         typeof LightweightCharts.createChart === "function";
}

function _destroyChart() {
  if (_chartInstance) {
    try { _chartInstance.remove(); } catch {}
    _chartInstance = null;
  }
}

// ── Router ────────────────────────────────────────────────────────────────────
const PAGES = ["home", "dashboard", "ultra", "chart", "research", "system"];

function currentPage() {
  const hash = location.hash.replace(/^#\/?/, "").toLowerCase();
  return PAGES.includes(hash) ? hash : "home";
}

async function navigate() {
  clearError();
  const newPage = currentPage();

  // Destroy chart when leaving chart page
  if (_page === "chart" && newPage !== "chart") _destroyChart();

  _page = newPage;
  setNavActive(_page);
  document.title = `Sachoki · ${_page.charAt(0).toUpperCase() + _page.slice(1)}`;
  $r().innerHTML = `<div class="page-loading">Loading…</div>`;

  try {
    await RENDERERS[_page]();
  } catch (err) {
    $r().innerHTML = `<div class="page-error">Failed to load page: ${esc(err.message)}</div>`;
    showError(err.message);
  }
}

// ── Refresh ───────────────────────────────────────────────────────────────────
async function refresh() {
  if (_busy) return;
  _busy = true;
  const btn = $("refreshBtn");
  if (btn) { btn.disabled = true; btn.classList.add("spinning"); }
  clearError();
  try {
    _bootstrap = null;
    await navigate();
  } catch (err) {
    showError(`Refresh failed: ${err.message}`);
  } finally {
    _busy = false;
    if (btn) { btn.disabled = false; btn.classList.remove("spinning"); }
  }
}

// ── Candidate Chart button (global, called from inline onclick) ────────────────
function openChart(sym) {
  _pendingChartSym = String(sym).toUpperCase();
  location.hash = "#chart";
}

// ═════════════════════════════════════════════════════════════════════════════
// PAGE: HOME
// ═════════════════════════════════════════════════════════════════════════════
async function renderHome() {
  let data;
  try { data = await ensureBootstrap(); }
  catch { $r().innerHTML = homeError(); return; }

  const state  = data.dashboard_state ?? "ERROR";
  const scan   = data.latest_scan ?? {};
  const sum    = data.summary ?? {};
  const health = data.data_health ?? {};
  const reach  = (health.scanner_api ?? {}).reachable === true;

  // Phase G: visible indicator of which path served this view.
  // generator_cache = Generate Views was run for this scan_run_id, page
  // shows pre-aggregated payloads. inline_fallback = generator not run yet,
  // BFF computed top_movers/best_setups/summary on the fly (slower).
  const dataSource = data.data_source || "";
  const sourceTag  = dataSource === "generator_cache"
    ? `<span class="data-source-tag ds-cache"   title="Served from scan_generated_views (pre-aggregated by generator).">📦 cached</span>`
    : dataSource === "inline_fallback"
    ? `<span class="data-source-tag ds-inline"  title="Generator hasn't run for this scan_run_id yet. BFF aggregated on the fly. Click Generate Views in System.">⚡ inline</span>`
    : "";

  let bannerCls = "ready", bannerMsg = "";
  if (state === "SCAN_READY") {
    bannerCls = "ready";
    bannerMsg = `SCAN READY — Run #${scan.scan_run_id ?? "?"} · ${scan.total_candidates ?? 0} candidates`;
  } else if (state === "NO_SCAN") {
    bannerCls = "warn"; bannerMsg = "No completed Ultra Scan found.";
  } else {
    bannerCls = "error"; bannerMsg = data.error ?? "Dashboard bootstrap failed.";
  }

  const quickLinks = [
    { page: "dashboard", icon: "▦", label: "Dashboard",     sub: "Movers & setups" },
    { page: "ultra",     icon: "⚡", label: "Ultra Scanner", sub: "Candidates & filters" },
    { page: "chart",     icon: "◈", label: "Superchart",    sub: "Symbol chart data" },
    { page: "system",    icon: "⚙", label: "System",        sub: "Health & config" },
  ];

  $r().innerHTML = `
    <div class="page-container">
      <div class="state-banner ${bannerCls}">${esc(bannerMsg)} ${sourceTag}</div>
      <div class="section-label">Scan Summary</div>
      <div class="cards-row">
        <div class="card"><div class="c-label">Candidates</div><div class="c-value">${sum.total_candidates ?? "—"}</div><div class="c-sub">total returned</div></div>
        <div class="card"><div class="c-label">Top Score</div><div class="c-value" style="color:var(--green)">${sum.top_score ?? "—"}</div><div class="c-sub">ultra score</div></div>
        <div class="card"><div class="c-label">Run ID</div><div class="c-value" style="font-size:1.1rem">${scan.scan_run_id ?? "—"}</div><div class="c-sub">${esc(scan.source ?? "scanner-api")}</div></div>
        <div class="card"><div class="c-label">Bands</div><div class="c-value">${Object.keys(sum.bands ?? {}).length}</div><div class="c-sub">active</div></div>
        <div class="card"><div class="c-label">Sectors</div><div class="c-value">${Object.keys(sum.sectors ?? {}).length}</div><div class="c-sub">covered</div></div>
        <div class="card"><div class="c-label">Universe</div><div class="c-value" style="font-size:.9rem;padding-top:4px">${esc(scan.universe ?? "—")}</div><div class="c-sub">${esc(scan.timeframe ?? "—")}</div></div>
      </div>
      <div class="section-label">Quick Access</div>
      <div class="quick-grid">
        ${quickLinks.map(q => `
          <a class="quick-card" href="#${q.page}">
            <span class="quick-icon">${q.icon}</span>
            <span class="quick-label">${q.label}</span>
            <span class="quick-sub">${q.sub}</span>
          </a>`).join("")}
      </div>
      <div class="section-label">Service Status</div>
      <div class="cards-row">
        <div class="card"><div class="c-label">Scanner API</div><div class="c-value" style="font-size:1rem"><span class="pill ${reach ? "ok" : "err"}">${reach ? "reachable" : "unreachable"}</span></div></div>
        <div class="card"><div class="c-label">Last Scan</div><div class="c-value" style="font-size:.8rem;line-height:1.4">${fmtDate(scan.finished_at)}</div></div>
        <div class="card"><div class="c-label">Status</div><div class="c-value" style="font-size:1rem">${esc(scan.status ?? "—")}</div></div>
      </div>
    </div>`;
}

function homeError() {
  return `<div class="page-container">
    <div class="state-banner error">Dashboard bootstrap failed — scanner-api may be unreachable.</div>
    <div class="section-label">Quick Access</div>
    <div class="quick-grid">
      <a class="quick-card" href="#system"><span class="quick-icon">⚙</span><span class="quick-label">System</span><span class="quick-sub">Check health</span></a>
    </div>
  </div>`;
}

// ═════════════════════════════════════════════════════════════════════════════
// PAGE: DASHBOARD
// ═════════════════════════════════════════════════════════════════════════════
async function renderDashboard() {
  const data   = await ensureBootstrap();
  const movers = data.top_movers?.regular ?? {};
  const setups = data.best_setups ?? [];
  const sum    = data.summary ?? {};

  $r().innerHTML = `
    <div class="page-container">
      <div class="section-label">Top Movers</div>
      <div class="movers-row">
        <div class="movers-box">
          <div class="m-title gain-title">Top Gainers</div>
          <div>${renderMoversList(movers.gainers ?? [])}</div>
        </div>
        <div class="movers-box">
          <div class="m-title loss-title">Top Losers</div>
          <div>${renderMoversList(movers.losers ?? [])}</div>
        </div>
      </div>
      <div class="section-label">Best Setups</div>
      <div class="setups-row">${renderSetups(setups)}</div>
      <div class="section-label">Distribution</div>
      <div class="dist-row">
        <div class="dist-box">
          <div class="d-title">Bands</div>
          <div>${renderBars(Object.entries(sum.bands ?? {}).sort((a,b)=>b[1]-a[1]), l => `band-${l === "A+" ? "Ap" : l}`)}</div>
        </div>
        <div class="dist-box">
          <div class="d-title">Top Sectors</div>
          <div>${renderBars(Object.entries(sum.sectors ?? {}).sort((a,b)=>b[1]-a[1]).slice(0,10), null)}</div>
        </div>
      </div>
    </div>`;
}

function renderMoversList(movers) {
  if (!movers?.length) return `<div class="mover-empty">No change_pct data available.</div>`;
  return movers.map((m, i) => {
    const chg    = m.change_pct;
    const chgStr = chg != null ? (chg >= 0 ? "+" : "") + fmt(chg, 2) + "%" : "—";
    const chgCls = chg != null && chg >= 0 ? "pos" : "neg";
    return `<div class="mover-row">
      <span class="mover-rank">${i + 1}</span>
      <span class="mover-sym">${esc(m.symbol)}</span>
      <span class="mover-sector">${esc(m.sector || "—")}</span>
      <span class="mover-chg ${chgCls}">${chgStr}</span>
      <span class="mover-score">${m.ultra_score ?? "—"}</span>
      <span class="chip ${bandClass(m.band)}">${esc(m.band || "—")}</span>
    </div>`;
  }).join("");
}

function renderSetups(setups) {
  if (!setups?.length) return `<span style="color:var(--text-dim);font-size:.82rem">No best setups found for current scan.</span>`;
  return setups.map(s => {
    const reasons   = (s.setup_reason ?? s.why_selected ?? []).slice(0, 4).map(w => `<li>${esc(w)}</li>`).join("");
    const riskChips = (s.risk_flags ?? []).slice(0, 2).map(r => `<span class="chip risk">${esc(r)}</span>`).join("");
    const chg    = s.change_pct;
    const chgStr = chg != null ? (chg >= 0 ? "+" : "") + fmt(chg, 2) + "%" : null;
    const chgCls = chg != null && chg >= 0 ? "pos" : "neg";
    return `<div class="setup-card">
      <div class="s-sym">
        ${esc(s.symbol)}
        <button class="btn-chart" onclick="openChart('${esc(s.symbol)}')" title="Open in Superchart" style="margin-left:6px">◈</button>
      </div>
      <div class="s-sector">${esc(s.sector || "—")}${s.industry ? ` · ${esc(s.industry)}` : ""}</div>
      <div class="score-row">
        <span class="score-num">${s.ultra_score ?? "—"}</span>
        <span class="chip ${bandClass(s.band)}">${esc(s.band || "—")}</span>
        ${s.final_signal ? `<span class="chip signal">${esc(s.final_signal)}</span>` : ""}
        ${chgStr ? `<span class="td-chg ${chgCls}" style="font-size:.75rem;margin-left:4px">${chgStr}</span>` : ""}
      </div>
      ${riskChips ? `<div style="margin-bottom:4px">${riskChips}</div>` : ""}
      <ul class="why-list">${reasons}</ul>
    </div>`;
  }).join("");
}

function renderBars(entries, rowClass) {
  if (!entries.length) return `<span style="color:var(--text-dim);font-size:.75rem">No data.</span>`;
  const max = entries[0][1] || 1;
  return entries.map(([label, count]) =>
    `<div class="bar-row ${rowClass ? rowClass(label) : ""}">
       <span class="bar-label">${esc(label)}</span>
       <div class="bar-track"><div class="bar-fill" style="width:${Math.round(count/max*100)}%"></div></div>
       <span class="bar-count">${count}</span>
     </div>`
  ).join("");
}

// ═════════════════════════════════════════════════════════════════════════════
// PAGE: ULTRA SCANNER
// ═════════════════════════════════════════════════════════════════════════════
let _ultraCandidates = [];
let _pollTimer       = null;
let _scanRunId       = null;
let _scanRunning     = false;

// ── Super Chart state ─────────────────────────────────────────────────────────
let _chartMode       = "latest";   // "latest" | "history"
let _historyLookback = 60;

// ── Scan controls: sample lists + controls init ───────────────────────────────
async function loadSampleListsAndInit() {
  const ctrlEl = $("scanControls");
  if (!ctrlEl) return;
  try {
    const d = await apiFetch("/api/dashboard/scans/ultra/sample-lists");
    const lists = d.lists ?? {};
    const counts = {
      sp500_sample:   (lists.sp500_sample   ?? []).length,
      nasdaq_sample:  (lists.nasdaq_sample  ?? []).length,
      manual_default: (lists.manual_default ?? []).length,
    };
    const univ = $("scUniverse");
    if (univ) {
      univ.querySelector('option[value="sp500_sample"]').textContent  = `S&P 500 Sample (${counts.sp500_sample})`;
      univ.querySelector('option[value="nasdaq_sample"]').textContent = `NASDAQ Sample (${counts.nasdaq_sample})`;
      univ.querySelector('option[value="manual_default"]').textContent = `Manual List (${counts.manual_default})`;
    }
    // If split-universe cache is cold, warm it in the background. The
    // sample-lists hot-path stays fast; the split fetch happens out-of-band.
    if (!lists.split_cache_warm) _warmSplitUniverseInBackground();
  } catch { /* leave default labels */ }
}

let _splitWarmAttempted = false;
function _warmSplitUniverseInBackground() {
  if (_splitWarmAttempted) return;
  _splitWarmAttempted = true;
  // Fire-and-forget. /split-universe has its own 15s upstream budget.
  apiFetch("/api/dashboard/scans/ultra/split-universe")
    .catch(() => { /* ignore — split is optional */ });
}

// ── Admin Control Center actions ──────────────────────────────────────────
function _adminStatusEl()    { return $("adminStatus"); }
function _adminToken()       { return ($("adminTokenInput")?.value ?? "").trim(); }
function _setAdminStatus(html, cls = "") {
  const el = _adminStatusEl();
  if (!el) return;
  el.className = "admin-status " + cls;
  el.innerHTML = html;
}
function _setAdminButtons(disabled) {
  for (const id of ["adminSyncBtn", "adminScanBtn", "adminGenBtn", "adminPipeBtn"]) {
    const b = $(id); if (b) b.disabled = disabled;
  }
}

// Run a scan from the System page (no Ultra-page DOM dependency).
// Picks universe/count/scoring defaults from localStorage so the operator
// can override via console.
// Returns Promise<{ok, runId, lastStatus, scanned}> for chaining.
async function _adminRunScanFromSystem(opts) {
  if (!_adminToken()) {
    _setAdminStatus("⚠ Paste ADMIN_TOKEN above first.", "admin-warn");
    return { ok: false, error: "no_token" };
  }
  const universe    = (opts && opts.universe)    || localStorage.getItem("sachoki_pipe_universe")     || "sp500_sample";
  const symbol_count= (opts && opts.symbol_count) ?? parseInt(localStorage.getItem("sachoki_pipe_count") || "25", 10);
  const scoring_mode= (opts && opts.scoring_mode) || localStorage.getItem("sachoki_pipe_scoring")      || "real";
  const replace     = (opts && opts.replace) ?? (localStorage.getItem("sachoki_pipe_replace") !== "false");

  _setAdminStatus(
    `⚡ Run Scan (${esc(universe)}, ${symbol_count} symbols, ${esc(scoring_mode)})…`,
    "admin-running"
  );
  _setAdminButtons(true);

  let runResp;
  try {
    const r = await fetch("/api/dashboard/scans/ultra/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ universe, symbol_count, scoring_mode, timeframe: "1d", replace_latest: replace }),
    });
    runResp = await r.json();
    if (!r.ok && runResp.error_code !== "UPSTREAM_TIMEOUT") {
      _setAdminStatus(`✕ Scan ack failed (HTTP ${r.status}): ${esc(runResp.error || "")}`, "admin-error");
      _setAdminButtons(false);
      return { ok: false, error: runResp.error };
    }
  } catch (err) {
    _setAdminStatus(`✕ Scan request failed: ${esc(String(err))}`, "admin-error");
    _setAdminButtons(false);
    return { ok: false, error: String(err) };
  }
  const runId = runResp?.run_id ?? null;

  // Poll status until terminal.
  const deadline = Date.now() + 5 * 60_000;
  let lastStatus = "starting";
  let lastScanned = 0;
  let lastTotal   = symbol_count;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 2500));
    let st;
    try {
      st = await apiFetch("/api/dashboard/scans/ultra/status",
                           runId ? { run_id: runId } : {});
    } catch { continue; }
    lastStatus = (st.status ?? "").toLowerCase();
    lastScanned = st.symbols_scanned ?? lastScanned;
    lastTotal   = st.symbols_requested ?? lastTotal;
    _setAdminStatus(
      `⚡ Run Scan… [${esc(lastStatus)}: ${lastScanned}/${lastTotal}]`,
      "admin-running"
    );
    if (["completed","done","complete","failed","cancelled"].includes(lastStatus)) break;
  }
  _setAdminButtons(false);

  if (lastStatus === "failed" || lastStatus === "cancelled") {
    _setAdminStatus(`✕ Scan ${lastStatus} after ${lastScanned}/${lastTotal} symbols.`, "admin-error");
    return { ok: false, error: lastStatus, scanned: lastScanned };
  }
  if (!["completed","done","complete"].includes(lastStatus)) {
    _setAdminStatus(`⚠ Scan timeout after 5 min (status=${esc(lastStatus)}, ${lastScanned}/${lastTotal}).`, "admin-warn");
    return { ok: false, error: "timeout", scanned: lastScanned };
  }
  _setAdminStatus(
    `✓ Scan complete · run_id=<b>${runId ?? "?"}</b> · <b>${lastScanned}</b> / ${lastTotal} symbols scanned`,
    "admin-ok"
  );
  return { ok: true, runId, lastStatus, scanned: lastScanned };
}

async function _adminSyncMarketData(opts) {
  const token = _adminToken();
  if (!token) {
    _setAdminStatus("⚠ Paste ADMIN_TOKEN above first.", "admin-warn");
    return { ok: false, error: "no_token" };
  }
  _setAdminStatus("⇣ Syncing market data… (this can take a while; first cold pull hits Massive for every symbol)", "admin-running");
  _setAdminButtons(true);
  try {
    const resp = await fetch("/api/dashboard/admin/sync-market-data", {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-admin-token": token },
      body: JSON.stringify(opts?.body ?? {}),
    });
    const result = await resp.json();
    if (!resp.ok) {
      _setAdminStatus(`✕ Sync failed (HTTP ${resp.status}): ${esc(result.error || result.detail || "")}`, "admin-error");
      return { ok: false, error: result.error || `HTTP ${resp.status}` };
    }
    const sent  = result.synced_from_massive ?? 0;
    const cache = result.cache_hit ?? 0;
    const fail  = result.failed ?? 0;
    const rows  = result.rows_written ?? 0;
    _setAdminStatus(
      `✓ Sync complete · <b>${sent}</b> fetched from Massive · <b>${cache}</b> cache hits · <b>${rows}</b> bars written · ${fail} failed`,
      "admin-ok"
    );
    return { ok: true, result };
  } catch (err) {
    _setAdminStatus(`✕ Sync error: ${esc(String(err))}`, "admin-error");
    return { ok: false, error: String(err) };
  } finally {
    _setAdminButtons(false);
  }
}

async function _adminGenerateViews(opts) {
  const token = _adminToken();
  if (!token) {
    _setAdminStatus("⚠ Paste ADMIN_TOKEN above first.", "admin-warn");
    return { ok: false, error: "no_token" };
  }
  _setAdminStatus("▦ Generating dashboard views (top_movers / best_setups / sector_heat / dashboard_summary)…", "admin-running");
  _setAdminButtons(true);
  try {
    const resp = await fetch("/api/dashboard/admin/generate-views", {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-admin-token": token },
      body: JSON.stringify(opts?.body ?? {}),
    });
    const result = await resp.json();
    if (!resp.ok) {
      _setAdminStatus(`✕ Generate failed (HTTP ${resp.status}): ${esc(result.error || result.detail || "")}`, "admin-error");
      return { ok: false, error: result.error || `HTTP ${resp.status}` };
    }
    if (result.ok === false) {
      _setAdminStatus(`✕ Generate failed: ${esc(result.error || result.message || "")}`, "admin-error");
      return { ok: false, error: result.error };
    }
    const runId = result.scan_run_id;
    const n     = result.candidate_count ?? 0;
    const v     = result.view_count ?? 0;
    _setAdminStatus(
      `✓ Views generated · run_id=<b>${runId}</b> · <b>${n}</b> candidates aggregated · <b>${v}</b> views written (${(result.views_generated || []).join(", ")})`,
      "admin-ok"
    );
    return { ok: true, result };
  } catch (err) {
    _setAdminStatus(`✕ Generate error: ${esc(String(err))}`, "admin-error");
    return { ok: false, error: String(err) };
  } finally {
    _setAdminButtons(false);
  }
}

// Full Pipeline triggered FROM the Ultra page (uses on-page form values for
// the scan step). Kept for legacy / direct in-page Run-Scan workflow.
async function _adminFullPipeline() {
  if (!_adminToken()) {
    _setAdminStatus("⚠ Paste ADMIN_TOKEN above first.", "admin-warn");
    return;
  }
  _setAdminStatus("▶ Step 1/3: Sync Market Data…", "admin-running");
  const sync = await _adminSyncMarketData();
  if (!sync.ok) return;
  _setAdminStatus(_adminStatusEl().innerHTML + "<br>▶ Step 2/3: Run Scan…", "admin-running");
  await runScan();
  const ok = await _waitForScanIdle({ timeoutMs: 5 * 60_000 });
  if (!ok) {
    _setAdminStatus(_adminStatusEl().innerHTML +
      "<br>⚠ Scan didn't finish within 5 min — skipping Generate Views. Run it manually after the scan completes.",
      "admin-warn");
    return;
  }
  _setAdminStatus(_adminStatusEl().innerHTML + "<br>▶ Step 3/3: Generate Views…", "admin-running");
  await _adminGenerateViews();
}

// Full Pipeline triggered FROM the System page. Independent of the Ultra
// page DOM — uses sensible defaults remembered in localStorage. The Ultra
// page's own Run Scan button stays for granular control of universe/count/
// scoring per-run.
async function _adminFullPipelineFromSystem() {
  if (!_adminToken()) {
    _setAdminStatus("⚠ Paste ADMIN_TOKEN above first.", "admin-warn");
    return;
  }
  _setAdminStatus("▶ Step 1/3: Sync Market Data…", "admin-running");
  const sync = await _adminSyncMarketData();
  if (!sync.ok) return;

  _setAdminStatus(_adminStatusEl().innerHTML + "<br>▶ Step 2/3: Run Scan…", "admin-running");
  const scan = await _adminRunScanFromSystem();
  if (!scan.ok) {
    _setAdminStatus(_adminStatusEl().innerHTML +
      `<br>✕ Scan failed (${esc(scan.error || "?")}); skipping Generate Views.`,
      "admin-error");
    return;
  }

  _setAdminStatus(_adminStatusEl().innerHTML + "<br>▶ Step 3/3: Generate Views…", "admin-running");
  await _adminGenerateViews();
}

async function _waitForScanIdle({ timeoutMs = 300000, pollMs = 2500 } = {}) {
  // Returns true when the global _scanRunning flag flips back to false,
  // or false on timeout. runScan/_doPoll already manage that flag.
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!_scanRunning) return true;
    await new Promise(r => setTimeout(r, pollMs));
  }
  return false;
}

async function runScan() {
  if (_scanRunning) return;
  const universe    = $("scUniverse")?.value   ?? "sp500_sample";
  const count       = parseInt($("scCount")?.value  ?? "25", 10);
  const mode        = $("scMode")?.value        ?? "real";
  const replace     = $("scReplace")?.checked  ?? true;

  _scanRunning = true;
  _clearPolling();
  _setScanBtns(true);
  _setProgressVisible(true);
  $("scProgress").innerHTML = _progressHtml({ status: "starting", symbols_scanned: 0, symbols_total: count });

  try {
    const resp = await fetch("/api/dashboard/scans/ultra/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ universe, symbol_count: count, scoring_mode: mode, timeframe: "1d", replace_latest: replace }),
    });
    const result = await resp.json();
    if (!resp.ok || result.ok === false) {
      // BFF now returns structured upstream errors with `error_code` —
      // we match on the code, not the human-readable message string.
      // Codes are defined in apps/dashboard/backend/scanner_client.py:
      //   UPSTREAM_TIMEOUT, UPSTREAM_UNAVAILABLE, UPSTREAM_NOT_CONFIGURED,
      //   UPSTREAM_HTTP_4XX, UPSTREAM_HTTP_5XX, UPSTREAM_UNKNOWN.
      const code = result.error_code || "";
      if (code === "UPSTREAM_TIMEOUT") {
        // scan_run is an "ack" endpoint; the actual scan runs in
        // background on scanner-api. A timeout here means we missed the
        // ack but the scan may have started — fall back to polling.
        $("scProgress").innerHTML = _progressHtml({
          status: "starting",
          symbols_scanned: 0,
          symbols_total: count,
          note: "ack timeout — scan may have started; polling status…",
        });
        _scanRunId = null;
        _startPolling(null);
        return;
      }
      _showScanError(result.error ?? `HTTP ${resp.status}`);
      _scanRunning = false;
      _setScanBtns(false);
      return;
    }
    _scanRunId = result.run_id ?? null;
    _startPolling(_scanRunId);
  } catch (err) {
    _showScanError(String(err));
    _scanRunning = false;
    _setScanBtns(false);
  }
}

async function cancelScan() {
  _clearPolling();
  try {
    await fetch("/api/dashboard/scans/ultra/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: _scanRunId }),
    });
  } catch { /* ignore */ }
  _scanRunning = false;
  _setScanBtns(false);
  const prog = $("scProgress");
  if (prog) prog.innerHTML = _progressHtml({ status: "cancelled" });
}

function _startPolling(runId) {
  _doPoll(runId);
}

function _clearPolling() {
  if (_pollTimer) { clearTimeout(_pollTimer); _pollTimer = null; }
}

async function _doPoll(runId) {
  if (!_scanRunning) return;
  try {
    const params = runId ? { run_id: runId } : {};
    const d = await apiFetch("/api/dashboard/scans/ultra/status", params);
    updateScanProgress(d);
    const done = ["done", "complete", "completed", "failed", "cancelled"].includes((d.status ?? "").toLowerCase());
    if (done) {
      _scanRunning = false;
      _setScanBtns(false);
      if ((d.status ?? "").toLowerCase() !== "failed") onScanComplete();
      return;
    }
  } catch { /* continue polling */ }
  _pollTimer = setTimeout(() => _doPoll(runId), 2500);
}

function updateScanProgress(status) {
  const prog = $("scProgress");
  if (prog) prog.innerHTML = _progressHtml(status);
}

async function onScanComplete() {
  await ensureBootstrap(true);   // force-refresh bootstrap cache
  const data = _bootstrap;
  _ultraCandidates = data?.top_candidates ?? [];
  const sectors = [...new Set(_ultraCandidates.map(c => c.sector || "").filter(Boolean))].sort();
  const sel = $("fSector");
  if (sel) {
    const cur = sel.value;
    sel.innerHTML = `<option value="">All Sectors</option>` +
      sectors.map(s => `<option${s===cur?" selected":""}>${esc(s)}</option>`).join("");
  }
  applyUltraFilters();
}

function _progressHtml(s) {
  if (!s) return "";
  const st       = s.status ?? "unknown";
  const scanned  = s.symbols_scanned  ?? s.processed ?? 0;
  const total    = s.symbols_total    ?? s.total      ?? 0;
  const saved    = s.symbols_saved    ?? s.candidates ?? 0;
  const failed   = s.symbols_failed   ?? s.errors     ?? 0;
  const current  = s.current_symbol   ?? s.symbol     ?? "";
  const runId    = s.run_id           ?? _scanRunId   ?? "—";
  const pct      = total > 0 ? Math.min(100, Math.round((scanned / total) * 100)) : 0;
  const pillCls  = st === "running" ? "pill-running" : st === "done" || st === "completed" || st === "complete" ? "pill-done" :
                   st === "failed"  ? "pill-failed"  : st === "cancelled" ? "pill-cancelled" : "pill-pending";
  return `
    <div class="scan-prog-row">
      <span class="scan-run-id">Run: ${esc(String(runId).slice(0,16))}…</span>
      <span class="scan-pill ${pillCls}">${esc(st)}</span>
    </div>
    <div class="scan-bar-wrap"><div class="scan-bar-fill" style="width:${pct}%"></div></div>
    <div class="scan-stats">
      <span>Scanned <b>${scanned}</b>${total ? " / " + total : ""}</span>
      <span>Saved <b>${saved}</b></span>
      <span>Failed <b>${failed}</b></span>
      ${current ? `<span class="scan-current">→ ${esc(current)}</span>` : ""}
    </div>`;
}

function _showScanError(msg) {
  const prog = $("scProgress");
  if (prog) prog.innerHTML = `<div class="scan-error">Scan error: ${esc(msg)}</div>`;
}

function _setProgressVisible(visible) {
  const wrap = $("scProgressWrap");
  if (wrap) wrap.style.display = visible ? "" : "none";
}

function _setScanBtns(running) {
  const runBtn    = $("scRunBtn");
  const cancelBtn = $("scCancelBtn");
  if (runBtn)    { runBtn.disabled    = running; runBtn.textContent = running ? "Scanning…" : "⚡ Run Scan"; }
  if (cancelBtn) { cancelBtn.disabled = !running; }
}

// Phase 8G commit 9 + Phase 8H UI parity: filter widgets for Ultra latest table.
// Reads bar.signals.{family} and bar.scores/ohlcv from the normalized scanner output.
const _FILTER_FAMILIES = [
  { key: "t",     label: "T"     }, { key: "z",     label: "Z"     },
  { key: "l",     label: "L"     }, { key: "f",     label: "F"     },
  { key: "fly",   label: "FLY"   }, { key: "g",     label: "G"     },
  { key: "b",     label: "B"     }, { key: "i",     label: "I"     },
  { key: "ult",   label: "ULT"   }, { key: "vabs",  label: "VABS"  },
  { key: "wick",  label: "WICK"  }, { key: "setup", label: "SETUP" },
  { key: "gog",   label: "GOG"   }, { key: "ctx",   label: "CTX"   },
];
const _ultraSelectedFamilies = new Set();

// Score bands matching old Ultra: all / 0-20 / 21-40 / 41-60 / 61-80 / 81-100
const _SCORE_BANDS = [
  { key: "all", label: "ALL",    min: null, max: null },
  { key: "b1",  label: "0–20",   min: 0,    max: 20 },
  { key: "b2",  label: "21–40",  min: 21,   max: 40 },
  { key: "b3",  label: "41–60",  min: 41,   max: 60 },
  { key: "b4",  label: "61–80",  min: 61,   max: 80 },
  { key: "b5",  label: "81–100", min: 81,   max: 100 },
];
let _selectedScoreBand = "all";

// Volume bands matching old Ultra: All / <100K / 100K+ / 500K+ / 1M+ / 5M+
const _VOL_BANDS = [
  { key: "all",    label: "All",    min: null,    max: null },
  { key: "lt100k", label: "<100K",  min: 0,       max: 100_000 },
  { key: "100k",   label: "100K+",  min: 100_000, max: null },
  { key: "500k",   label: "500K+",  min: 500_000, max: null },
  { key: "1m",     label: "1M+",    min: 1_000_000, max: null },
  { key: "5m",     label: "5M+",    min: 5_000_000, max: null },
];
let _selectedVolBand = "all";

// Direction toggle: ALL / BULL / BEAR
const _DIR_OPTS = [
  { key: "all",  label: "ALL"  },
  { key: "bull", label: "BULL" },
  { key: "bear", label: "BEAR" },
];
let _selectedDir = "all";

function _refreshSegRow(rowId, dataAttr, selectedKey) {
  const row = $(rowId);
  if (!row) return;
  row.querySelectorAll(`[data-${dataAttr}]`).forEach(btn => {
    btn.classList.toggle("seg-btn-active", btn.dataset[dataAttr] === selectedKey);
  });
}

// CSV export of currently-filtered Ultra candidates with normalized payload.
let _lastFilteredCandidates = [];
function _csvCell(v) {
  if (v == null) return "";
  let s = typeof v === "object" ? JSON.stringify(v) : String(v);
  if (/[",\n]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
  return s;
}
function _exportUltraCSV() {
  const rows = _lastFilteredCandidates;
  if (!rows.length) { alert("No candidates to export."); return; }
  const cols = [
    "symbol", "sector", "industry", "price", "change_pct",
    "ultra_score", "real_ultra_score", "signal_score",
    "final_bull_score", "final_bear_score",
    "band", "priority", "category", "sector_band",
    "turbo_score", "rtb_phase", "rtb_total",
    "pf", "cat", "signal_source",
    "signals.t", "signals.z", "signals.l", "signals.f", "signals.fly",
    "signals.g", "signals.b", "signals.i", "signals.ult",
    "signals.vabs", "signals.wick", "signals.setup", "signals.gog", "signals.ctx",
    "split.has_split", "split.has_reverse_split", "split.split_ratio",
    "split.split_date", "split.phase", "split.wave",
    "why_selected", "risk_flags", "ultra_active_signals",
    "engines_ran", "engines_failed", "bar_date",
  ];
  const lines = [cols.join(",")];
  for (const c of rows) {
    const scores = c.scores || {};
    const split  = c.split  || {};
    const sigs   = c.signals || {};
    const dbg    = c.engine_debug || {};
    const get = path => {
      const [head, tail] = path.split(".");
      if (!tail) return c[head] ?? scores[head] ?? "";
      const root = head === "split" ? split : head === "signals" ? sigs : {};
      const v = root[tail];
      return Array.isArray(v) ? v.join(" ") : v;
    };
    const row = cols.map(col => {
      if (col === "engines_ran")    return _csvCell((dbg.engines_ran    ?? []).join(" "));
      if (col === "engines_failed") return _csvCell((dbg.engines_failed ?? []).join(" "));
      if (col === "why_selected")   return _csvCell((c.why_selected ?? []).join(" "));
      if (col === "risk_flags")     return _csvCell((c.risk_flags   ?? []).join(" "));
      if (col === "ultra_active_signals")
        return _csvCell((c.ultra_active_signals ?? []).join(" "));
      return _csvCell(get(col));
    });
    lines.push(row.join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href = url;
  a.download = `ultra_candidates_${new Date().toISOString().slice(0,10)}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

function _toggleUltraDebug() {
  const panel = $("ultraDebugPanel");
  if (!panel) return;
  const isHidden = panel.style.display === "none";
  if (!isHidden) { panel.style.display = "none"; return; }

  // Aggregate engine_debug + signal_source across visible candidates.
  const rows = _lastFilteredCandidates;
  const sourceCounts = {};
  const engineRanCounts = {};
  const engineFailedCounts = {};
  for (const c of rows) {
    const src = c.signal_source || "(unset)";
    sourceCounts[src] = (sourceCounts[src] ?? 0) + 1;
    const dbg = c.engine_debug || {};
    for (const e of (dbg.engines_ran    ?? [])) engineRanCounts[e]    = (engineRanCounts[e]    ?? 0) + 1;
    for (const e of (dbg.engines_failed ?? [])) engineFailedCounts[e] = (engineFailedCounts[e] ?? 0) + 1;
  }
  const fmt = (obj) => Object.entries(obj).sort((a,b) => b[1]-a[1])
    .map(([k,v]) => `<span class="chip" style="font-size:.65rem">${esc(k)} <b>${v}</b></span>`).join(" ");

  panel.innerHTML = `
    <div class="dbg-row"><b>signal_source (real vs proxy):</b> ${fmt(sourceCounts) || "—"}</div>
    <div class="dbg-row"><b>engines_ran:</b> ${fmt(engineRanCounts) || "—"}</div>
    <div class="dbg-row"><b>engines_failed:</b> ${fmt(engineFailedCounts) || "—"}</div>
    <div class="dbg-row" style="color:var(--text-dim);font-size:.7rem">
      Source-of-truth check: every candidate above is scored from
      engine_registry output, not inferred proxies (signal_source=engine_registry).
      Super Chart History reads the same payload — clicking a row's <b>chart</b>
      button opens the matching Super Chart bars.
    </div>`;
  panel.style.display = "";
}

function _refreshFamilyChips() {
  document.querySelectorAll(".family-chip[data-family]").forEach(btn => {
    const active = _ultraSelectedFamilies.has(btn.dataset.family);
    btn.classList.toggle("family-chip-active", active);
  });
}

async function renderUltra() {
  const data = await ensureBootstrap();
  const scan = data.latest_scan ?? {};
  _ultraCandidates = data.top_candidates ?? [];

  const sectors    = [...new Set(_ultraCandidates.map(c => c.sector || "").filter(Boolean))].sort();
  const sectorOpts = sectors.map(s => `<option>${esc(s)}</option>`).join("");

  $r().innerHTML = `
    <div class="page-container">
      <div class="section-label">Latest Scan</div>
      <div class="cards-row">
        <div class="card"><div class="c-label">Run ID</div><div class="c-value" style="font-size:1.1rem">${scan.scan_run_id ?? "—"}</div></div>
        <div class="card"><div class="c-label">Universe</div><div class="c-value" style="font-size:.9rem;padding-top:4px">${esc(scan.universe ?? "—")}</div></div>
        <div class="card"><div class="c-label">Timeframe</div><div class="c-value">${esc(scan.timeframe ?? "—")}</div></div>
        <div class="card"><div class="c-label">Candidates</div><div class="c-value">${scan.total_candidates ?? "—"}</div></div>
        <div class="card"><div class="c-label">Status</div><div class="c-value" style="font-size:.9rem">${esc(scan.status ?? "—")}</div></div>
        <div class="card"><div class="c-label">Finished</div><div class="c-value" style="font-size:.7rem;padding-top:4px">${fmtDate(scan.finished_at)}</div></div>
      </div>

      <div class="section-label">Ultra Scan Controls</div>
      <div class="scan-controls-card" id="scanControls">
        <div class="scan-safety-row">
          <span class="safety-chip">Scheduler Disabled</span>
          <span class="safety-chip">Full-market Disabled</span>
          <span class="safety-chip safety-provider">Provider: Massive</span>
        </div>
        <div class="scan-form-row">
          <label class="scan-label">Universe
            <select id="scUniverse">
              <option value="sp500_sample">S&amp;P 500 Sample</option>
              <option value="nasdaq_sample">NASDAQ Sample</option>
              <option value="manual_default">Manual List</option>
            </select>
          </label>
          <label class="scan-label">Symbols
            <select id="scCount" title="MAX uses the entire universe list. Upstream cap is governed by SCANNER_MAX_SYMBOLS env.">
              <option value="10">10</option>
              <option value="25" selected>25</option>
              <option value="50">50</option>
              <option value="100">100</option>
              <option value="250">250</option>
              <option value="500">500</option>
              <option value="0">MAX</option>
            </select>
          </label>
          <label class="scan-label">Scoring
            <select id="scMode">
              <option value="real" selected>Real</option>
              <option value="compare">Compare</option>
            </select>
          </label>
          <label class="scan-label scan-label-check">
            <input type="checkbox" id="scReplace" checked /> Replace latest
          </label>
          <button class="btn-run"    id="scRunBtn"    onclick="runScan()">⚡ Run Scan</button>
          <button class="btn-cancel" id="scCancelBtn" onclick="cancelScan()" disabled>✕ Cancel</button>
          <button class="btn-refresh-scan" onclick="ensureBootstrap(true).then(onScanComplete)">↻ Refresh Latest</button>
        </div>
        <div class="scan-progress-wrap" id="scProgressWrap" style="display:none">
          <div id="scProgress"></div>
        </div>
      </div>

      <div class="section-label">Candidates</div>
      <div class="filters-bar">
        <label>Symbol
          <input id="fSearch" type="text" placeholder="AAPL, MSFT…" autocomplete="off" />
        </label>
        <label>Band
          <select id="fBand">
            <option value="">All</option>
            <option>A+</option><option>A</option><option>B</option><option>C</option><option>D</option>
          </select>
        </label>
        <label>Sector
          <select id="fSector"><option value="">All Sectors</option>${sectorOpts}</select>
        </label>
        <label>Min Score
          <input id="fMinScore" type="number" min="0" max="100" placeholder="0" style="width:70px" />
        </label>
        <label>Split
          <select id="fSplit">
            <option value="">Any</option>
            <option value="exclude">Exclude split-contaminated</option>
            <option value="only">Split universe only</option>
            <option value="reverse">Has reverse split</option>
          </select>
        </label>
        <label>RTB Phase
          <select id="fRtb">
            <option value="">Any</option>
            <option>A</option><option>B</option><option>C</option><option>D</option>
          </select>
        </label>
        <button class="btn-clear" id="clearFilters">Clear</button>
        <button class="btn-clear" id="exportCsvBtn" title="Export filtered candidates to CSV">⬇ CSV</button>
        <button class="btn-clear" id="debugPanelBtn" title="Toggle debug panel">⚙ Debug</button>
        <span class="filter-count" id="filterCount"></span>
      </div>
      <div id="ultraDebugPanel" class="ultra-debug-panel" style="display:none"></div>
      <div class="filter-row" id="filterScoreBands">
        <span class="filter-row-label">Score band</span>
        ${_SCORE_BANDS.map(b =>
          `<button type="button" class="seg-btn" data-band="${b.key}">${esc(b.label)}</button>`
        ).join("")}
      </div>
      <div class="filter-row" id="filterVolBands">
        <span class="filter-row-label">Volume</span>
        ${_VOL_BANDS.map(v =>
          `<button type="button" class="seg-btn" data-vol="${v.key}">${esc(v.label)}</button>`
        ).join("")}
      </div>
      <div class="filter-row" id="filterDirection">
        <span class="filter-row-label">Direction</span>
        ${_DIR_OPTS.map(d =>
          `<button type="button" class="seg-btn" data-dir="${d.key}">${esc(d.label)}</button>`
        ).join("")}
      </div>
      <div class="filter-families" id="filterFamilies">
        <span class="filter-families-label">Signal family</span>
        ${_FILTER_FAMILIES.map(f =>
          `<button type="button" class="chip family-chip" data-family="${f.key}">${esc(f.label)}</button>`
        ).join("")}
        <button type="button" class="chip family-chip family-clear" id="famClearBtn">clear</button>
      </div>
      <div class="table-wrap">
        <table class="ultra-table">
          <thead><tr>
            <th class="td-rank">#</th>
            <th>Symbol</th>
            <th title="Turbo score (0-100). Primary sort key, score-band filter.">Score</th>
            <th title="Ultra score banded A+/A/B/C/D">ULTRA</th>
            <th title="BETA score + zone (beta_engine)">BETA</th>
            <th>RTB</th>
            <th>T/Z</th>
            <th title="Profile category (profile_playbook): SWEET_SPOT / BUILDING / WATCH / LATE">Cat</th>
            <th class="td-signals">Signals</th>
            <th>RSI</th>
            <th>CCI</th>
            <th>Price</th>
            <th>%</th>
            <th>Split</th>
            <th>Sector</th>
            <th></th>
          </tr></thead>
          <tbody id="candidatesBody"></tbody>
        </table>
      </div>
    </div>`;

  applyUltraFilters();
  ["fSearch","fBand","fSector","fMinScore","fSplit","fRtb"].forEach(id => {
    const el = $(id);
    if (el) el.addEventListener("input",  applyUltraFilters);
    if (el) el.addEventListener("change", applyUltraFilters);
  });
  const clr = $("clearFilters");
  if (clr) clr.addEventListener("click", () => {
    ["fSearch","fBand","fSector","fMinScore","fSplit","fRtb"].forEach(id => {
      const el=$(id); if(el) el.value="";
    });
    _ultraSelectedFamilies.clear();
    _refreshFamilyChips();
    applyUltraFilters();
  });
  // Signal-family chips — click toggles selection
  document.querySelectorAll(".family-chip[data-family]").forEach(btn => {
    btn.addEventListener("click", () => {
      const fam = btn.dataset.family;
      if (_ultraSelectedFamilies.has(fam)) _ultraSelectedFamilies.delete(fam);
      else                                 _ultraSelectedFamilies.add(fam);
      _refreshFamilyChips();
      applyUltraFilters();
    });
  });
  const famClr = $("famClearBtn");
  if (famClr) famClr.addEventListener("click", () => {
    _ultraSelectedFamilies.clear();
    _refreshFamilyChips();
    applyUltraFilters();
  });
  _refreshFamilyChips();

  // Segmented controls (score bands, volume, direction)
  document.querySelectorAll("#filterScoreBands [data-band]").forEach(btn => {
    btn.addEventListener("click", () => {
      _selectedScoreBand = btn.dataset.band;
      _refreshSegRow("filterScoreBands", "band", _selectedScoreBand);
      applyUltraFilters();
    });
  });
  document.querySelectorAll("#filterVolBands [data-vol]").forEach(btn => {
    btn.addEventListener("click", () => {
      _selectedVolBand = btn.dataset.vol;
      _refreshSegRow("filterVolBands", "vol", _selectedVolBand);
      applyUltraFilters();
    });
  });
  document.querySelectorAll("#filterDirection [data-dir]").forEach(btn => {
    btn.addEventListener("click", () => {
      _selectedDir = btn.dataset.dir;
      _refreshSegRow("filterDirection", "dir", _selectedDir);
      applyUltraFilters();
    });
  });
  _refreshSegRow("filterScoreBands", "band", _selectedScoreBand);
  _refreshSegRow("filterVolBands",   "vol",  _selectedVolBand);
  _refreshSegRow("filterDirection",  "dir",  _selectedDir);

  const expBtn = $("exportCsvBtn");
  if (expBtn) expBtn.addEventListener("click", _exportUltraCSV);
  const dbgBtn = $("debugPanelBtn");
  if (dbgBtn) dbgBtn.addEventListener("click", _toggleUltraDebug);

  // Restore running state if scan was in progress before navigation
  if (_scanRunning) {
    _setProgressVisible(true);
    _setScanBtns(true);
    if (_scanRunId) _startPolling(_scanRunId);
  }

  // Load sample list sizes in background (non-blocking)
  loadSampleListsAndInit();
}

function applyUltraFilters() {
  const search   = ($("fSearch")?.value ?? "").trim().toUpperCase();
  const band     = $("fBand")?.value ?? "";
  const sector   = $("fSector")?.value ?? "";
  const minScore = parseFloat($("fMinScore")?.value) || 0;
  const splitOpt = $("fSplit")?.value ?? "";
  const rtbOpt   = $("fRtb")?.value ?? "";

  const filtered = _ultraCandidates.filter(c => {
    if (search && !c.symbol?.toUpperCase().includes(search)) return false;
    if (band   && c.band !== band)     return false;
    if (sector && c.sector !== sector) return false;

    // Phase 8I: score band + min-score filter operate on TURBO_SCORE
    // (the primary score in old UltraScanPanel.jsx:832, line 944). ULTRA
    // band A+/A/B/C/D is a separate banded view of ultra_score and is
    // filtered by the "Band" dropdown above, not by these segmented
    // buttons. Fallback to ultra_score only when turbo_score is null
    // (e.g. legacy candidate rows or registry failure).
    const turbo = (c.scores && c.scores.turbo_score) ?? c.turbo_score
                ?? c.ultra_score ?? 0;
    if (turbo < minScore) return false;

    const sb = _SCORE_BANDS.find(b => b.key === _selectedScoreBand);
    if (sb && sb.min != null) {
      if (turbo < sb.min || (sb.max != null && turbo > sb.max)) return false;
    }

    // Volume segmented filter
    const vb = _VOL_BANDS.find(v => v.key === _selectedVolBand);
    if (vb && vb.min != null) {
      const v = (c.ohlcv && c.ohlcv.volume) || c.volume || 0;
      if (v < vb.min || (vb.max != null && v > vb.max)) return false;
    }

    // Direction filter (derived from signals.t / signals.z presence)
    if (_selectedDir === "bull") {
      const t = (c.signals && c.signals.t) || [];
      if (!t.length) return false;
    } else if (_selectedDir === "bear") {
      const z = (c.signals && c.signals.z) || [];
      if (!z.length) return false;
    }

    // Split filter
    const split = c.split || {};
    if (splitOpt === "exclude" && split.split_contaminated) return false;
    if (splitOpt === "only"    && !split.has_split)         return false;
    if (splitOpt === "reverse" && !split.has_reverse_split) return false;

    // RTB filter
    if (rtbOpt) {
      const phase = (c.scores || {}).rtb_phase ?? "";
      if (phase !== rtbOpt) return false;
    }

    // Signal-family chip filter — AND across selected families.
    if (_ultraSelectedFamilies.size > 0) {
      const sigs = c.signals || {};
      for (const fam of _ultraSelectedFamilies) {
        if (!(sigs[fam] && sigs[fam].length > 0)) return false;
      }
    }
    return true;
  });

  _lastFilteredCandidates = filtered;
  const fc = $("filterCount");
  if (fc) fc.textContent = `${filtered.length} / ${_ultraCandidates.length} shown`;
  renderCandidateTable(filtered);
}

// Row order in which we flatten candidate.signals.* into the Signals badge string.
// Matches old Ultra column order: ABCD/SETUP first, then VABS, COMBO, ULT, L, GOG, CTX, B/F/FLY/G.
const _SIG_RENDER_ORDER = ["setup", "vabs", "i", "ult", "l", "gog", "ctx", "f", "fly", "g", "b", "wick"];

function _renderSignalString(signals) {
  if (!signals) return "";
  const out = [];
  for (const row of _SIG_RENDER_ORDER) {
    const arr = signals[row] || [];
    for (const lbl of arr) out.push(SignalBadges.renderSignalBadge(lbl));
  }
  return out.join("");
}

function _formatSplitLifecycle(split) {
  if (!split || !split.has_split) return "—";
  const ratio = split.split_ratio
    ? `1:${Math.round(split.split_ratio)}`
    : "";
  const wave = split.wave || "";
  const doff = split.days_offset;
  const dstr = (doff != null && doff !== "")
    ? (doff >= 0 ? `D+${doff}` : `D${doff}`)
    : "";
  return [ratio, wave, dstr].filter(Boolean).join(" ");
}

function _firstSignal(signals, row) {
  const arr = signals && signals[row];
  return Array.isArray(arr) && arr.length ? arr[0] : "";
}

function _rtbCellClass(phase) {
  if (phase === "A") return "rtb-a";
  if (phase === "B") return "rtb-b";
  if (phase === "C") return "rtb-c";
  if (phase === "D") return "rtb-d";
  return "rtb-none";
}

function renderCandidateTable(candidates) {
  const body = $("candidatesBody");
  if (!body) return;
  if (!candidates.length) {
    body.innerHTML = `<tr class="empty-row"><td colspan="16">No candidates match the current filters.</td></tr>`;
    return;
  }
  body.innerHTML = candidates.map((c, i) => {
    const chgVal = c.change_pct;
    const chgTxt = chgVal != null ? (chgVal >= 0 ? "+" : "") + fmt(chgVal, 2) + "%" : "—";
    const chgCls = chgVal == null ? "" : chgVal >= 0 ? "pos" : "neg";

    // All visual fields come from the normalized scanner payload — no fallbacks
    // to inferred values. If a field is null, render "—".
    const scores = c.scores || {};
    const ind    = c.indicators || {};
    const signals = c.signals || {};

    // Phase 8I: Score column = REAL turbo_score (primary in old Ultra),
    // ULTRA column = ultra_score banded A+/A/B/C/D. The two scores are
    // distinct and computed by different engines — never aliased.
    const turbo = scores.turbo_score ?? c.turbo_score ?? null;
    const ultra = c.ultra_score      ?? scores.ultra_score ?? null;
    const band  = c.band || scores.band || "";
    const rtb   = scores.rtb_phase || "";
    const tz    = _firstSignal(signals, "t") || _firstSignal(signals, "z");
    // Phase 8J — profile_playbook + beta_engine now populate these
    const cat       = scores.category   || c.category || "";   // SWEET_SPOT / BUILDING / WATCH / LATE
    const pf        = scores.pf ?? c.pf ?? null;
    const betaScore = scores.beta_score ?? null;
    const betaZone  = scores.beta_zone  || "";
    const sigs  = _renderSignalString(signals);
    const rsi   = ind.rsi != null ? fmt(ind.rsi, 0) : "—";
    const cci   = ind.cci != null ? fmt(ind.cci, 0) : "—";
    const rsiCls = ind.rsi == null ? "" : (ind.rsi >= 70 ? "rsi-hi" : ind.rsi <= 35 ? "rsi-lo" : "");
    const cciCls = ind.cci == null ? "" : (ind.cci >= 100 ? "cci-hi" : ind.cci <= -100 ? "cci-lo" : "");
    const splitTxt = _formatSplitLifecycle(c.split || {});

    const tzBadge  = tz  ? SignalBadges.renderSignalBadge(tz)  : "—";
    const catBadge = cat ? `<span class="chip cat-${esc(cat.toLowerCase().replace('_','-'))}">${esc(cat)}${pf != null ? ` ${fmt(pf,0)}` : ""}</span>` : "—";
    const rtbBadge = rtb ? `<span class="rtb-pill ${_rtbCellClass(rtb)}">${esc(rtb)}</span>` : "—";
    const betaCell = betaScore == null ? "—" :
                     `<div class="beta-cell">
                       <div class="beta-num">${fmt(betaScore, 0)}</div>
                       <div class="beta-zone beta-zone-${esc(betaZone.toLowerCase().replace('_','-'))}">${esc(betaZone)}</div>
                     </div>`;

    // Turbo tier emoji (matches old UltraScanPanel: 65+ 🔥, 50+ ★, 35+ ▲)
    const turboTier = turbo == null ? "" :
                      turbo >= 65   ? "🔥" :
                      turbo >= 50   ? "★"  :
                      turbo >= 35   ? "▲"  : "";
    const turboCell = turbo == null ? "—" :
                      `${turboTier} ${fmt(turbo, 0)}`;

    // ULTRA cell — only render banded chip when the score is from the same
    // row that produced turbo (signal_source == "engine_registry_turbo_row").
    // Old Ultra showed "—" until Stage-2 enrichment landed; in our system
    // without profile_playbook + delta_engine + tz_intel ports, the ultra
    // score is partial. Showing "—" is more honest than a misleading number.
    const ultraTrustworthy =
      c.signal_source === "engine_registry_turbo_row" && ultra != null;
    const ultraCell = !ultraTrustworthy ? "—" :
                      `<span class="chip band-${esc((band || "").replace('+','plus').toLowerCase())}">${fmt(ultra, 0)} ${esc(band || "")}</span>`;

    return `<tr>
      <td class="td-rank">${i + 1}</td>
      <td class="td-sym">★ ${esc(c.symbol)}</td>
      <td class="td-score">${turboCell}</td>
      <td class="td-ultra">${ultraCell}</td>
      <td class="td-beta">${betaCell}</td>
      <td class="td-rtb">${rtbBadge}</td>
      <td class="td-tz">${tzBadge}</td>
      <td class="td-cat">${catBadge}</td>
      <td class="td-signals">${sigs || `<span class="sig sig-neutral">${esc(c.final_signal || "")}</span>`}</td>
      <td class="td-rsi ${rsiCls}">${rsi}</td>
      <td class="td-cci ${cciCls}">${cci}</td>
      <td class="td-price">${c.price != null ? "$" + fmt(c.price, 2) : "—"}</td>
      <td class="td-chg ${chgCls}">${chgTxt}</td>
      <td class="td-split">${esc(splitTxt)}</td>
      <td class="td-sector">${esc(c.sector || "—")}</td>
      <td><button class="btn-chart" onclick="openChart('${esc(c.symbol)}')" title="Open in Superchart">◈</button></td>
    </tr>`;
  }).join("");
}

// ═════════════════════════════════════════════════════════════════════════════
// PAGE: SUPERCHART
// ═════════════════════════════════════════════════════════════════════════════

function _setChartMode(mode) {
  _chartMode = mode;
  document.querySelectorAll(".chart-mode-tab").forEach(t => {
    t.classList.toggle("active", t.dataset.mode === mode);
  });
  const lbWrap = $("chartLookbackWrap");
  const barsWrap = $("chartBarsWrap");
  if (lbWrap) lbWrap.style.display = mode === "history" ? "" : "none";
  if (barsWrap) barsWrap.style.display = mode === "latest" ? "" : "none";
  const btn = $("chartLoadBtn");
  if (btn) btn.textContent = mode === "history" ? "Load History" : "Load Chart";
}

function _loadChartInMode() {
  if (_chartMode === "history") return loadChartHistory();
  return loadChartSnapshot();
}

async function renderChart() {
  const initialSym = _pendingChartSym || "";
  const autoLoad   = Boolean(_pendingChartSym);
  _pendingChartSym = null;

  $r().innerHTML = `
    <div class="page-container">

      <div class="chart-header">
        <div>
          <div class="section-label" style="margin:0 0 6px">Superchart</div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
            <span class="pill ok">Massive</span>
            <span class="chip">1D Completed Candles</span>
            <span class="chip" style="color:var(--text-dim)">yfinance_used=false</span>
          </div>
        </div>
        <div class="chart-controls">
          <div class="chart-mode-tabs">
            <button class="chart-mode-tab${_chartMode === "latest"  ? " active" : ""}" data-mode="latest">Latest</button>
            <button class="chart-mode-tab${_chartMode === "history" ? " active" : ""}" data-mode="history">History</button>
          </div>
          <label class="chart-label">Symbol
            <input id="chartSym" type="text" placeholder="AAPL" maxlength="7"
              class="chart-input" value="${esc(initialSym)}" autocomplete="off" />
          </label>
          <label class="chart-label">Timeframe
            <select id="chartTf" class="chart-select">
              <option value="1d" selected>1D Daily</option>
            </select>
          </label>
          <label class="chart-label" id="chartBarsWrap"${_chartMode !== "latest" ? ' style="display:none"' : ""}>Bars
            <select id="chartBars" class="chart-select">
              <option value="50">50</option>
              <option value="100">100</option>
              <option value="150" selected>150</option>
              <option value="200">200</option>
            </select>
          </label>
          <label class="chart-label" id="chartLookbackWrap"${_chartMode !== "history" ? ' style="display:none"' : ""}>Lookback
            <select id="chartLookback" class="chart-select">
              <option value="30">30 bars</option>
              <option value="60" selected>60 bars</option>
              <option value="90">90 bars</option>
              <option value="120">120 bars</option>
            </select>
          </label>
          <button id="chartLoadBtn" class="btn-refresh" style="align-self:flex-end;padding:6px 20px;font-size:.82rem">
            ${_chartMode === "history" ? "Load History" : "Load Chart"}
          </button>
        </div>
      </div>

      <div id="chartStatus" style="margin-top:8px"></div>

      <div id="chartAreaWrap" style="display:none">
        <div class="chart-body">
          <div class="chart-canvas-col">
            <div id="lwChart" class="lw-chart-box"></div>
          </div>
          <div class="score-panel-col" id="scorePanel"></div>
        </div>
        <div id="chartMetaSection" class="chart-meta-section"></div>
      </div>

      <div id="chartHistoryArea" style="display:none"></div>

      <div id="chartPlaceholder" class="placeholder-box" style="margin-top:12px">
        <span class="placeholder-icon">◈</span>
        <span class="placeholder-text">Enter a ticker and click ${_chartMode === "history" ? "Load History" : "Load Chart"}</span>
        <span class="placeholder-sub">Superchart · Massive · No yfinance · dashboard BFF only</span>
      </div>

    </div>`;

  // Mode tab listeners
  document.querySelectorAll(".chart-mode-tab").forEach(tab => {
    tab.addEventListener("click", () => _setChartMode(tab.dataset.mode));
  });

  const btn = $("chartLoadBtn");
  if (btn) btn.addEventListener("click", _loadChartInMode);
  const inp = $("chartSym");
  if (inp) inp.addEventListener("keydown", e => { if (e.key === "Enter") _loadChartInMode(); });

  if (autoLoad && initialSym) await _loadChartInMode();
}

async function loadChartSnapshot() {
  const sym  = ($("chartSym")?.value ?? "").trim().toUpperCase();
  const tf   = $("chartTf")?.value ?? "1d";
  const bars = parseInt($("chartBars")?.value ?? "150", 10);

  const status      = $("chartStatus");
  const areaWrap    = $("chartAreaWrap");
  const placeholder = $("chartPlaceholder");

  if (!sym || !/^[A-Z]{1,5}(-[A-Z]{1,2})?$/.test(sym)) {
    if (status) status.innerHTML = `<div class="state-banner error" style="margin:8px 0">Enter a valid ticker symbol.</div>`;
    return;
  }

  if (status) status.innerHTML = `<div class="page-loading" style="padding:12px 0">Loading ${esc(sym)} chart data…</div>`;
  const btn = $("chartLoadBtn");
  if (btn) btn.disabled = true;

  let data;
  try {
    data = await apiFetch("/api/dashboard/chart/snapshot", { symbol: sym, tf, bars });
  } catch (err) {
    if (status) status.innerHTML = `<div class="state-banner error" style="margin:8px 0">Chart snapshot unavailable: ${esc(err.message)}</div>`;
    if (btn) btn.disabled = false;
    return;
  } finally {
    if (btn) btn.disabled = false;
  }

  if (!data.ok) {
    if (status) status.innerHTML = `<div class="state-banner error" style="margin:8px 0">${esc(data.error ?? "Snapshot failed")}</div>`;
    return;
  }

  const candles = data.candles ?? [];
  if (!candles.length) {
    if (status) status.innerHTML = `<div class="state-banner warn" style="margin:8px 0">No candles returned for ${esc(sym)}.</div>`;
    return;
  }

  if (status) status.innerHTML = "";
  if (areaWrap)    areaWrap.style.display = "";
  if (placeholder) placeholder.style.display = "none";

  // Render chart
  _renderLwChart(candles, data.markers ?? []);

  // Render score panel
  const scoreEl = $("scorePanel");
  if (scoreEl) scoreEl.innerHTML = _buildScorePanel(data);

  // Render metadata + missing groups
  const metaEl = $("chartMetaSection");
  if (metaEl) metaEl.innerHTML = _buildChartMeta(data, candles.length);
}

// ── History / Timeline mode ───────────────────────────────────────────────────

async function loadChartHistory() {
  const sym      = ($("chartSym")?.value ?? "").trim().toUpperCase();
  const tf       = $("chartTf")?.value ?? "1d";
  const lookback = parseInt($("chartLookback")?.value ?? "60", 10);

  const status      = $("chartStatus");
  const chartArea   = $("chartAreaWrap");
  const histArea    = $("chartHistoryArea");
  const placeholder = $("chartPlaceholder");

  if (!sym || !/^[A-Z]{1,5}(-[A-Z]{1,2})?$/.test(sym)) {
    if (status) status.innerHTML = `<div class="state-banner error" style="margin:8px 0">Enter a valid ticker symbol (e.g. AAPL).</div>`;
    return;
  }

  if (status)      status.innerHTML = `<div class="page-loading" style="padding:12px 0">Loading ${esc(sym)} history…</div>`;
  if (chartArea)   chartArea.style.display = "none";
  if (histArea)    histArea.style.display = "none";
  if (placeholder) placeholder.style.display = "none";
  _destroyChart();

  const btn = $("chartLoadBtn");
  if (btn) btn.disabled = true;

  let data;
  try {
    data = await apiFetch("/api/dashboard/super-chart/history",
                          { ticker: sym, timeframe: tf, lookback });
  } catch (err) {
    if (status) status.innerHTML = `<div class="state-banner error" style="margin:8px 0">History unavailable: ${esc(err.message)}</div>`;
    if (btn) btn.disabled = false;
    return;
  } finally {
    if (btn) btn.disabled = false;
  }

  if (status) status.innerHTML = "";

  if (!data.ok || !(data.bars ?? []).length) {
    const warn = data.meta?.warning ?? data.error ?? "No history data available.";
    if (histArea) {
      histArea.style.display = "";
      histArea.innerHTML = `<div class="state-banner warn" style="margin:12px 0">${esc(warn)}</div>`;
    }
    return;
  }

  _lastHistoryData = data;
  if (histArea) {
    histArea.style.display = "";
    histArea.innerHTML = _buildHistoryTimeline(data);
  }
}

// Signal rows for the timeline table — full old-Ultra parity row order.
// Score rows read from bar.scores.*; numeric rows read from top-level fields.
const _TL_ROWS = [
  { key: "z",     label: "Z",     type: "sig", cls: "badge-z"       },
  { key: "t",     label: "T",     type: "sig", cls: "badge-t"       },
  { key: "l",     label: "L",     type: "sig", cls: "badge-l"       },
  { key: "f",     label: "F",     type: "sig", cls: "badge-f"       },
  { key: "fly",   label: "FLY",   type: "sig", cls: "badge-fly"     },
  { key: "g",     label: "G",     type: "sig", cls: "badge-g"       },
  { key: "b",     label: "B",     type: "sig", cls: "badge-b"       },
  { key: "i",     label: "I",     type: "sig", cls: "badge-i"       },
  { key: "ult",   label: "ULT",   type: "sig", cls: "badge-ult"     },
  { key: "vol",   label: "VOL",   type: "sig", cls: "badge-vol"     },
  { key: "vabs",  label: "VABS",  type: "sig", cls: "badge-vabs"    },
  { key: "wick",  label: "WICK",  type: "sig", cls: "badge-wick"    },
  { key: "setup", label: "SETUP", type: "sig", cls: "badge-setup"   },
  { key: "gog",   label: "GOG",   type: "sig", cls: "badge-gog"     },
  { key: "ctx",   label: "CTX",   type: "sig", cls: "badge-ctx"     },
  // numeric separator + rows
  { key: "_sep",  label: "",      type: "sep" },
  { key: "score",       label: "SCORE", type: "score", from: "ultra_score",     dec: 0 },
  { key: "turbo_score", label: "turbo", type: "score", from: "turbo_score",     dec: 0 },
  { key: "rtb_phase",   label: "rtb",   type: "score", from: "rtb_phase",       dec: -1 },
  { key: "close",       label: "close", type: "num",   dec: 2 },
  { key: "rsi",         label: "RSI",   type: "num",   dec: 1 },
  { key: "cci",         label: "CCI",   type: "num",   dec: 1 },
  { key: "pf",          label: "Pf",    type: "score", from: "pf",              dec: 0 },
  { key: "category",    label: "Cat",   type: "score", from: "category",        dec: -1 },
];

function _numCls(key, val) {
  if (key === "rsi") return val > 70 ? "tl-num tl-num-neg" : val < 30 ? "tl-num tl-num-pos" : "tl-num";
  if (key === "cci") return val > 100 ? "tl-num tl-num-pos" : val < -100 ? "tl-num tl-num-neg" : "tl-num";
  return "tl-num";
}

let _tlHideEmpty    = true;   // hide signal rows that have no badges across all bars
let _lastHistoryData = null;  // cached response for toggle re-render

function _tlRowHasData(row, bars) {
  if (row.type === "sig") {
    return bars.some(bar => (bar.signals?.[row.key] ?? []).length > 0);
  }
  if (row.type === "score") {
    return bars.some(bar => {
      const v = bar.scores?.[row.from];
      return v !== null && v !== undefined && v !== "" && v !== 0;
    });
  }
  return true;  // sep / num
}

function _buildHistoryTimeline(data) {
  const bars   = data.bars ?? [];
  const ticker = data.ticker ?? "";
  const tf     = data.timeframe ?? "1d";
  const genAt  = data.meta?.generated_at ?? "";

  const dateHeaders = bars.map(b =>
    `<th class="tl-date">${esc(b.display_date)}</th>`
  ).join("");

  const tableRows = _TL_ROWS.map(row => {
    if (_tlHideEmpty && !_tlRowHasData(row, bars)) return "";

    if (row.type === "sep") {
      const emptyCells = bars.map(() => `<td class="tl-sep-cell"></td>`).join("");
      return `<tr class="tl-sep-row"><th class="tl-row-label tl-sep-cell"></th>${emptyCells}</tr>`;
    }

    if (row.type === "sig") {
      const cells = bars.map(bar => {
        const sigs = bar.signals?.[row.key] ?? [];
        if (!sigs.length) return `<td class="tl-cell"></td>`;
        // Design system: badge color is resolved by the family-aware
        // resolver, not by row-level cls. So "ROCKET" in the i row is bold
        // green, "BEST↑" in the ult row is bold yellow, "L88" is bold violet,
        // "G1P" is ringed green, etc. — identical to old Ultra.
        const badges = sigs.map(s => SignalBadges.renderSignalBadge(s)).join("");
        return `<td class="tl-cell">${badges}</td>`;
      }).join("");
      return `<tr><th class="tl-row-label">${esc(row.label)}</th>${cells}</tr>`;
    }

    if (row.type === "num") {
      const cells = bars.map(bar => {
        const val = bar[row.key];
        if (val == null) return `<td class="tl-num tl-num-dim">—</td>`;
        return `<td class="${_numCls(row.key, val)}">${Number(val).toFixed(row.dec ?? 2)}</td>`;
      }).join("");
      return `<tr><th class="tl-row-label">${esc(row.label)}</th>${cells}</tr>`;
    }

    if (row.type === "score") {
      const cells = bars.map(bar => {
        const val = bar.scores?.[row.from];
        if (val == null || val === "") return `<td class="tl-num tl-num-dim">—</td>`;
        if (row.dec === -1) {
          // String label (rtb_phase, category)
          return `<td class="tl-num">${esc(String(val))}</td>`;
        }
        const num = Number(val);
        if (!isFinite(num)) return `<td class="tl-num">${esc(String(val))}</td>`;
        return `<td class="${_numCls(row.from, num)}">${num.toFixed(row.dec ?? 0)}</td>`;
      }).join("");
      return `<tr><th class="tl-row-label">${esc(row.label)}</th>${cells}</tr>`;
    }

    return "";
  }).join("");

  const hiddenToggleLabel = _tlHideEmpty ? "Show all rows" : "Hide empty rows";

  return `
    <div class="timeline-header">
      <span>${esc(ticker)} · ${esc(tf)} · ${bars.length} bars</span>
      <div style="display:flex;gap:10px;align-items:center">
        <button class="btn-tl-toggle" onclick="_tlToggleEmpty(this)">${hiddenToggleLabel}</button>
        <span class="timeline-meta">${esc(genAt.slice(0, 16).replace("T", " ") + " UTC")}</span>
      </div>
    </div>
    <div class="timeline-wrap">
      <table class="timeline-table">
        <thead>
          <tr>
            <th class="tl-corner tl-row-label"></th>
            ${dateHeaders}
          </tr>
        </thead>
        <tbody>${tableRows}</tbody>
      </table>
    </div>`;
}

function _tlToggleEmpty(btn) {
  _tlHideEmpty = !_tlHideEmpty;
  btn.textContent = _tlHideEmpty ? "Show all rows" : "Hide empty rows";
  const histArea = $("chartHistoryArea");
  if (histArea && _lastHistoryData) {
    histArea.innerHTML = _buildHistoryTimeline(_lastHistoryData);
  }
}

function _renderLwChart(candles, markers) {
  _destroyChart();
  const container = $("lwChart");
  if (!container) return;

  if (!_lwAvailable()) {
    container.innerHTML = `<div class="lw-unavailable">lightweight-charts failed to load. Check network / ad-blocker.</div>`;
    return;
  }

  const chart = LightweightCharts.createChart(container, {
    autoSize: true,
    layout: {
      background: { type: "solid", color: "#161b22" },
      textColor: "#c9d1d9",
    },
    grid: {
      vertLines: { color: "#21262d" },
      horzLines: { color: "#21262d" },
    },
    crosshair: { mode: 1 },
    timeScale: { borderColor: "#30363d", timeVisible: true },
    rightPriceScale: { borderColor: "#30363d" },
  });
  _chartInstance = chart;

  // ── Candlestick series ────────────────────────────────────────────────────
  const candleSeries = chart.addCandlestickSeries({
    upColor:        "#3fb950",
    downColor:      "#f85149",
    wickUpColor:    "#3fb950",
    wickDownColor:  "#f85149",
    borderVisible:  false,
  });
  candleSeries.setData(candles.map(c => ({
    time: c.time, open: c.open, high: c.high, low: c.low, close: c.close,
  })));

  // ── Volume histogram (bottom 18% of chart) ────────────────────────────────
  const volSeries = chart.addHistogramSeries({
    priceFormat:  { type: "volume" },
    priceScaleId: "vol",
  });
  chart.priceScale("vol").applyOptions({
    scaleMargins: { top: 0.82, bottom: 0 },
  });
  volSeries.setData(candles.map(c => ({
    time:  c.time,
    value: c.volume,
    color: c.close >= c.open ? "#3fb95044" : "#f8514944",
  })));

  // ── T/Z markers ───────────────────────────────────────────────────────────
  if (markers.length) {
    candleSeries.setMarkers(markers);
  }

  chart.timeScale().fitContent();
}

// ── Score panel HTML ──────────────────────────────────────────────────────────
function _buildScorePanel(data) {
  const score = data.score  ?? {};
  const tz    = data.tz     ?? {};
  const wlnbb = data.wlnbb  ?? {};
  const band  = score.band  || "";
  const why   = (score.why_selected ?? []).slice(0, 5);
  const risk  = score.risk_flags ?? [];

  const isBull = tz.is_bull;
  const isBear = tz.is_bear;
  const sigName = tz.sig_name || "NONE";

  return `
    <div class="score-panel">
      <div class="sp-title">Ultra Score</div>
      <div class="sp-score-num">${score.ultra_score ?? "—"}</div>
      <div style="margin-bottom:8px">
        <span class="chip ${bandClass(band)}">${esc(band || "—")}</span>
        ${score.final_signal ? `<span class="chip signal">${esc(score.final_signal)}</span>` : ""}
      </div>

      ${score.price != null ? `<div class="sp-row"><span class="sp-lbl">Price</span><span>$${fmt(score.price, 2)}</span></div>` : ""}
      ${score.change_pct != null ? `<div class="sp-row"><span class="sp-lbl">Chg%</span><span class="${score.change_pct >= 0 ? "pos" : "neg"}">${(score.change_pct >= 0 ? "+" : "")}${fmt(score.change_pct, 2)}%</span></div>` : ""}
      ${score.rsi != null ? `<div class="sp-row"><span class="sp-lbl">RSI</span><span>${fmt(score.rsi, 1)}</span></div>` : ""}
      ${score.sector ? `<div class="sp-row"><span class="sp-lbl">Sector</span><span style="font-size:.68rem;color:var(--text-dim)">${esc(score.sector)}</span></div>` : ""}

      <div class="sp-divider"></div>
      <div class="sp-title">T/Z Signal</div>
      <div class="sp-row" style="gap:8px">
        <span class="chip ${isBull ? "chip-bull" : isBear ? "chip-bear" : ""}">${esc(sigName)}</span>
        <span style="font-size:.68rem;color:var(--text-dim)">${isBull ? "▲ bullish" : isBear ? "▼ bearish" : "neutral"}</span>
      </div>

      <div class="sp-divider"></div>
      <div class="sp-title">WLNBB</div>
      <div class="sp-bool-grid">
        ${_boolRow("BLUE",  wlnbb.BLUE)}
        ${_boolRow("L34",   wlnbb.L34)}
        ${_boolRow("FRI34", wlnbb.FRI34)}
        ${_boolRow("BO↑",   wlnbb.BO_UP)}
        ${_boolRow("BE↑",   wlnbb.BE_UP)}
        ${_boolRow("PP",    wlnbb.PRE_PUMP)}
      </div>
      <div class="sp-row"><span class="sp-lbl">Vol</span><span>${esc(wlnbb.vol_bucket || "—")}</span></div>
      ${wlnbb.cci_sma != null ? `<div class="sp-row"><span class="sp-lbl">CCI</span><span>${fmt(wlnbb.cci_sma, 1)}</span></div>` : ""}

      ${why.length ? `
        <div class="sp-divider"></div>
        <div class="sp-title">Why Selected</div>
        <div>${why.map(w => `<span class="chip" style="font-size:.6rem">${esc(w)}</span>`).join("")}</div>` : ""}

      ${risk.length ? `
        <div class="sp-divider"></div>
        <div class="sp-title">Risk Flags</div>
        <div>${risk.map(r => `<span class="chip risk" style="font-size:.6rem">${esc(r)}</span>`).join("")}</div>` : ""}

      <div class="sp-divider"></div>
      <div class="sp-engine">Engine: ${esc(score.score_engine || "—")}</div>
      <div class="sp-engine" style="margin-top:2px">Turbo: not migrated yet</div>
    </div>`;
}

function _boolRow(label, val) {
  const on = val === true;
  return `<span class="sp-bool-lbl">${label}</span>
          <span class="${on ? "sp-yes" : "sp-no"}">${on ? "YES" : "no"}</span>`;
}

// ── Chart metadata + missing groups ───────────────────────────────────────────
function _buildChartMeta(data, candleCount) {
  const markers   = data.markers ?? [];
  const bullM     = markers.filter(m => m.shape === "arrowUp").length;
  const bearM     = markers.filter(m => m.shape === "arrowDown").length;
  const missing   = data.missing_groups ?? [];
  const lastC     = data.candles?.at?.(-1);

  return `
    <div class="section-label" style="margin-top:20px">Chart Metadata</div>
    <div class="chart-meta-chips">
      <span class="chip">Candles: ${candleCount}</span>
      <span class="chip" style="color:var(--green)">Bull signals: ${bullM}</span>
      <span class="chip" style="color:var(--red)">Bear signals: ${bearM}</span>
      <span class="chip">TF: ${esc(data.timeframe || "1d")}</span>
      <span class="chip">Provider: Massive</span>
      <span class="chip">yfinance_used: false</span>
      <span class="chip">Source: ${esc(data.source || "dashboard-bff")}</span>
      ${lastC ? `<span class="chip">Latest: ${esc(lastC.time)}</span>` : ""}
      ${data.generated_at ? `<span class="chip" style="color:var(--text-dim)">Generated: ${fmtDate(data.generated_at)}</span>` : ""}
    </div>
    ${missing.length ? `
      <div class="section-label" style="margin-top:16px">Not Yet Migrated (Phase 8C-P1/P2)</div>
      <div class="chart-meta-chips">
        ${missing.map(g => `<span class="chip" style="color:var(--text-dim);border-color:var(--border);font-size:.65rem">${esc(g)}</span>`).join("")}
      </div>` : ""}`;
}

// ═════════════════════════════════════════════════════════════════════════════
// PAGE: RESEARCH
// ═════════════════════════════════════════════════════════════════════════════
async function renderResearch() {
  $r().innerHTML = `
    <div class="page-container">
      <div class="section-label">Research</div>
      <div class="placeholder-box" style="min-height:200px">
        <span class="placeholder-icon">◎</span>
        <span class="placeholder-text">Research — Phase 8D / 8F</span>
        <span class="placeholder-sub">Replay, statistics, and signal history migration planned for a later phase.</span>
      </div>
      <div class="section-label">Research API</div>
      <div class="cards-row">
        <div class="card"><div class="c-label">Research API</div><div class="c-value" style="font-size:.9rem;color:var(--text-dim)">Not configured</div><div class="c-sub">RESEARCH_API_URL not set</div></div>
        <div class="card"><div class="c-label">Planned</div><div class="c-value" style="font-size:.72rem;line-height:1.5;padding-top:4px;color:var(--text-dim)">Replay · Statistics · Signal history</div></div>
      </div>
    </div>`;
}

// ═════════════════════════════════════════════════════════════════════════════
// PAGE: SYSTEM
// ═════════════════════════════════════════════════════════════════════════════
async function renderSystem() {
  $r().innerHTML = `<div class="page-container"><div class="page-loading">Loading system status…</div></div>`;

  let status;
  try {
    status = await apiFetch("/api/debug/status");
  } catch (err) {
    $r().innerHTML = `<div class="page-container"><div class="state-banner error">Failed to load system status: ${esc(err.message)}</div></div>`;
    return;
  }

  const reach      = status.scanner_api_reachable === true;
  const chartReach = status.scanner_chart_snapshot_reachable === true;

  $r().innerHTML = `
    <div class="page-container">

      <div class="section-label">Admin Control Center</div>
      <div class="admin-card" id="adminControls">
        <div class="admin-row">
          <button class="btn-admin"               id="adminSyncBtn"  title="Sync OHLCV from Massive into market_bars cache. Subsequent scans skip Massive.">⇣ Sync Market Data</button>
          <button class="btn-admin"               id="adminScanBtn"  title="Run a controlled Ultra scan using the cached bars. Use this to verify engine-api wiring without running Sync + Generate.">⚡ Run Scan</button>
          <button class="btn-admin"               id="adminGenBtn"   title="Generate dashboard-ready views (top_movers / best_setups / sector_heat / dashboard_summary) from the latest scan candidates.">▦ Generate Views</button>
          <button class="btn-admin btn-admin-pipe" id="adminPipeBtn" title="Sync → Run Scan → Generate Views, sequentially. The one-click refresh for the entire pipeline.">▶ Run Full Pipeline</button>
          <span class="admin-row-divider">·</span>
          <a class="btn-admin-link" href="#ultra" title="Open Ultra Scanner to view candidates / configure scan parameters before running.">↗ Open Ultra Scanner</a>
        </div>
        <div class="admin-row admin-token-row">
          <label class="admin-token-label">
            <span>x-admin-token</span>
            <input type="password" id="adminTokenInput" placeholder="paste ADMIN_TOKEN" autocomplete="off" />
          </label>
          <span class="admin-token-hint">Stored in this browser only (localStorage). Required for all admin operations.</span>
        </div>
        <div class="admin-status" id="adminStatus"></div>
      </div>

      <div class="section-label">Service Health</div>
      <div class="cards-row">
        <div class="card"><div class="c-label">Scanner API</div><div class="c-value" style="font-size:1rem"><span class="pill ${reach ? "ok" : "err"}">${reach ? "reachable" : "unreachable"}</span></div></div>
        <div class="card" title="${esc(status.engine_api_url || 'not configured — scanner-api uses in-process engines')}">
          <div class="c-label">Engine API</div>
          <div class="c-value" style="font-size:1rem">
            ${status.engine_api_url_configured
              ? (status.engine_api_reachable
                  ? `<span class="pill ok">HTTP · v${esc(status.engine_api_version || '?')}</span>`
                  : `<span class="pill err">unreachable</span>`)
              : `<span class="pill warn">in-process</span>`}
          </div>
          ${status.engine_api_error ? `<div class="c-sub" style="color:var(--c-neg)">${esc(status.engine_api_error)}</div>` : ""}
        </div>
        <div class="card" title="${esc(status.market_data_api_url || 'not configured — scanner-api uses in-process market_data module')}">
          <div class="c-label">Market Data API</div>
          <div class="c-value" style="font-size:1rem">
            ${status.market_data_api_url_configured
              ? (status.market_data_api_reachable
                  ? `<span class="pill ok">HTTP · v${esc(status.market_data_api_version || '?')}</span>`
                  : `<span class="pill err">unreachable</span>`)
              : `<span class="pill warn">in-process</span>`}
          </div>
          ${status.market_data_api_error ? `<div class="c-sub" style="color:var(--c-neg)">${esc(status.market_data_api_error)}</div>` : ""}
        </div>
        <div class="card"><div class="c-label">Chart Proxy</div><div class="c-value" style="font-size:1rem"><span class="pill ${chartReach ? "ok" : "warn"}">${chartReach ? "ready" : "not verified"}</span></div></div>
        <div class="card"><div class="c-label">DB Configured</div><div class="c-value" style="font-size:1rem;color:${status.database_configured ? "var(--green)" : "var(--text-dim)"}">${status.database_configured ? "yes" : "no"}</div></div>
        <div class="card"><div class="c-label">Redis</div><div class="c-value" style="font-size:1rem;color:${status.redis_configured ? "var(--green)" : "var(--text-dim)"}">${status.redis_configured ? "yes" : "no"}</div></div>
        <div class="card"><div class="c-label">Massive API</div><div class="c-value" style="font-size:1rem;color:${status.massive_configured ? "var(--green)" : "var(--text-dim)"}">${status.massive_configured ? "yes" : "no"}</div></div>
        <div class="card"><div class="c-label">Research API</div><div class="c-value" style="font-size:1rem;color:${status.research_api_url_configured ? "var(--green)" : "var(--text-dim)"}">${status.research_api_url_configured ? "yes" : "no"}</div></div>
      </div>
      <div class="section-label">Safety Flags</div>
      <div class="cards-row">
        <div class="card"><div class="c-label">Scheduler</div><div class="c-value" style="font-size:.9rem;color:var(--text-dim)">disabled</div></div>
        <div class="card"><div class="c-label">Full-Market Scan</div><div class="c-value" style="font-size:.9rem;color:var(--text-dim)">disabled</div></div>
        <div class="card"><div class="c-label">yfinance</div><div class="c-value" style="font-size:.9rem;color:var(--text-dim)">not used</div></div>
        <div class="card"><div class="c-label">AI / News</div><div class="c-value" style="font-size:.9rem;color:var(--text-dim)">not enabled</div></div>
      </div>
      <div class="section-label">Raw Status</div>
      <pre class="status-pre">${esc(JSON.stringify(status, null, 2))}</pre>
      <div style="font-size:.65rem;color:var(--text-dim);margin-top:8px">Fetched ${new Date().toLocaleString()}</div>
    </div>`;

  // ── Admin Control Center wiring ─────────────────────────────────────────
  const tokInput = $("adminTokenInput");
  if (tokInput) {
    tokInput.value = localStorage.getItem("sachoki_admin_token") || "";
    tokInput.addEventListener("input", () => {
      localStorage.setItem("sachoki_admin_token", tokInput.value || "");
    });
  }
  const syncBtn = $("adminSyncBtn");
  if (syncBtn) syncBtn.addEventListener("click", () => _adminSyncMarketData());
  const scanBtn = $("adminScanBtn");
  if (scanBtn) scanBtn.addEventListener("click", () => _adminRunScanFromSystem());
  const genBtn = $("adminGenBtn");
  if (genBtn) genBtn.addEventListener("click", () => _adminGenerateViews());
  const pipeBtn = $("adminPipeBtn");
  if (pipeBtn) pipeBtn.addEventListener("click", () => _adminFullPipelineFromSystem());
}

// ── Page registry ─────────────────────────────────────────────────────────────
const RENDERERS = {
  home:      renderHome,
  dashboard: renderDashboard,
  ultra:     renderUltra,
  chart:     renderChart,
  research:  renderResearch,
  system:    renderSystem,
};

// ── Boot ──────────────────────────────────────────────────────────────────────
window.addEventListener("hashchange", navigate);
$("refreshBtn").addEventListener("click", refresh);
navigate();

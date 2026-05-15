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
      <div class="state-banner ${bannerCls}">${esc(bannerMsg)}</div>
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

// ── Scan controls: sample lists + controls init ───────────────────────────────
async function loadSampleListsAndInit() {
  const ctrlEl = $("scanControls");
  if (!ctrlEl) return;
  try {
    const d = await apiFetch("/api/dashboard/scans/ultra/sample-lists");
    const lists = d.lists ?? {};
    const counts = {
      sp500_sample:  (lists.sp500_sample  ?? []).length,
      nasdaq_sample: (lists.nasdaq_sample ?? []).length,
      manual_test:   (lists.manual_test   ?? []).length,
    };
    const univ = $("scUniverse");
    if (univ) {
      univ.querySelector('option[value="sp500_sample"]').textContent  = `S&P 500 Sample (${counts.sp500_sample})`;
      univ.querySelector('option[value="nasdaq_sample"]').textContent = `NASDAQ Sample (${counts.nasdaq_sample})`;
      univ.querySelector('option[value="manual_default"]').textContent = `Manual List (${counts.manual_test})`;
    }
  } catch { /* leave default labels */ }
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
            <select id="scCount">
              <option value="10">10</option>
              <option value="25" selected>25</option>
              <option value="50">50</option>
              <option value="100">100</option>
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
        <button class="btn-clear" id="clearFilters">Clear</button>
        <span class="filter-count" id="filterCount"></span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th class="td-rank">#</th><th>Symbol</th><th>Sector</th>
            <th>Price</th><th>Chg%</th><th>Score</th><th>Band</th>
            <th>Signal</th><th>Why Selected</th><th>Risk Flags</th><th></th>
          </tr></thead>
          <tbody id="candidatesBody"></tbody>
        </table>
      </div>
    </div>`;

  applyUltraFilters();
  ["fSearch","fBand","fSector","fMinScore"].forEach(id => {
    const el = $(id);
    if (el) el.addEventListener("input", applyUltraFilters);
  });
  const clr = $("clearFilters");
  if (clr) clr.addEventListener("click", () => {
    ["fSearch","fBand","fSector","fMinScore"].forEach(id => { const el=$(id); if(el) el.value=""; });
    applyUltraFilters();
  });

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

  const filtered = _ultraCandidates.filter(c => {
    if (search && !c.symbol?.toUpperCase().includes(search)) return false;
    if (band   && c.band !== band)     return false;
    if (sector && c.sector !== sector) return false;
    if ((c.ultra_score ?? 0) < minScore) return false;
    return true;
  });

  const fc = $("filterCount");
  if (fc) fc.textContent = `${filtered.length} / ${_ultraCandidates.length} shown`;
  renderCandidateTable(filtered);
}

function renderCandidateTable(candidates) {
  const body = $("candidatesBody");
  if (!body) return;
  if (!candidates.length) {
    body.innerHTML = `<tr class="empty-row"><td colspan="11">No candidates match the current filters.</td></tr>`;
    return;
  }
  body.innerHTML = candidates.map((c, i) => {
    const chgVal = c.change_pct;
    const chgTxt = chgVal != null ? (chgVal >= 0 ? "+" : "") + fmt(chgVal, 2) + "%" : "—";
    const chgCls = chgVal == null ? "" : chgVal >= 0 ? "pos" : "neg";
    const why    = (c.why_selected ?? []).slice(0, 3).map(w => `<span class="chip" style="font-size:.6rem">${esc(w)}</span>`).join(" ");
    const risk   = (c.risk_flags  ?? []).map(r => `<span class="chip risk" style="font-size:.6rem">${esc(r)}</span>`).join(" ");
    return `<tr>
      <td class="td-rank">${i + 1}</td>
      <td class="td-sym">${esc(c.symbol)}</td>
      <td>${esc(c.sector || "—")}</td>
      <td class="td-price">${c.price != null ? "$" + fmt(c.price, 2) : "—"}</td>
      <td class="td-chg ${chgCls}">${chgTxt}</td>
      <td class="td-score">${c.ultra_score ?? "—"}</td>
      <td><span class="chip ${bandClass(c.band)}">${esc(c.band || "—")}</span></td>
      <td>${c.final_signal ? `<span class="chip signal">${esc(c.final_signal)}</span>` : "—"}</td>
      <td class="td-why">${why || "—"}</td>
      <td class="td-risk">${risk || "—"}</td>
      <td><button class="btn-chart" onclick="openChart('${esc(c.symbol)}')" title="Open in Superchart">◈</button></td>
    </tr>`;
  }).join("");
}

// ═════════════════════════════════════════════════════════════════════════════
// PAGE: SUPERCHART
// ═════════════════════════════════════════════════════════════════════════════
async function renderChart() {
  const initialSym = _pendingChartSym || "";
  const autoLoad   = Boolean(_pendingChartSym);
  _pendingChartSym = null;

  $r().innerHTML = `
    <div class="page-container">

      <div class="chart-header">
        <div>
          <div class="section-label" style="margin:0 0 6px">Superchart Preview</div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
            <span class="pill ok">Massive</span>
            <span class="chip">1D Completed Candles</span>
            <span class="chip" style="color:var(--text-dim)">yfinance_used=false</span>
          </div>
        </div>
        <div class="chart-controls">
          <label class="chart-label">Symbol
            <input id="chartSym" type="text" placeholder="AAPL" maxlength="7"
              class="chart-input" value="${esc(initialSym)}" autocomplete="off" />
          </label>
          <label class="chart-label">Timeframe
            <select id="chartTf" class="chart-select">
              <option value="1d" selected>1D Daily</option>
            </select>
          </label>
          <label class="chart-label">Bars
            <select id="chartBars" class="chart-select">
              <option value="50">50</option>
              <option value="100">100</option>
              <option value="150" selected>150</option>
              <option value="200">200</option>
            </select>
          </label>
          <button id="chartLoadBtn" class="btn-refresh" style="align-self:flex-end;padding:6px 20px;font-size:.82rem">
            Load Chart
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

      <div id="chartPlaceholder" class="placeholder-box" style="margin-top:12px">
        <span class="placeholder-icon">◈</span>
        <span class="placeholder-text">Enter a ticker and click Load Chart</span>
        <span class="placeholder-sub">P0 Superchart Preview · Massive · No yfinance · dashboard BFF only</span>
      </div>

    </div>`;

  const btn = $("chartLoadBtn");
  if (btn) btn.addEventListener("click", loadChartSnapshot);
  const inp = $("chartSym");
  if (inp) inp.addEventListener("keydown", e => { if (e.key === "Enter") loadChartSnapshot(); });

  if (autoLoad && initialSym) await loadChartSnapshot();
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
      <div class="section-label">Service Health</div>
      <div class="cards-row">
        <div class="card"><div class="c-label">Scanner API</div><div class="c-value" style="font-size:1rem"><span class="pill ${reach ? "ok" : "err"}">${reach ? "reachable" : "unreachable"}</span></div></div>
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

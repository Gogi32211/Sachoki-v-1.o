"use strict";

// ── State ─────────────────────────────────────────────────────────────────────
let _bootstrap = null;   // cached bootstrap response
let _busy      = false;
let _page      = null;   // current page key

// ── DOM shortcuts ─────────────────────────────────────────────────────────────
const $  = id => document.getElementById(id);
const $r = () => $("pageRoot");

// ── Utilities ─────────────────────────────────────────────────────────────────
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt     = (n, d = 2)  => n != null ? Number(n).toFixed(d) : "—";
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

// ── Bootstrap (shared across pages) ──────────────────────────────────────────
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

  const meta = $("cmdMeta");
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

// ── Router ────────────────────────────────────────────────────────────────────
const PAGES = ["home", "dashboard", "ultra", "chart", "research", "system"];

function currentPage() {
  const hash = location.hash.replace(/^#\/?/, "").toLowerCase();
  return PAGES.includes(hash) ? hash : "home";
}

async function navigate() {
  clearError();
  _page = currentPage();
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
    _bootstrap = null;  // force re-fetch
    await navigate();
  } catch (err) {
    showError(`Refresh failed: ${err.message}`);
  } finally {
    _busy = false;
    if (btn) { btn.disabled = false; btn.classList.remove("spinning"); }
  }
}

// ═════════════════════════════════════════════════════════════════════════════
// PAGE: HOME
// ═════════════════════════════════════════════════════════════════════════════
async function renderHome() {
  let data;
  try {
    data = await ensureBootstrap();
  } catch {
    $r().innerHTML = homeError();
    return;
  }

  const state  = data.dashboard_state ?? "ERROR";
  const scan   = data.latest_scan ?? {};
  const sum    = data.summary ?? {};
  const health = data.data_health ?? {};
  const sapi   = health.scanner_api ?? {};
  const reach  = sapi.reachable === true;

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
        <div class="card"><div class="c-label">Bands</div><div class="c-value">${Object.keys(sum.bands ?? {}).length}</div><div class="c-sub">active bands</div></div>
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
        <div class="card">
          <div class="c-label">Scanner API</div>
          <div class="c-value" style="font-size:1rem">
            <span class="pill ${reach ? "ok" : "err"}">${reach ? "reachable" : "unreachable"}</span>
          </div>
        </div>
        <div class="card">
          <div class="c-label">Last Scan</div>
          <div class="c-value" style="font-size:.8rem;line-height:1.4">${fmtDate(scan.finished_at)}</div>
        </div>
        <div class="card">
          <div class="c-label">Scan Status</div>
          <div class="c-value" style="font-size:1rem">${esc(scan.status ?? "—")}</div>
        </div>
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
  const data = await ensureBootstrap();
  const movers = data.top_movers?.regular ?? {};
  const setups = data.best_setups ?? [];
  const sum    = data.summary ?? {};

  $r().innerHTML = `
    <div class="page-container">
      <div class="section-label">Top Movers</div>
      <div class="movers-row">
        <div class="movers-box">
          <div class="m-title gain-title">Top Gainers</div>
          <div id="gainersTable">${renderMoversList(movers.gainers ?? [])}</div>
        </div>
        <div class="movers-box">
          <div class="m-title loss-title">Top Losers</div>
          <div id="losersTable">${renderMoversList(movers.losers ?? [])}</div>
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
      <div class="s-sym">${esc(s.symbol)}</div>
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

async function renderUltra() {
  const data = await ensureBootstrap();
  const scan = data.latest_scan ?? {};
  _ultraCandidates = data.top_candidates ?? [];

  const sectors = [...new Set(_ultraCandidates.map(c => c.sector || "").filter(Boolean))].sort();
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

      <div class="section-label">Scan Controls</div>
      <div class="placeholder-box">
        <span class="placeholder-icon">⚡</span>
        <span class="placeholder-text">Scan controls — Phase 8D</span>
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
            <th>Signal</th><th>Why Selected</th><th>Risk Flags</th>
          </tr></thead>
          <tbody id="candidatesBody"></tbody>
        </table>
      </div>
    </div>`;

  // Bind filters
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
    body.innerHTML = `<tr class="empty-row"><td colspan="10">No candidates match the current filters.</td></tr>`;
    return;
  }
  body.innerHTML = candidates.map((c, i) => {
    const chgVal = c.change_pct;
    const chgTxt = chgVal != null ? (chgVal >= 0 ? "+" : "") + fmt(chgVal, 2) + "%" : "—";
    const chgCls = chgVal == null ? "" : chgVal >= 0 ? "pos" : "neg";
    const why  = (c.why_selected ?? []).slice(0, 3).map(w => `<span class="chip" style="font-size:.6rem">${esc(w)}</span>`).join(" ");
    const risk = (c.risk_flags  ?? []).map(r => `<span class="chip risk" style="font-size:.6rem">${esc(r)}</span>`).join(" ");
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
    </tr>`;
  }).join("");
}

// ═════════════════════════════════════════════════════════════════════════════
// PAGE: SUPERCHART
// ═════════════════════════════════════════════════════════════════════════════
async function renderChart() {
  $r().innerHTML = `
    <div class="page-container">
      <div class="section-label">Superchart</div>

      <div class="chart-controls">
        <label class="chart-label">Symbol
          <input id="chartSym" type="text" placeholder="AAPL" maxlength="6" autocomplete="off" class="chart-input" />
        </label>
        <label class="chart-label">Timeframe
          <select id="chartTf" class="chart-select">
            <option value="1d" selected>1D Daily</option>
          </select>
        </label>
        <label class="chart-label">Bars
          <input id="chartBars" type="number" value="150" min="20" max="250" class="chart-input" style="width:70px" />
        </label>
        <button id="chartLoadBtn" class="btn-refresh" style="align-self:flex-end">Load</button>
      </div>

      <div id="chartResult" class="chart-result">
        <div class="placeholder-box" style="margin-top:16px">
          <span class="placeholder-icon">◈</span>
          <span class="placeholder-text">Enter a ticker and click Load to fetch chart data</span>
        </div>
      </div>
    </div>`;

  const btn = $("chartLoadBtn");
  if (btn) btn.addEventListener("click", loadChartSnapshot);

  const inp = $("chartSym");
  if (inp) inp.addEventListener("keydown", e => { if (e.key === "Enter") loadChartSnapshot(); });
}

async function loadChartSnapshot() {
  const sym  = ($("chartSym")?.value ?? "").trim().toUpperCase();
  const tf   = $("chartTf")?.value ?? "1d";
  const bars = parseInt($("chartBars")?.value ?? "150", 10);
  const result = $("chartResult");

  if (!sym || !/^[A-Z]{1,5}(-[A-Z]{1,2})?$/.test(sym)) {
    if (result) result.innerHTML = `<div class="state-banner error" style="margin-top:12px">Invalid ticker symbol.</div>`;
    return;
  }

  if (result) result.innerHTML = `<div class="page-loading">Fetching ${sym} snapshot…</div>`;

  try {
    const data = await apiFetch("/api/dashboard/chart/snapshot", { symbol: sym, tf, bars });

    if (!data.ok) {
      result.innerHTML = `<div class="state-banner error" style="margin-top:12px">${esc(data.error ?? "Snapshot failed")}</div>`;
      return;
    }

    const score  = data.score  ?? {};
    const tz     = data.tz     ?? {};
    const wlnbb  = data.wlnbb  ?? {};
    const cands  = data.candles ?? [];
    const marks  = data.markers ?? [];
    const missing = data.missing_groups ?? [];

    const bullMarks = marks.filter(m => m.shape === "arrowUp").length;
    const bearMarks = marks.filter(m => m.shape === "arrowDown").length;

    const sigItems = missing.map(g => `<li class="dim-text" style="font-size:.72rem">${esc(g)}</li>`).join("");

    result.innerHTML = `
      <div class="section-label" style="margin-top:16px">Score Panel — ${esc(sym)}</div>
      <div class="cards-row">
        <div class="card"><div class="c-label">Ultra Score</div><div class="c-value" style="color:var(--green)">${score.ultra_score ?? "—"}</div><div class="c-sub">Band ${esc(score.band ?? "—")}</div></div>
        <div class="card"><div class="c-label">Price</div><div class="c-value" style="font-size:1rem">${score.price != null ? "$" + fmt(score.price, 2) : "—"}</div><div class="c-sub">${score.change_pct != null ? (score.change_pct >= 0 ? "+" : "") + fmt(score.change_pct, 2) + "%" : "—"}</div></div>
        <div class="card"><div class="c-label">RSI</div><div class="c-value">${score.rsi != null ? fmt(score.rsi, 1) : "—"}</div></div>
        <div class="card"><div class="c-label">T/Z Signal</div><div class="c-value" style="font-size:.9rem">${esc(tz.sig_name ?? "NONE")}</div><div class="c-sub">${tz.is_bull ? "bullish" : tz.is_bear ? "bearish" : "neutral"}</div></div>
        <div class="card"><div class="c-label">Vol Bucket</div><div class="c-value">${esc(wlnbb.vol_bucket ?? "—")}</div></div>
        <div class="card"><div class="c-label">Candles</div><div class="c-value">${data.bars_returned ?? "—"}</div><div class="c-sub">${tf} bars</div></div>
      </div>

      <div class="section-label">WLNBB State</div>
      <div class="cards-row">
        ${renderBoolCard("BLUE",     wlnbb.BLUE)}
        ${renderBoolCard("L34",      wlnbb.L34)}
        ${renderBoolCard("FRI34",    wlnbb.FRI34)}
        ${renderBoolCard("BO↑",      wlnbb.BO_UP)}
        ${renderBoolCard("BE↑",      wlnbb.BE_UP)}
        ${renderBoolCard("PRE PUMP", wlnbb.PRE_PUMP)}
      </div>

      <div class="section-label">Signal Markers</div>
      <div class="cards-row">
        <div class="card"><div class="c-label">Bull Signals</div><div class="c-value" style="color:var(--green)">${bullMarks}</div><div class="c-sub">arrowUp markers</div></div>
        <div class="card"><div class="c-label">Bear Signals</div><div class="c-value" style="color:var(--red)">${bearMarks}</div><div class="c-sub">arrowDown markers</div></div>
        <div class="card"><div class="c-label">Sector</div><div class="c-value" style="font-size:.8rem;padding-top:4px">${esc(score.sector ?? "—")}</div></div>
      </div>

      ${(score.why_selected ?? []).length ? `
        <div class="section-label">Why Selected</div>
        <div class="why-chips">${(score.why_selected).map(w => `<span class="chip">${esc(w)}</span>`).join("")}</div>` : ""}

      ${(score.risk_flags ?? []).length ? `
        <div class="section-label">Risk Flags</div>
        <div class="why-chips">${(score.risk_flags).map(r => `<span class="chip risk">${esc(r)}</span>`).join("")}</div>` : ""}

      <div class="section-label">Signal Groups — Not Yet Implemented</div>
      <ul class="missing-list">${sigItems || "<li class='dim-text' style='font-size:.72rem'>All groups implemented.</li>"}</ul>

      <div style="font-size:.65rem;color:var(--text-dim);margin-top:12px">
        Generated ${fmtDate(data.generated_at)} · source: ${esc(data.source ?? "—")} · proxied from: ${esc(data.proxied_from ?? "scanner-api")}
      </div>`;

  } catch (err) {
    if (result) result.innerHTML = `<div class="state-banner error" style="margin-top:12px">Failed: ${esc(err.message)}</div>`;
  }
}

function renderBoolCard(label, val) {
  const on = val === true;
  return `<div class="card">
    <div class="c-label">${esc(label)}</div>
    <div class="c-value" style="font-size:1rem;color:${on ? "var(--green)" : "var(--text-dim)"}">${on ? "YES" : "no"}</div>
  </div>`;
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
        <div class="card">
          <div class="c-label">Research API</div>
          <div class="c-value" style="font-size:.9rem;color:var(--text-dim)">Not configured</div>
          <div class="c-sub">RESEARCH_API_URL not set</div>
        </div>
        <div class="card">
          <div class="c-label">Planned Features</div>
          <div class="c-value" style="font-size:.72rem;line-height:1.5;padding-top:4px;color:var(--text-dim)">Replay · Statistics · Signal history</div>
        </div>
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

  const bool = (v, trueLabel = "yes", falseLabel = "no") => {
    const on = v === true;
    return `<span style="color:${on ? "var(--green)" : "var(--text-dim)"}">${on ? trueLabel : falseLabel}</span>`;
  };
  const reach = status.scanner_api_reachable === true;
  const chartReach = status.scanner_chart_snapshot_reachable === true;

  $r().innerHTML = `
    <div class="page-container">
      <div class="section-label">Service Health</div>
      <div class="cards-row">
        <div class="card">
          <div class="c-label">Scanner API</div>
          <div class="c-value" style="font-size:1rem">
            <span class="pill ${reach ? "ok" : "err"}">${reach ? "reachable" : "unreachable"}</span>
          </div>
        </div>
        <div class="card">
          <div class="c-label">Chart Proxy</div>
          <div class="c-value" style="font-size:1rem">
            <span class="pill ${chartReach ? "ok" : "warn"}">${chartReach ? "ready" : "not verified"}</span>
          </div>
        </div>
        <div class="card"><div class="c-label">DB Configured</div><div class="c-value" style="font-size:1rem">${bool(status.database_configured)}</div></div>
        <div class="card"><div class="c-label">Redis Configured</div><div class="c-value" style="font-size:1rem">${bool(status.redis_configured)}</div></div>
        <div class="card"><div class="c-label">Massive API</div><div class="c-value" style="font-size:1rem">${bool(status.massive_configured)}</div></div>
        <div class="card"><div class="c-label">Research API</div><div class="c-value" style="font-size:1rem">${bool(status.research_api_url_configured)}</div></div>
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

      <div style="font-size:.65rem;color:var(--text-dim);margin-top:8px">
        Fetched ${new Date().toLocaleString()}
      </div>
    </div>`;
}

// ── Page registry ─────────────────────────────────────────────────────────────
const RENDERERS = {
  home:     renderHome,
  dashboard: renderDashboard,
  ultra:    renderUltra,
  chart:    renderChart,
  research: renderResearch,
  system:   renderSystem,
};

// ── Boot ──────────────────────────────────────────────────────────────────────
window.addEventListener("hashchange", navigate);
$("refreshBtn").addEventListener("click", refresh);
navigate();

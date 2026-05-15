"use strict";
// ── State ─────────────────────────────────────────────────────────────────────
let _data        = null;   // last successful bootstrap response
let _allCandidates = [];   // unfiltered candidate list
let _busy        = false;

// ── DOM refs ──────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const $loading    = $("loadingMsg");
const $main       = $("mainContent");
const $errBanner  = $("errorBanner");
const $refreshBtn = $("refreshBtn");

// ── Utility ───────────────────────────────────────────────────────────────────
const esc  = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt  = (n, d=2) => n != null ? Number(n).toFixed(d) : "—";
const fmtDate = s => { try { return new Date(s).toLocaleString(); } catch { return s ?? "—"; } };
const show = el => { el.style.display = ""; };
const hide = el => { el.style.display = "none"; };

function bandClass(b) {
  if (b === "A+") return "band-Ap";
  if (b === "A")  return "band-A";
  if (b === "B")  return "band-B";
  if (b === "C")  return "band-C";
  return "";
}

// ── Error banner ──────────────────────────────────────────────────────────────
function showError(msg) {
  $errBanner.textContent = "⚠ " + msg;
  show($errBanner);
}
function clearError() { hide($errBanner); }

// ── Command bar ───────────────────────────────────────────────────────────────
function renderCmdBar(data) {
  const scan    = data.latest_scan ?? {};
  const health  = data.data_health ?? {};
  const sapi    = health.scanner_api ?? {};
  const reachable = sapi.reachable === true;

  $("cmScanner").innerHTML =
    `Scanner: <span class="pill ${reachable ? "ok" : "err"}">${reachable ? "reachable" : "unreachable"}</span>`;
  $("cmRunId").textContent    = scan.scan_run_id ?? "—";
  $("cmUniverse").textContent = scan.universe ?? "—";
  $("cmTf").textContent       = scan.timeframe ?? "—";
  $("cmFinished").textContent = fmtDate(scan.finished_at);
}

// ── State banner ──────────────────────────────────────────────────────────────
function renderStateBanner(data) {
  const state = data.dashboard_state ?? "ERROR";
  const el    = $("stateBanner");
  const scan  = data.latest_scan ?? {};
  let cls = "ready", msg = "";
  if (state === "SCAN_READY") {
    cls = "ready";
    msg = `SCAN READY — Run #${scan.scan_run_id ?? "?"} · ${scan.total_candidates ?? 0} candidates`;
  } else if (state === "NO_SCAN") {
    cls = "warn"; msg = "No completed Ultra Scan found.";
  } else {
    cls = "error"; msg = data.error ?? "Dashboard bootstrap failed.";
  }
  el.className = `state-banner ${cls}`;
  el.textContent = msg;
}

// ── Summary cards ─────────────────────────────────────────────────────────────
function renderSummary(data) {
  const sum  = data.summary      ?? {};
  const scan = data.latest_scan  ?? {};
  $("sumTotal").textContent      = sum.total_candidates ?? "—";
  $("sumScore").textContent      = sum.top_score        ?? "—";
  $("sumRunId").textContent      = scan.scan_run_id     ?? "—";
  $("sumSource").textContent     = scan.source          ?? "—";
  $("sumBandCount").textContent  = Object.keys(sum.bands   ?? {}).length;
  $("sumSectorCount").textContent= Object.keys(sum.sectors ?? {}).length;
  $("sumUniverse").textContent   = scan.universe  ?? "—";
  $("sumTf").textContent         = scan.timeframe ?? "—";
}

// ── Top Movers ────────────────────────────────────────────────────────────────
function renderMoversList(containerId, movers, isGainer) {
  const el = $(containerId);
  if (!movers?.length) {
    el.innerHTML = `<div class="mover-empty">No change_pct data available.</div>`;
    return;
  }
  el.innerHTML = movers.map((m, i) => {
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

function renderMovers(topMovers) {
  const regular = topMovers?.regular ?? {};
  renderMoversList("gainersTable", regular.gainers ?? [], true);
  renderMoversList("losersTable",  regular.losers  ?? [], false);
}

// ── Best setups ───────────────────────────────────────────────────────────────
function renderSetups(setups) {
  if (!setups?.length) {
    $("setupsRow").innerHTML = `<span style="color:var(--text-dim);font-size:.82rem">No best setups found for current scan.</span>`;
    return;
  }
  $("setupsRow").innerHTML = setups.map(s => {
    const reasons   = (s.setup_reason ?? s.why_selected ?? []).slice(0, 4)
      .map(w => `<li>${esc(w)}</li>`).join("");
    const riskChips = (s.risk_flags ?? []).slice(0, 2)
      .map(r => `<span class="chip risk">${esc(r)}</span>`).join("");
    const chg    = s.change_pct;
    const chgStr = chg != null ? (chg >= 0 ? "+" : "") + fmt(chg, 2) + "%" : null;
    const chgCls = chg != null && chg >= 0 ? "pos" : "neg";
    return `
    <div class="setup-card">
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

// ── Distribution bars ─────────────────────────────────────────────────────────
function renderBars(containerId, entries, rowClass) {
  const el  = $(containerId);
  if (!entries.length) { el.innerHTML = `<span style="color:var(--text-dim);font-size:.75rem">No data.</span>`; return; }
  const max = entries[0][1] || 1;
  el.innerHTML = entries.map(([label, count]) =>
    `<div class="bar-row ${rowClass ? rowClass(label) : ""}">
       <span class="bar-label">${esc(label)}</span>
       <div class="bar-track"><div class="bar-fill" style="width:${Math.round(count/max*100)}%"></div></div>
       <span class="bar-count">${count}</span>
     </div>`
  ).join("");
}

function renderDistribution(data) {
  const sum  = data.summary ?? {};
  const bands   = Object.entries(sum.bands   ?? {}).sort((a,b) => b[1]-a[1]);
  const sectors = Object.entries(sum.sectors ?? {}).sort((a,b) => b[1]-a[1]).slice(0, 10);
  renderBars("bandBars",   bands,   l => `band-${l === "A+" ? "Ap" : l}`);
  renderBars("sectorBars", sectors, null);
}

// ── Sector filter options ─────────────────────────────────────────────────────
function populateSectorFilter(candidates) {
  const sectors = [...new Set(candidates.map(c => c.sector || "").filter(Boolean))].sort();
  const sel = $("fSector");
  const cur = sel.value;
  sel.innerHTML = `<option value="">All Sectors</option>` +
    sectors.map(s => `<option${s === cur ? " selected" : ""}>${esc(s)}</option>`).join("");
}

// ── Candidates table ──────────────────────────────────────────────────────────
function renderTable(candidates) {
  const body = $("candidatesBody");
  if (!candidates.length) {
    body.innerHTML = `<tr class="empty-row"><td colspan="10">No candidates match the current filters.</td></tr>`;
    return;
  }
  body.innerHTML = candidates.map((c, i) => {
    const chgVal  = c.change_pct;
    const chgTxt  = chgVal != null ? (chgVal >= 0 ? "+" : "") + fmt(chgVal, 2) + "%" : "—";
    const chgCls  = chgVal == null ? "" : chgVal >= 0 ? "pos" : "neg";
    const why     = (c.why_selected ?? []).slice(0, 3)
      .map(w => `<span class="chip" style="font-size:.6rem">${esc(w)}</span>`).join(" ");
    const risk    = (c.risk_flags ?? [])
      .map(r => `<span class="chip risk" style="font-size:.6rem">${esc(r)}</span>`).join(" ");
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

// ── Filter logic ──────────────────────────────────────────────────────────────
function applyFilters() {
  const search   = $("fSearch").value.trim().toUpperCase();
  const band     = $("fBand").value;
  const sector   = $("fSector").value;
  const minScore = parseFloat($("fMinScore").value) || 0;

  const filtered = _allCandidates.filter(c => {
    if (search && !c.symbol?.toUpperCase().includes(search)) return false;
    if (band   && c.band !== band)   return false;
    if (sector && c.sector !== sector) return false;
    if ((c.ultra_score ?? 0) < minScore) return false;
    return true;
  });

  $("filterCount").textContent = `${filtered.length} / ${_allCandidates.length} shown`;
  renderTable(filtered);
}

// ── Data health ───────────────────────────────────────────────────────────────
function renderHealth(data) {
  const health = data.data_health ?? {};
  const sapi   = health.scanner_api ?? {};
  const ultra  = health.ultra ?? {};
  const reach  = sapi.reachable === true;

  $("hState").textContent    = data.dashboard_state ?? "—";
  $("hScanner").className    = "h-val " + (reach ? "ok-text" : "err-text");
  $("hScanner").textContent  = reach ? "Reachable" : "Unreachable";
  $("hUltraSrc").textContent = ultra.source ?? "—";
  $("hUltraSt").textContent  = ultra.status ?? "—";
  $("hCandCount").textContent= ultra.total_candidates ?? "—";
  $("hRefresh").textContent  = new Date().toLocaleTimeString();
}

// ── Full render ───────────────────────────────────────────────────────────────
function render(data) {
  _data          = data;
  _allCandidates = data.top_candidates ?? [];

  renderCmdBar(data);
  renderStateBanner(data);
  renderSummary(data);
  renderMovers(data.top_movers ?? {});
  renderSetups(data.best_setups ?? []);
  renderDistribution(data);
  populateSectorFilter(_allCandidates);
  applyFilters();
  renderHealth(data);

  hide($loading);
  show($main);
}

// ── Fetch bootstrap ───────────────────────────────────────────────────────────
async function loadDashboard() {
  if (_busy) return;
  _busy = true;
  $refreshBtn.disabled = true;
  $refreshBtn.classList.add("spinning");
  clearError();

  // Don't blank existing content — only show loading on first load
  if (!_data) show($loading);

  try {
    const resp = await fetch("/api/dashboard/bootstrap");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    render(data);
  } catch (err) {
    hide($loading);
    if (_data) {
      // Keep existing data, show banner
      showError(`Refresh failed: ${err.message}. Showing last successful data.`);
      $("hRefresh").textContent = "⚠ refresh failed";
    } else {
      // First load failure — show error state
      show($main);
      $("stateBanner").className = "state-banner error";
      $("stateBanner").textContent = `Dashboard bootstrap failed: ${err.message}`;
      hide($loading);
    }
  } finally {
    _busy = false;
    $refreshBtn.disabled = false;
    $refreshBtn.classList.remove("spinning");
  }
}

// ── Filter event listeners ────────────────────────────────────────────────────
["fSearch","fBand","fSector","fMinScore"].forEach(id =>
  $(id).addEventListener("input", applyFilters)
);
$("clearFilters").addEventListener("click", () => {
  $("fSearch").value   = "";
  $("fBand").value     = "";
  $("fSector").value   = "";
  $("fMinScore").value = "";
  applyFilters();
});
$("refreshBtn").addEventListener("click", loadDashboard);

// ── Boot ──────────────────────────────────────────────────────────────────────
loadDashboard();

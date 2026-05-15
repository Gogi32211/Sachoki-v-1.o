"use strict";

// ── DOM refs ──────────────────────────────────────────────────────────────────
const $loading   = document.getElementById("loadingMsg");
const $error     = document.getElementById("errorMsg");
const $main      = document.getElementById("mainContent");
const $scannerSt = document.getElementById("scannerStatus");
const $refreshBtn= document.getElementById("refreshBtn");

// ── State ─────────────────────────────────────────────────────────────────────
let busy = false;

// ── Helpers ───────────────────────────────────────────────────────────────────
function show(el)  { el.style.display = ""; }
function hide(el)  { el.style.display = "none"; }
function esc(s)    { return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function fmtDate(s){ if (!s) return "—"; try { return new Date(s).toLocaleString(); } catch { return s; } }
function bandClass(b){ return b === "A+" ? "band-Ap" : b ? `band-${b}` : ""; }

// ── Scanner-status pill ───────────────────────────────────────────────────────
function updateScannerPill(reachable) {
  $scannerSt.textContent = reachable ? "Scanner API: reachable" : "Scanner API: unreachable";
  $scannerSt.className   = "scanner-status " + (reachable ? "ok" : "err");
}

// ── Render ────────────────────────────────────────────────────────────────────
function render(data) {
  const state      = data.dashboard_state ?? "ERROR";
  const scan       = data.latest_scan     ?? {};
  const summary    = data.summary         ?? {};
  const candidates = data.top_candidates  ?? [];
  const setups     = data.best_setups     ?? [];
  const health     = data.data_health     ?? {};
  const sapiHealth = health.scanner_api   ?? {};

  updateScannerPill(sapiHealth.reachable === true);

  // ── State banner
  const bannerCls = state === "SCAN_READY" ? "ready" : state === "NO_SCAN" ? "warn" : "error";
  const bannerTxt = state === "SCAN_READY"
    ? `SCAN READY — ${summary.total_candidates ?? 0} candidates loaded`
    : state === "NO_SCAN"
    ? "No completed Ultra Scan found."
    : data.error ?? "Dashboard bootstrap failed.";

  document.getElementById("stateBanner").className = `state-banner ${bannerCls}`;
  document.getElementById("stateBanner").textContent = bannerTxt;

  // ── Scan meta
  document.getElementById("metaState").textContent     = state;
  document.getElementById("metaRunId").textContent     = scan.scan_run_id ?? "—";
  document.getElementById("metaUniverse").textContent  = scan.universe   ?? "—";
  document.getElementById("metaTimeframe").textContent = scan.timeframe  ?? "—";
  document.getElementById("metaTotal").textContent     = scan.total_candidates ?? "—";
  document.getElementById("metaFinished").textContent  = fmtDate(scan.finished_at);
  document.getElementById("metaSource").textContent    = scan.source ?? "—";

  // ── Summary cards
  document.getElementById("sumTotal").textContent  = summary.total_candidates ?? 0;
  document.getElementById("sumScore").textContent  = summary.top_score        ?? "—";
  const bands   = summary.bands   ?? {};
  const sectors = summary.sectors ?? {};
  document.getElementById("sumBands").textContent   = Object.keys(bands).length;
  document.getElementById("sumSectors").textContent = Object.keys(sectors).length;

  // ── Bands chips
  document.getElementById("bandsChips").innerHTML = Object.entries(bands)
    .sort((a, b) => b[1] - a[1])
    .map(([b, n]) => `<span class="chip ${bandClass(b)}">${esc(b)}<span class="cnt">${n}</span></span>`)
    .join("");

  // ── Sectors chips
  document.getElementById("sectorsChips").innerHTML = Object.entries(sectors)
    .sort((a, b) => b[1] - a[1])
    .map(([s, n]) => `<span class="chip">${esc(s)}<span class="cnt">${n}</span></span>`)
    .join("");

  // ── Best setups
  document.getElementById("setupsGrid").innerHTML = setups.length
    ? setups.map(s => `
        <div class="setup-card">
          <div class="sym">${esc(s.symbol)}</div>
          <div class="sec">${esc(s.sector || "—")}</div>
          <div class="score-row">
            <span class="score-val">${s.ultra_score ?? "—"}</span>
            <span class="band-tag">${esc(s.band || "—")}</span>
          </div>
          ${s.final_signal ? `<span class="signal-tag">${esc(s.final_signal)}</span>` : ""}
          <ul class="why-list">
            ${(s.why_selected ?? []).slice(0, 4).map(w => `<li>${esc(w)}</li>`).join("")}
          </ul>
        </div>`).join("")
    : "<span style='color:#6b7280;font-size:.82rem'>No setups available.</span>";

  // ── Top candidates table
  document.getElementById("candidatesBody").innerHTML = candidates.length
    ? candidates.map((c, i) => {
        const risks = (c.risk_flags ?? []).map(r => `<span class="risk-tag">${esc(r)}</span>`).join("");
        return `<tr>
          <td class="rank-col">${i + 1}</td>
          <td class="sym-col">${esc(c.symbol)}</td>
          <td>${esc(c.sector || "—")}</td>
          <td class="score-col">${c.ultra_score ?? "—"}</td>
          <td><span class="chip ${bandClass(c.band)}" style="font-size:.7rem;padding:2px 7px">${esc(c.band || "—")}</span></td>
          <td>${esc(c.priority || c.action_bucket || "—")}</td>
          <td>${esc(c.final_signal || "—")}</td>
          <td>${risks || "—"}</td>
        </tr>`;
      }).join("")
    : `<tr><td colspan="8" style="color:#6b7280;padding:20px;text-align:center">No candidates.</td></tr>`;

  // ── Data health
  const ultraH = health.ultra ?? {};
  document.getElementById("healthScannerReach").className = sapiHealth.reachable ? "h-val ok-text" : "h-val err-text";
  document.getElementById("healthScannerReach").textContent = sapiHealth.reachable ? "Reachable" : "Unreachable";
  document.getElementById("healthUltraSource").textContent  = ultraH.source   ?? "—";
  document.getElementById("healthUltraStatus").textContent  = ultraH.status   ?? "—";
  document.getElementById("healthUltraCount").textContent   = ultraH.total_candidates ?? "—";

  show($main);
}

// ── Fetch bootstrap ───────────────────────────────────────────────────────────
async function loadDashboard() {
  if (busy) return;
  busy = true;
  $refreshBtn.disabled = true;
  hide($main);
  hide($error);
  show($loading);

  try {
    const resp = await fetch("/api/dashboard/bootstrap");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    hide($loading);
    render(data);
  } catch (err) {
    hide($loading);
    $error.textContent = `Dashboard bootstrap failed: ${err.message}`;
    show($error);
    updateScannerPill(false);
  } finally {
    busy = false;
    $refreshBtn.disabled = false;
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
$refreshBtn.addEventListener("click", loadDashboard);
loadDashboard();

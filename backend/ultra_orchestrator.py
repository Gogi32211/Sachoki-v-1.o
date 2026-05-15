"""
ultra_orchestrator.py — ULTRA v2 two-stage orchestrator.

ULTRA is a *display-only* layer over Turbo. It does NOT introduce any new
score, category, or context flag, and never modifies the canonical Turbo /
TZ-WLNBB / TZ Intelligence / Pullback / Rare Reversal modules.

Stage 1 — `run_ultra_scan_job` (Turbo only)
    • runs the canonical `run_turbo_scan`
    • caches Turbo rows by ticker, with all enrichment slots null
    • cheap on memory: no stock_stat generation, no readers

Stage 2 — `run_ultra_enrich_job(tickers, …)` (lazy, per-subset)
    • generates an ULTRA-private subset stock_stat CSV for the picked subset
      (extracted from canonical when present, otherwise fresh-fetched)
    • runs TZ/WLNBB read · TZ Intelligence · Pullback · Rare Reversal
      against that private CSV via their backward-compat `stat_path=…`
      parameter
    • merges the projected enrichments into the cached Turbo rows
      *incrementally* — earlier enrichments are preserved
    • per-source failure → warning, never aborts the response
"""
from __future__ import annotations

import csv as _csv
import gc as _gc
import hashlib
import logging
import os as _os
import threading
import time as _time
from collections import OrderedDict
from typing import Any

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# State surface
# ─────────────────────────────────────────────────────────────────────────────

# Canonical phase ordering for the status pills.
_PHASE_ORDER = (
    "turbo",            # Stage 1
    "stock_stat",       # Stage 2 setup
    "tz_wlnbb",         # Stage 2 readers
    "tz_intelligence",
    "pullback",
    "rare_reversal",
    "merge",            # Stage 2 finalise
)

# Default Phase 2 fan-out for enrich.
# Each Phase 2 reader still loads its subset CSV in full — but the subset is
# tiny (visible-tickers only) so 4 parallel readers is safe again.
_DEFAULT_MAX_WORKERS = 4

# Cap how many merged ULTRA responses live in memory across (universe, tf).
_MAX_RESULTS_CACHE_ENTRIES = 4


_ultra_state: dict = {
    "running":      False,
    "stage":        None,    # "turbo" | "enrich" | None
    "started_at":   0.0,
    "completed_at": None,
    "universe":     None,
    "tf":           None,
    "nasdaq_batch": None,
    "phase":        None,
    "phases":       {},
    "error":        None,
    "warnings":     [],
    "sources":      {},
    "turbo_done":   0,
    "turbo_total":  0,
    "stock_stat_done":  0,
    "stock_stat_total": 0,
    "enrich_total":     0,   # tickers requested for enrichment
    "enrich_done":      0,
}

# Cache: {(universe, tf, nasdaq_batch): {"rows": [...], "rows_by_ticker": {...},
#                                         "last_scan": "...", "warnings": [...],
#                                         "sources": {...}}}
# LRU-ordered so we can evict the oldest entry when the cap is reached.
_ultra_results_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_ultra_lock = threading.Lock()


def _new_phase_dict() -> dict:
    return {p: {"state": "pending", "message": ""} for p in _PHASE_ORDER}


def _set_phase(phase: str, state: str, message: str = "") -> None:
    with _ultra_lock:
        _ultra_state["phase"] = phase
        ph = _ultra_state.setdefault("phases", _new_phase_dict())
        ph.setdefault(phase, {"state": "pending", "message": ""})
        ph[phase]["state"]   = state
        ph[phase]["message"] = message


def _set_source(name: str, payload: dict) -> None:
    """Update the live `_ultra_state['sources'][name]` snapshot.

    Phase 1 only ever writes the 'turbo' source; Phase 2 (enrich) needs to
    push its own per-reader updates so the source-status badges in the UI
    don't keep showing the stale 'unavailable' state from the Stage 1
    initialisation after enrich completes.
    """
    with _ultra_lock:
        sources = _ultra_state.setdefault("sources", {})
        sources[name] = dict(payload)


def _add_warning(msg: str) -> None:
    with _ultra_lock:
        warnings = _ultra_state.setdefault("warnings", [])
        warnings.append(msg)
    log.warning("ULTRA: %s", msg)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(v, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _ultra_tz_batch_stat_path(universe: str, tf: str, nasdaq_batch: str = "") -> str:
    """Mirror of main._tz_batch_stat_path (kept local to avoid circular import)."""
    if nasdaq_batch and nasdaq_batch != "all":
        if universe == "nasdaq":
            return f"stock_stat_tz_wlnbb_nasdaq_{nasdaq_batch}_{tf}.csv"
        if universe == "nasdaq_gt5":
            return f"stock_stat_tz_wlnbb_nasdaq_gt5_{nasdaq_batch}_{tf}.csv"
    return f"stock_stat_tz_wlnbb_{universe}_{tf}.csv"


def _resolve_canonical_stock_stat(universe: str, tf: str, nasdaq_batch: str = "") -> str | None:
    candidates = [
        _ultra_tz_batch_stat_path(universe, tf, nasdaq_batch),
        f"stock_stat_tz_wlnbb_{universe}_{tf}.csv",
        f"stock_stat_tz_wlnbb_{tf}.csv",
    ]
    for p in candidates:
        if _os.path.exists(p):
            return p
    return None


def _ultra_subset_path(universe: str, tf: str, tickers: list[str]) -> str:
    """ULTRA-private subset CSV path. Hash is over sorted tickers so the same
    subset re-uses the same file (cheap idempotency)."""
    norm = ",".join(sorted(t.upper() for t in tickers if t))
    h = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:8]
    return f"stock_stat_tz_wlnbb_ultra_{universe}_{tf}_{h}.csv"


def _read_tz_wlnbb_latest_from(stat_path: str, universe: str) -> dict:
    """Latest TZ/WLNBB row per ticker from a specific stock_stat CSV."""
    if not stat_path or not _os.path.exists(stat_path):
        return {}
    rows_by_ticker: dict[str, list] = {}
    with open(stat_path, newline="", encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            t = row.get("ticker", "")
            if not t:
                continue
            if row.get("universe", "") and row.get("universe", "") != universe:
                continue
            rows_by_ticker.setdefault(t, []).append(row)
    latest: dict = {}
    for t, rows in rows_by_ticker.items():
        rows.sort(key=lambda r: r.get("bar_datetime") or r.get("date", ""))
        latest[t] = rows[-1]
    return latest


# ─────────────────────────────────────────────────────────────────────────────
# Subset stock_stat: extract from canonical or fresh-generate
# ─────────────────────────────────────────────────────────────────────────────

def _extract_subset_csv(canonical_path: str, subset_path: str,
                        tickers: list[str]) -> int:
    """Filter `canonical_path` rows to the picked tickers and write the result
    to `subset_path`. Returns row count written."""
    wanted = {t.upper() for t in tickers if t}
    written = 0
    with open(canonical_path, newline="", encoding="utf-8") as fin:
        reader = _csv.DictReader(fin)
        fieldnames = reader.fieldnames or []
        with open(subset_path, "w", newline="", encoding="utf-8") as fout:
            writer = _csv.DictWriter(fout, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in reader:
                t = (row.get("ticker") or "").upper()
                if t in wanted:
                    writer.writerow(row)
                    written += 1
    return written


def _generate_subset_csv_fresh(universe: str, tf: str, tickers: list[str],
                                bars: int, subset_path: str) -> int:
    """Run the existing TZ/WLNBB stock_stat generator for ONLY the picked
    tickers and write the output to the ULTRA-private subset path. Does NOT
    touch the canonical path."""
    from analyzers.tz_wlnbb.stock_stat import generate_stock_stat
    from data_polygon import fetch_bars as _fetch_bars, polygon_available

    if polygon_available():
        def _fetch(ticker, interval, n_bars):
            days = max(int(n_bars * 1.6), 365)
            return _fetch_bars(ticker, interval=interval, days=days)
    else:
        from data import fetch_ohlcv as _fetch_yf
        def _fetch(ticker, interval, n_bars):
            return _fetch_yf(ticker, interval, n_bars)

    def _on_progress(done, total):
        with _ultra_lock:
            _ultra_state["stock_stat_done"]  = done
            _ultra_state["stock_stat_total"] = total

    gen_min_price = 5.0 if universe == "nasdaq_gt5" else 0.0
    path, _audit = generate_stock_stat(
        list(tickers), _fetch, universe=universe, tf=tf, bars=bars,
        min_price=gen_min_price, output_path=subset_path,
        progress_callback=_on_progress,
    )
    # Count rows written
    if not _os.path.exists(path):
        return 0
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for _ in _csv.DictReader(f):
            n += 1
    return n


# ─────────────────────────────────────────────────────────────────────────────
# Projections — same shape as before, no new score/category fields
# ─────────────────────────────────────────────────────────────────────────────

def _project_tz_wlnbb(row: dict) -> dict:
    return {
        "t_signal":      row.get("t_signal", "") or "",
        "z_signal":      row.get("z_signal", "") or "",
        "l_signal":      row.get("l_signal", "") or "",
        "preup_signal":  row.get("preup_signal", "") or "",
        "predn_signal":  row.get("predn_signal", "") or "",
        "lane1_label":   row.get("lane1_label", "") or "",
        "lane3_label":   row.get("lane3_label", "") or "",
        "volume_bucket": row.get("volume_bucket", "") or "",
        "wick_suffix":   row.get("wick_suffix", "") or "",
    }


def _project_tz_intel(row: dict) -> dict:
    return {
        "role":                row.get("role", "") or "",
        "quality":             row.get("quality", "") or "",
        "action":              row.get("action", "") or "",
        "score":               row.get("score"),
        "matched_status":      row.get("matched_status", "") or "",
        "matched_med10d_pct":  row.get("matched_med10d_pct"),
        "matched_fail10d_pct": row.get("matched_fail10d_pct"),
    }


def _project_abr(row: dict) -> dict:
    return {
        "category":           row.get("abr_category", "") or "",
        "med10d_pct":         row.get("abr_med10d_pct"),
        "fail10d_pct":        row.get("abr_fail10d_pct"),
        "context_type":       row.get("abr_context_type", "") or "",
        "action_hint":        row.get("abr_action_hint", "") or "",
        "conflict_flag":      bool(row.get("abr_conflict_flag")),
        "confirmation_flag":  bool(row.get("abr_confirmation_flag")),
    }


def _project_pullback(row: dict) -> dict:
    return {
        "evidence_tier":              row.get("evidence_tier", "") or "",
        "pullback_stage":             row.get("pullback_stage", "") or "",
        "pattern_key":                row.get("pattern_key", "") or "",
        "pattern_length":             row.get("pattern_length"),
        "score":                      row.get("score"),
        "median_10d_return":          row.get("median_10d_return"),
        "win_rate_10d":               row.get("win_rate_10d"),
        "fail_rate_10d":              row.get("fail_rate_10d"),
        "is_currently_active":        bool(row.get("is_currently_active")),
        "current_pattern_completion": row.get("current_pattern_completion"),
    }


def _project_rare(row: dict) -> dict:
    return {
        "evidence_tier":              row.get("evidence_tier", "") or "",
        "base4_key":                  row.get("base4_key", "") or "",
        "extended5_key":              row.get("extended5_key") or "",
        "extended6_key":              row.get("extended6_key") or "",
        "pattern_length":             row.get("pattern_length"),
        "score":                      row.get("score"),
        "median_10d_return":          row.get("median_10d_return"),
        "fail_rate_10d":              row.get("fail_rate_10d"),
        "is_currently_active":        bool(row.get("is_currently_active")),
        "current_pattern_completion": row.get("current_pattern_completion"),
    }


def _best_pattern_per_ticker(rows: list) -> dict:
    by_ticker: dict = {}
    for r in rows or []:
        t = r.get("ticker")
        if not t:
            continue
        prev = by_ticker.get(t)
        if prev is None:
            by_ticker[t] = r
            continue
        if _safe_float(r.get("score"), -1e9) > _safe_float(prev.get("score"), -1e9):
            by_ticker[t] = r
    return by_ticker


# ─────────────────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cache_key(universe: str, tf: str, nasdaq_batch: str = "") -> tuple:
    return (universe, tf, nasdaq_batch or "")


# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# ULTRA Score — thin wrapper around the shared backend.ultra_score helper.
# Live ULTRA and Stock Stat / Replay must stay in lockstep, so neither side
# defines its own formula.
# ─────────────────────────────────────────────────────────────────────────────

from ultra_score import compute_ultra_score as _shared_compute_ultra_score


def _attach_ultra_score(row: dict) -> None:
    """Compute and attach ULTRA Score fields to ``row`` in place."""
    sc = _shared_compute_ultra_score(row)
    row["ultra_score"]                    = sc["ultra_score"]
    row["ultra_score_band"]               = sc["ultra_score_band"]
    row["ultra_score_reasons"]            = sc["ultra_score_reasons"]
    row["ultra_score_flags"]              = sc["ultra_score_flags"]
    row["ultra_score_raw_before_penalty"] = sc["ultra_score_raw_before_penalty"]
    row["ultra_score_penalty_total"]      = sc["ultra_score_penalty_total"]
    # v2 calibration fields (replay-derived). Live UI / CSV reads these.
    row["ultra_score_band_v2"]            = sc.get("ultra_score_band_v2", "D")
    row["ultra_score_priority"]           = sc.get("ultra_score_priority", "LOW")
    row["ultra_score_regime_bonus"]       = sc.get("ultra_score_regime_bonus", 0)
    row["ultra_score_caps_applied"]       = sc.get("ultra_score_caps_applied", [])
    row["ultra_score_cap_reason"]         = sc.get("ultra_score_cap_reason", "")


def _empty_unenriched_row(turbo_row: dict) -> dict:
    """Wrap a Turbo row as an ULTRA row with all enrichment slots null."""
    out = dict(turbo_row)
    out["ultra_enriched"] = False
    out["ultra_sources"]  = {
        "has_turbo":         True,
        "has_tz_wlnbb":      False,
        "has_tz_intel":      False,
        "has_pullback":      False,
        "has_rare_reversal": False,
    }
    out["tz_wlnbb"]      = None
    out["tz_intel"]      = None
    out["abr"]           = None
    out["pullback"]      = None
    out["rare_reversal"] = None
    _attach_ultra_score(out)
    return out


def _store_results(universe: str, tf: str, nasdaq_batch: str,
                   rows: list, last_scan: str | None,
                   warnings: list, sources: dict, phase: str) -> None:
    """Replace the cache entry for (universe, tf, nasdaq_batch). Used by
    Stage 1 (Turbo). Stage 2 enrichment uses _patch_cached_rows instead."""
    key = _cache_key(universe, tf, nasdaq_batch)
    rows_by_ticker = {r["ticker"]: r for r in rows if r.get("ticker")}
    with _ultra_lock:
        if key in _ultra_results_cache:
            _ultra_results_cache.move_to_end(key)
        _ultra_results_cache[key] = {
            "rows":           rows,
            "rows_by_ticker": rows_by_ticker,
            "last_scan":      last_scan,
            "warnings":       list(warnings or []),
            "sources":        dict(sources or {}),
            "phase":          phase,
        }
        while len(_ultra_results_cache) > _MAX_RESULTS_CACHE_ENTRIES:
            _ultra_results_cache.popitem(last=False)


def _patch_cached_rows(universe: str, tf: str, nasdaq_batch: str,
                        ticker_patches: dict[str, dict],
                        warnings_to_add: list,
                        sources_to_merge: dict,
                        phase: str | None = None) -> None:
    """Incremental enrichment merge: update only the rows we have new data
    for. Other rows (already enriched or never enriched) are left alone."""
    key = _cache_key(universe, tf, nasdaq_batch)
    with _ultra_lock:
        cached = _ultra_results_cache.get(key)
        if cached is None:
            return
        for ticker, patch in ticker_patches.items():
            row = cached["rows_by_ticker"].get(ticker)
            if row is None:
                continue
            # Merge enrichment slots without losing previous ones
            for k, v in patch.items():
                if v is not None:
                    row[k] = v
            # Recompute source flags from the resulting row
            row["ultra_sources"] = {
                "has_turbo":         True,
                "has_tz_wlnbb":      row.get("tz_wlnbb")      is not None,
                "has_tz_intel":      row.get("tz_intel")      is not None,
                "has_pullback":      row.get("pullback")      is not None,
                "has_rare_reversal": row.get("rare_reversal") is not None,
            }
            row["ultra_enriched"] = any([
                row["ultra_sources"]["has_tz_wlnbb"],
                row["ultra_sources"]["has_tz_intel"],
                row["ultra_sources"]["has_pullback"],
                row["ultra_sources"]["has_rare_reversal"],
            ])
            # Recompute ULTRA Score now that enrichment slots may have changed
            _attach_ultra_score(row)
        # Merge sources (prefer fresh ok counts)
        sources = cached.get("sources") or {}
        for k, v in (sources_to_merge or {}).items():
            sources[k] = v
        cached["sources"] = sources
        cached["warnings"].extend(warnings_to_add or [])
        if phase:
            cached["phase"] = phase
        # Move-to-end for LRU
        _ultra_results_cache.move_to_end(key)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Turbo only
# ─────────────────────────────────────────────────────────────────────────────

def run_ultra_scan_job(
    universe: str = "sp500",
    tf: str = "1d",
    lookback_n: int = 5,
    partial_day: bool = False,
    min_volume: float = 0.0,
    min_store_score: float = 5.0,
    nasdaq_batch: str = "",
    # Accepted for API compatibility (Stage 2 uses these); Stage 1 ignores them.
    stock_stat_bars: int = 500,
    min_price: float = 0.0,
    max_price: float = 1e9,
    max_workers: int = _DEFAULT_MAX_WORKERS,
) -> dict:
    """Stage 1: run the canonical Turbo scan and cache its rows. Enrichment
    columns are initialised null/false; nothing else runs."""
    with _ultra_lock:
        _ultra_state.update({
            "running":          True,
            "stage":            "turbo",
            "started_at":       _time.time(),
            "completed_at":     None,
            "universe":         universe,
            "tf":               tf,
            "nasdaq_batch":     nasdaq_batch or None,
            "phase":            None,
            "phases":           _new_phase_dict(),
            "error":            None,
            "warnings":         [],
            "sources":          {},
            "turbo_done":       0,
            "turbo_total":      0,
            "stock_stat_done":  0,
            "stock_stat_total": 0,
            "enrich_total":     0,
            "enrich_done":      0,
        })

    sources: dict = {
        "turbo":           {"ok": False, "count": 0},
        "stock_stat":      {"ok": False, "count": 0, "path": None},
        "tz_wlnbb":        {"ok": False, "count": 0},
        "tz_intelligence": {"ok": False, "count": 0},
        "pullback":        {"ok": False, "count": 0},
        "rare_reversal":   {"ok": False, "count": 0},
    }

    rows: list = []
    last_scan: str | None = None

    try:
        _set_phase("turbo", "running", "Running Turbo scan")
        from turbo_engine import (
            run_turbo_scan, get_turbo_results, get_last_turbo_scan_time,
            get_turbo_progress,
        )
        try:
            run_turbo_scan(
                interval=tf, universe=universe, workers=8,
                lookback_n=lookback_n, partial_day=partial_day,
                min_volume=min_volume, min_store_score=min_store_score,
            )
            prog = get_turbo_progress()
            with _ultra_lock:
                _ultra_state["turbo_done"]  = prog.get("done", 0)
                _ultra_state["turbo_total"] = prog.get("total", 0)
            turbo_rows = get_turbo_results(
                limit=10000, min_score=0, direction="all",
                tf=tf, universe=universe,
            )
            # Mirror /api/turbo-scan exactly: apply the same read-only profile
            # playbook enrichment so PF Score / Category / sweet_spot / etc.
            # match Turbo tab. Skipping this leaves profile_score and
            # profile_category empty in ULTRA — even though the underlying
            # Turbo scoring is identical, the playbook context is missing.
            try:
                from profile_playbook import enrich_row_with_profile
                turbo_rows = [enrich_row_with_profile(r, universe) for r in turbo_rows]
            except Exception as exc:
                log.warning("ULTRA: profile_playbook enrichment failed: %s", exc)

            # BETA Score enrichment (same logic as turbo-scan endpoint)
            try:
                from beta_engine import calc_beta_score as _calc_beta
                from canonical_scoring_engine import compute_canonical_score as _canon
                for r in turbo_rows:
                    try:
                        canon = _canon(r, universe)
                        _vol = ("20x" if r.get("vol_spike_20x") else
                                "10x" if r.get("vol_spike_10x") else
                                "5x"  if r.get("vol_spike_5x")  else "")
                        _br = dict(r,
                            ROCKET_SCORE=canon["ROCKET_SCORE"],
                            CLEAN_ENTRY_SCORE=canon["CLEAN_ENTRY_SCORE"],
                            FINAL_REGIME=canon["FINAL_REGIME"],
                            VOL=_vol)
                        _b = _calc_beta(_br, [], universe)
                        r.update(_b)
                    except Exception:
                        pass
            except Exception as exc:
                log.warning("ULTRA: beta score enrichment failed: %s", exc)

            last_scan = get_last_turbo_scan_time(tf=tf, universe=universe)
            rows = [_empty_unenriched_row(r) for r in turbo_rows
                    if r.get("ticker")]
            sources["turbo"] = {"ok": True, "count": len(rows)}
            _set_phase("turbo", "ok", f"{len(rows)} tickers")
        except Exception as exc:
            _set_phase("turbo", "error", str(exc))
            _add_warning(f"Turbo scan failed: {exc}")

        # All Stage 2 phases stay 'pending' until enrich is invoked
        for ph in ("stock_stat", "tz_wlnbb", "tz_intelligence",
                   "pullback", "rare_reversal", "merge"):
            _set_phase(ph, "pending", "waiting on enrich")

    except Exception as exc:
        with _ultra_lock:
            _ultra_state["error"] = str(exc)
        log.exception("ULTRA Stage 1 crashed")

    _warnings_snapshot = list(_ultra_state.get("warnings", []))
    _store_results(
        universe, tf, nasdaq_batch,
        rows=rows, last_scan=last_scan,
        warnings=_warnings_snapshot,
        sources=sources,
        phase="turbo_done",
    )
    elapsed_ms = int((_time.time() - _ultra_state.get("started_at", _time.time())) * 1000)
    response = _build_response(universe, tf, nasdaq_batch, elapsed_ms)
    with _ultra_lock:
        _ultra_state["sources"]      = sources
        _ultra_state["completed_at"] = _time.time()
        _ultra_state["running"]      = False
    _gc.collect()

    # Persist to DB so results survive deploy/restart (non-fatal if it fails)
    if rows:
        try:
            persist_ultra_scan_results(
                universe, tf, nasdaq_batch,
                rows=rows, last_scan=last_scan,
                warnings=_warnings_snapshot, sources=sources,
            )
        except Exception as _exc:
            log.warning("ULTRA: DB persist skipped: %s", _exc)

    return response


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Enrich a subset
# ─────────────────────────────────────────────────────────────────────────────

def run_ultra_enrich_job(
    tickers: list[str],
    universe: str = "sp500",
    tf: str = "1d",
    nasdaq_batch: str = "",
    min_price: float = 0.0,
    max_price: float = 1e9,
    min_volume: float = 0.0,
    stock_stat_bars: int = 500,
    max_workers: int = _DEFAULT_MAX_WORKERS,
) -> dict:
    """Stage 2: run TZ/WLNBB stock_stat (subset only) + the four secondary
    readers against the subset, and incrementally merge the projections back
    into the cached ULTRA rows for the requested tickers.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    norm_tickers = sorted({(t or "").upper() for t in (tickers or []) if t})
    if not norm_tickers:
        return {"results": [], "warnings": ["enrich called with empty ticker list"]}

    with _ultra_lock:
        _ultra_state.update({
            "running":          True,
            "stage":            "enrich",
            "started_at":       _time.time(),
            "completed_at":     None,
            "universe":         universe,
            "tf":               tf,
            "nasdaq_batch":     nasdaq_batch or None,
            "error":            None,
            "warnings":         [],
            "stock_stat_done":  0,
            "stock_stat_total": len(norm_tickers),
            "enrich_total":     len(norm_tickers),
            "enrich_done":      0,
        })
        # Reset Stage 2 phase pills for this enrich run; keep Stage 1 'turbo' as 'ok'
        for ph in ("stock_stat", "tz_wlnbb", "tz_intelligence",
                   "pullback", "rare_reversal", "merge"):
            _ultra_state["phases"][ph] = {"state": "pending", "message": ""}

    src_sources: dict = {
        "stock_stat":      {"ok": False, "count": 0, "path": None},
        "tz_wlnbb":        {"ok": False, "count": 0},
        "tz_intelligence": {"ok": False, "count": 0},
        "pullback":        {"ok": False, "count": 0},
        "rare_reversal":   {"ok": False, "count": 0},
    }
    fresh_warnings: list = []

    # ── Step A: subset stock_stat — extract from canonical or fresh-fetch ───
    subset_path = _ultra_subset_path(universe, tf, norm_tickers)
    _set_phase("stock_stat", "running",
               "extracting subset from canonical" if False else "preparing subset CSV")
    try:
        if _os.path.exists(subset_path):
            # Already prepared for this exact ticker set
            stock_stat_count = _count_csv_rows(subset_path)
            _set_phase("stock_stat", "ok",
                       f"reused subset {subset_path} ({stock_stat_count} rows)")
        else:
            canonical = _resolve_canonical_stock_stat(universe, tf, nasdaq_batch)
            if canonical:
                stock_stat_count = _extract_subset_csv(canonical, subset_path,
                                                       norm_tickers)
                _set_phase("stock_stat", "ok",
                           f"extracted {stock_stat_count} rows from canonical")
            else:
                stock_stat_count = _generate_subset_csv_fresh(
                    universe, tf, norm_tickers, stock_stat_bars, subset_path,
                )
                _set_phase("stock_stat", "ok",
                           f"fresh-generated {stock_stat_count} rows")
        src_sources["stock_stat"] = {
            "ok": stock_stat_count > 0, "count": stock_stat_count, "path": subset_path,
        }
        _set_source("stock_stat", src_sources["stock_stat"])
    except Exception as exc:
        _set_phase("stock_stat", "error", str(exc))
        fresh_warnings.append(f"stock_stat unavailable: {exc}")
        src_sources["stock_stat"] = {"ok": False, "count": 0, "path": subset_path,
                                       "error": str(exc)}
        _set_source("stock_stat", src_sources["stock_stat"])
        _patch_cached_rows(universe, tf, nasdaq_batch, {},
                            fresh_warnings, src_sources, phase="enrich_done")
        elapsed_ms = int((_time.time() - _ultra_state.get("started_at", _time.time())) * 1000)
        with _ultra_lock:
            _ultra_state["completed_at"] = _time.time()
            _ultra_state["running"]      = False
        _gc.collect()
        return _build_response(universe, tf, nasdaq_batch, elapsed_ms)

    # ── Step B-E: run the four readers in parallel against the subset CSV ───
    ph2_workers = max(1, min(4, max_workers))
    tz_wlnbb_by_ticker: dict = {}
    tz_intel_by_ticker: dict = {}
    pullback_by_ticker: dict = {}
    rare_by_ticker:     dict = {}

    def _do_tz_wlnbb():
        _set_phase("tz_wlnbb", "running", "")
        try:
            d = _read_tz_wlnbb_latest_from(subset_path, universe)
            _set_phase("tz_wlnbb", "ok" if d else "skipped",
                       f"{len(d)} tickers")
            return d
        except Exception as exc:
            _set_phase("tz_wlnbb", "error", str(exc))
            fresh_warnings.append(f"TZ/WLNBB unavailable: {exc}")
            return {}

    def _do_tz_intel():
        _set_phase("tz_intelligence", "running", "")
        try:
            from tz_intelligence.scanner import run_intelligence_scan
            resp = run_intelligence_scan(
                universe=universe, tf=tf, nasdaq_batch=nasdaq_batch,
                min_price=min_price, max_price=max_price, min_volume=min_volume,
                role_filter="all", scan_mode="latest", limit=10000,
                stat_path=subset_path,
            )
            if isinstance(resp, dict) and resp.get("error"):
                _set_phase("tz_intelligence", "skipped", resp["error"])
                fresh_warnings.append(f"TZ Intelligence unavailable: {resp['error']}")
                return {}
            out: dict = {}
            for r in (resp or {}).get("results", []) or []:
                t = r.get("ticker")
                if t and t not in out:
                    out[t] = r
            _set_phase("tz_intelligence", "ok", f"{len(out)} tickers")
            return out
        except Exception as exc:
            _set_phase("tz_intelligence", "error", str(exc))
            fresh_warnings.append(f"TZ Intelligence unavailable: {exc}")
            return {}

    def _do_pullback():
        _set_phase("pullback", "running", "")
        try:
            from analyzers.pullback_miner.miner import run_pullback_scan
            resp = run_pullback_scan(
                universe=universe, tf=tf,
                min_price=min_price, max_price=max_price,
                limit=10000, stat_path=subset_path,
            )
            if isinstance(resp, dict) and resp.get("error"):
                _set_phase("pullback", "skipped", resp["error"])
                fresh_warnings.append(f"Pullback Miner unavailable: {resp['error']}")
                return {}
            d = _best_pattern_per_ticker((resp or {}).get("results", []) or [])
            _set_phase("pullback", "ok", f"{len(d)} tickers")
            return d
        except Exception as exc:
            _set_phase("pullback", "error", str(exc))
            fresh_warnings.append(f"Pullback Miner unavailable: {exc}")
            return {}

    def _do_rare():
        _set_phase("rare_reversal", "running", "")
        try:
            from analyzers.rare_reversal.miner import run_rare_reversal_scan
            resp = run_rare_reversal_scan(
                universe=universe, tf=tf,
                min_price=min_price, max_price=max_price,
                limit=10000, stat_path=subset_path,
            )
            if isinstance(resp, dict) and resp.get("error"):
                _set_phase("rare_reversal", "skipped", resp["error"])
                fresh_warnings.append(f"Rare Reversal unavailable: {resp['error']}")
                return {}
            d = _best_pattern_per_ticker((resp or {}).get("results", []) or [])
            _set_phase("rare_reversal", "ok", f"{len(d)} tickers")
            return d
        except Exception as exc:
            _set_phase("rare_reversal", "error", str(exc))
            fresh_warnings.append(f"Rare Reversal unavailable: {exc}")
            return {}

    ex = ThreadPoolExecutor(max_workers=ph2_workers, thread_name_prefix="ultra-enrich")
    try:
        fut_w = ex.submit(_do_tz_wlnbb)
        fut_i = ex.submit(_do_tz_intel)
        fut_p = ex.submit(_do_pullback)
        fut_r = ex.submit(_do_rare)
        for _ in as_completed([fut_w, fut_i, fut_p, fut_r]):
            pass
        try: tz_wlnbb_by_ticker = fut_w.result() or {}
        except Exception: pass
        try: tz_intel_by_ticker = fut_i.result() or {}
        except Exception: pass
        try: pullback_by_ticker = fut_p.result() or {}
        except Exception: pass
        try: rare_by_ticker     = fut_r.result() or {}
        except Exception: pass
    finally:
        ex.shutdown(wait=True)

    src_sources["tz_wlnbb"]        = {"ok": bool(tz_wlnbb_by_ticker),
                                       "count": len(tz_wlnbb_by_ticker)}
    src_sources["tz_intelligence"] = {"ok": bool(tz_intel_by_ticker),
                                       "count": len(tz_intel_by_ticker)}
    src_sources["pullback"]        = {"ok": bool(pullback_by_ticker),
                                       "count": len(pullback_by_ticker)}
    src_sources["rare_reversal"]   = {"ok": bool(rare_by_ticker),
                                       "count": len(rare_by_ticker)}
    # Push to live status so the UI's source-status badges reflect the
    # enrich outcome instead of the stale Stage 1 'unavailable' state.
    for _k in ("tz_wlnbb", "tz_intelligence", "pullback", "rare_reversal"):
        _set_source(_k, src_sources[_k])

    # ── Step F: merge per-ticker patches into the cache ─────────────────────
    _set_phase("merge", "running", "")
    patches: dict[str, dict] = {}
    for ticker in norm_tickers:
        patch: dict = {}
        if ticker in tz_wlnbb_by_ticker:
            patch["tz_wlnbb"] = _project_tz_wlnbb(tz_wlnbb_by_ticker[ticker])
        if ticker in tz_intel_by_ticker:
            patch["tz_intel"] = _project_tz_intel(tz_intel_by_ticker[ticker])
            patch["abr"]      = _project_abr(tz_intel_by_ticker[ticker])
        if ticker in pullback_by_ticker:
            patch["pullback"] = _project_pullback(pullback_by_ticker[ticker])
        if ticker in rare_by_ticker:
            patch["rare_reversal"] = _project_rare(rare_by_ticker[ticker])
        if patch:
            patches[ticker] = patch
        with _ultra_lock:
            _ultra_state["enrich_done"] += 1

    _patch_cached_rows(universe, tf, nasdaq_batch, patches,
                       fresh_warnings, src_sources, phase="enrich_done")
    _set_phase("merge", "ok", f"{len(patches)} rows merged")

    # Free large dicts before serialising response
    try:
        tz_wlnbb_by_ticker.clear()
        tz_intel_by_ticker.clear()
        pullback_by_ticker.clear()
        rare_by_ticker.clear()
    except Exception:
        pass
    _gc.collect()

    elapsed_ms = int((_time.time() - _ultra_state.get("started_at", _time.time())) * 1000)
    with _ultra_lock:
        _ultra_state["completed_at"] = _time.time()
        _ultra_state["running"]      = False
    return _build_response(universe, tf, nasdaq_batch, elapsed_ms)


def _count_csv_rows(path: str) -> int:
    if not _os.path.exists(path):
        return 0
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for _ in _csv.DictReader(f):
            n += 1
    return n


# ─────────────────────────────────────────────────────────────────────────────
# Status / results readers
# ─────────────────────────────────────────────────────────────────────────────

def _build_response(universe: str, tf: str, nasdaq_batch: str,
                     elapsed_ms: int | None = None) -> dict:
    cached = _ultra_results_cache.get(_cache_key(universe, tf, nasdaq_batch))
    if cached is None:
        return {
            "results":   [],
            "total":     0,
            "last_scan": None,
            "warnings":  ["No ULTRA scan has run yet for this universe/tf — press ULTRA Scan."],
            "meta": {
                "universe":     universe,
                "tf":           tf,
                "nasdaq_batch": nasdaq_batch or None,
                "phase":        None,
                "sources": {
                    "turbo":           {"ok": False, "count": 0},
                    "stock_stat":      {"ok": False, "count": 0, "path": None},
                    "tz_wlnbb":        {"ok": False, "count": 0},
                    "tz_intelligence": {"ok": False, "count": 0},
                    "pullback":        {"ok": False, "count": 0},
                    "rare_reversal":   {"ok": False, "count": 0},
                },
            },
        }
    return {
        "results":   list(cached["rows"]),
        "total":     len(cached["rows"]),
        "last_scan": cached.get("last_scan"),
        "warnings":  list(cached.get("warnings") or []),
        "meta": {
            "universe":     universe,
            "tf":           tf,
            "nasdaq_batch": nasdaq_batch or None,
            "phase":        cached.get("phase"),
            "elapsed_ms":   elapsed_ms,
            "sources":      dict(cached.get("sources") or {}),
        },
    }


def get_ultra_status() -> dict:
    with _ultra_lock:
        return dict(_ultra_state)


def get_ultra_results(universe: str, tf: str, nasdaq_batch: str = "") -> dict:
    return _build_response(universe, tf, nasdaq_batch)


# ─────────────────────────────────────────────────────────────────────────────
# DB persistence — survives deploy/restart
# ─────────────────────────────────────────────────────────────────────────────

def persist_ultra_scan_results(
    universe: str, tf: str, nasdaq_batch: str,
    rows: list, last_scan: str | None,
    warnings: list, sources: dict,
) -> int | None:
    """Atomically persist scan results to DB. Old is_latest stays until new run
    succeeds, then a single transaction flips is_latest.
    Returns new run_id, or None on failure (non-fatal to caller)."""
    import json as _json
    try:
        from db import get_db, USE_PG
        nb = nasdaq_batch or ""
        now_str = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())

        with get_db() as db:
            if USE_PG:
                db.execute(
                    """INSERT INTO ultra_scan_runs
                       (universe, tf, nasdaq_batch, status, is_latest, total_candidates,
                        last_turbo_scan, sources_json, warnings_json, started_at, finished_at)
                       VALUES (%s, %s, %s, 'completed', false, %s, %s, %s, %s, %s, %s)
                       RETURNING id""",
                    (universe, tf, nb, len(rows), last_scan,
                     _json.dumps(sources), _json.dumps(warnings), now_str, now_str),
                )
            else:
                db.execute(
                    """INSERT INTO ultra_scan_runs
                       (universe, tf, nasdaq_batch, status, is_latest, total_candidates,
                        last_turbo_scan, sources_json, warnings_json, started_at, finished_at)
                       VALUES (?, ?, ?, 'completed', 0, ?, ?, ?, ?, ?, ?)""",
                    (universe, tf, nb, len(rows), last_scan,
                     _json.dumps(sources), _json.dumps(warnings), now_str, now_str),
                )
            run_id = db.lastrowid

            # Batch insert all candidates
            candidate_rows = [
                (run_id, r.get("ticker", ""), float(r.get("ultra_score", 0) or 0), _json.dumps(r))
                for r in rows
            ]
            db.executemany(
                "INSERT INTO ultra_scan_candidates (scan_run_id, ticker, ultra_score, row_json)"
                " VALUES (?, ?, ?, ?)",
                candidate_rows,
            )

            # Atomic swap: old latest → False, new run → True
            db.execute(
                "UPDATE ultra_scan_runs SET is_latest=? WHERE universe=? AND tf=? AND nasdaq_batch=? AND id!=?",
                (False, universe, tf, nb, run_id),
            )
            db.execute(
                "UPDATE ultra_scan_runs SET is_latest=? WHERE id=?",
                (True, run_id),
            )
            db.commit()

        log.info("ULTRA scan persisted: run_id=%s, %d candidates (%s/%s)", run_id, len(rows), universe, tf)
        return run_id
    except Exception as exc:
        log.error("ULTRA scan persist failed (non-fatal): %s", exc)
        return None


def load_latest_ultra_scan_from_db(
    universe: str = "sp500", tf: str = "1d", nasdaq_batch: str = "",
) -> bool:
    """Load the latest completed scan from DB into the in-memory cache.
    Returns True if data was loaded, False if nothing found or on error."""
    import json as _json
    try:
        from db import get_db
        nb = nasdaq_batch or ""
        with get_db() as db:
            db.execute(
                """SELECT id, total_candidates, last_turbo_scan, sources_json, warnings_json
                   FROM ultra_scan_runs
                   WHERE universe=? AND tf=? AND nasdaq_batch=? AND is_latest=1 AND status='completed'
                   ORDER BY id DESC LIMIT 1""",
                (universe, tf, nb),
            )
            run_row = db.fetchone()
            if not run_row:
                return False

            run_id   = run_row["id"]
            last_scan = run_row.get("last_turbo_scan")
            sources   = _json.loads(run_row["sources_json"] or "{}")
            warnings  = _json.loads(run_row["warnings_json"] or "[]")

            db.execute(
                "SELECT row_json FROM ultra_scan_candidates WHERE scan_run_id=? ORDER BY ultra_score DESC",
                (run_id,),
            )
            raw_rows = db.fetchall()

        rows = []
        for r in raw_rows:
            try:
                rows.append(_json.loads(r["row_json"]))
            except Exception:
                pass

        if not rows:
            return False

        _store_results(
            universe, tf, nb,
            rows=rows, last_scan=last_scan,
            warnings=warnings, sources=sources,
            phase="db_loaded",
        )
        log.info(
            "ULTRA: loaded %d candidates from DB (run_id=%s, %s/%s)",
            len(rows), run_id, universe, tf,
        )
        return True
    except Exception as exc:
        log.warning("ULTRA: DB load failed: %s", exc)
        return False


def get_ultra_latest_from_db(
    universe: str | None = None, tf: str | None = None,
) -> dict:
    """
    Return metadata for the latest completed Ultra Scan stored in DB.

    If universe/tf are given, filter by them. Otherwise find the most recent
    finished is_latest=1 completed run across any universe/tf for the given tf
    (or unrestricted when tf is None).

    Always returns a dict — never raises. Shape on success:
        {
          "has_data": True,
          "scan_run_id":      <int>,
          "status":           "completed",
          "universe":         <str>,
          "tf":               <str>,
          "nasdaq_batch":     <str>,
          "finished_at":      <iso str>,
          "total_candidates": <int>,
          "data_age_seconds": <int>,
        }
    On no data:
        {"has_data": False, "message": "No completed Ultra Scan found"}
    On error:
        {"has_data": False, "error": <str>}
    """
    try:
        from db import get_db
        with get_db() as db:
            if universe and tf:
                db.execute(
                    "SELECT id, universe, tf, nasdaq_batch, status,"
                    " finished_at, total_candidates"
                    " FROM ultra_scan_runs"
                    " WHERE universe=? AND tf=? AND is_latest=1 AND status='completed'"
                    " ORDER BY finished_at DESC LIMIT 1",
                    (universe, tf),
                )
            elif tf:
                db.execute(
                    "SELECT id, universe, tf, nasdaq_batch, status,"
                    " finished_at, total_candidates"
                    " FROM ultra_scan_runs"
                    " WHERE tf=? AND is_latest=1 AND status='completed'"
                    " ORDER BY finished_at DESC LIMIT 1",
                    (tf,),
                )
            else:
                db.execute(
                    "SELECT id, universe, tf, nasdaq_batch, status,"
                    " finished_at, total_candidates"
                    " FROM ultra_scan_runs"
                    " WHERE is_latest=1 AND status='completed'"
                    " ORDER BY finished_at DESC LIMIT 1",
                )
            row = db.fetchone()

        if not row:
            return {"has_data": False, "message": "No completed Ultra Scan found"}

        finished_at = row.get("finished_at") or ""
        age_s = 0
        try:
            import datetime as _dt
            if finished_at:
                ts = finished_at.replace("Z", "+00:00")
                dt = _dt.datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_dt.timezone.utc)
                now = _dt.datetime.now(_dt.timezone.utc)
                age_s = max(0, int((now - dt).total_seconds()))
        except Exception:
            age_s = 0

        return {
            "has_data":         True,
            "scan_run_id":      row.get("id"),
            "status":           row.get("status") or "completed",
            "universe":         row.get("universe") or "",
            "tf":               row.get("tf") or "",
            "nasdaq_batch":     row.get("nasdaq_batch") or "",
            "finished_at":      finished_at,
            "total_candidates": int(row.get("total_candidates") or 0),
            "data_age_seconds": age_s,
        }
    except Exception as exc:
        log.warning("get_ultra_latest_from_db error: %s", exc)
        return {"has_data": False, "error": str(exc)}


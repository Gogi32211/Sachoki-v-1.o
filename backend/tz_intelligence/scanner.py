"""Run TZ Intelligence classification across a universe."""
from __future__ import annotations
import csv
import os
from typing import List, Optional

from .classifier import classify_tz_event
from .matrix_loader import load_matrix

# ── Input allowlists (validated before any path construction) ─────────────────

_VALID_UNIVERSES = frozenset({
    "sp500", "nasdaq", "nasdaq_gt5", "russell2k", "all_us", "split",
})
_VALID_TFS = frozenset({"1d", "4h", "1h", "1wk"})
_VALID_NASDAQ_BATCHES = frozenset({"", "a_m", "n_z", "a_f", "g_m", "n_s", "t_z"})
_VALID_SCAN_MODES = frozenset({"latest", "history"})

# Batches valid only for nasdaq_gt5 (4-way split for large 4H scans)
_NASDAQ_GT5_BATCHES = frozenset({"a_f", "g_m", "n_s", "t_z"})


def _stat_path(universe: str, tf: str, nasdaq_batch: str = "") -> str:
    if nasdaq_batch:
        if universe == "nasdaq":
            return f"stock_stat_tz_wlnbb_nasdaq_{nasdaq_batch}_{tf}.csv"
        if universe == "nasdaq_gt5":
            return f"stock_stat_tz_wlnbb_nasdaq_gt5_{nasdaq_batch}_{tf}.csv"
    return f"stock_stat_tz_wlnbb_{universe}_{tf}.csv"


def _sort_key(row: dict) -> str:
    """Sort key that uses bar_datetime when available (preserves intraday order)."""
    return row.get("bar_datetime") or row.get("date", "")


def _build_result(clf: dict, bar_row: dict, debug: bool) -> dict:
    """Build the result dict from a classifier output and raw CSV row."""
    r: dict = {
        # core
        "ticker":            clf["ticker"],
        "date":              clf["date"],
        "bar_datetime":      bar_row.get("bar_datetime") or clf["date"],
        "close":             bar_row.get("close"),
        "volume":            bar_row.get("volume"),
        "price_bucket":      bar_row.get("price_bucket", ""),
        "final_signal":      clf["final_signal"],
        "composite_pattern": clf["composite_pattern"],
        "seq4":              clf["seq4"],
        "lane1":             clf["lane1"],
        "lane3":             clf["lane3"],
        "role":              clf["role"],
        "score":             clf["score"],
        "quality":           clf["quality"],
        "action":            clf["action"],
        "vol_bucket":        clf["vol_bucket"],
        "wick_suffix":       clf["wick_suffix"],
        "reason_codes":      clf["reason_codes"],
        "explanation":       clf["explanation"],
        # EMA
        "above_ema20":       clf["above_ema20"],
        "above_ema50":       clf["above_ema50"],
        "above_ema89":       clf["above_ema89"],
        "ema20_reclaim":     clf["ema20_reclaim"],
        "ema50_reclaim":     clf["ema50_reclaim"],
        "ema89_reclaim":     clf["ema89_reclaim"],
        # Conflict
        "conflict_flag":          clf["conflict_flag"],
        "conflict_resolution":    clf["conflict_resolution"],
        "conflicting_rule_ids":   clf["conflicting_rule_ids"],
        # Flags
        "good_flags":        clf["good_flags"],
        "reject_flags":      clf["reject_flags"],
        # Price position
        "price_position_4bar": clf["price_position_4bar"],
        "breaks_4bar_high":  clf["breaks_4bar_high"],
        "breaks_4bar_low":   clf["breaks_4bar_low"],
        # Volume vs history
        "final_volume_vs_prev1": clf["final_volume_vs_prev1"],
        "final_volume_vs_prev2": clf["final_volume_vs_prev2"],
        "final_volume_vs_prev3": clf["final_volume_vs_prev3"],
        # Matched rule debug fields
        "matched_rule_id":           clf["matched_rule_id"],
        "matched_rule_type":         clf["matched_rule_type"],
        "matched_universe":          clf["matched_universe"],
        "matched_status":            clf["matched_status"],
        "matched_med10d_pct":        clf["matched_med10d_pct"],
        "matched_fail10d_pct":       clf["matched_fail10d_pct"],
        "matched_avg10d_pct":        clf["matched_avg10d_pct"],
        "matched_source_file":       clf["matched_source_file"],
        "matched_rule_notes":        clf["matched_rule_notes"],
        "matched_composite_rule_id": clf["matched_composite_rule_id"],
        "matched_seq4_rule_id":      clf["matched_seq4_rule_id"],
        "matched_reject_rule_id":    clf["matched_reject_rule_id"],
        # PULLBACK_GO proof fields
        "prior_pullback_ready_found":        clf["prior_pullback_ready_found"],
        "prior_pullback_ready_bars_ago":     clf["prior_pullback_ready_bars_ago"],
        "prior_pullback_ready_signal":       clf["prior_pullback_ready_signal"],
        "prior_pullback_ready_composite":    clf["prior_pullback_ready_composite"],
        "prior_pullback_ready_role":         clf["prior_pullback_ready_role"],
        "pullback_high":                     clf["pullback_high"],
        "current_close_above_pullback_high": clf["current_close_above_pullback_high"],
        # Liquidity fields
        "dollar_volume":    clf["dollar_volume"],
        "liquidity_tier":   clf["liquidity_tier"],
        # ABR overlay fields
        "abr_category":          clf["abr_category"],
        "abr_sequence":          clf["abr_sequence"],
        "abr_prev1_composite":   clf["abr_prev1_composite"],
        "abr_prev2_composite":   clf["abr_prev2_composite"],
        "abr_prev1_comp_med10d": clf["abr_prev1_comp_med10d"],
        "abr_prev2_comp_med10d": clf["abr_prev2_comp_med10d"],
        "abr_prev1_quality":     clf["abr_prev1_quality"],
        "abr_prev2_quality":     clf["abr_prev2_quality"],
        "abr_gate_pass":       clf["abr_gate_pass"],
        "abr_rule_found":      clf["abr_rule_found"],
        "abr_n":               clf["abr_n"],
        "abr_med10d_pct":      clf["abr_med10d_pct"],
        "abr_avg10d_pct":      clf["abr_avg10d_pct"],
        "abr_fail10d_pct":     clf["abr_fail10d_pct"],
        "abr_win10d_pct":      clf["abr_win10d_pct"],
        "abr_action_hint":       clf["abr_action_hint"],
        "abr_role_suggestion":   clf["abr_role_suggestion"],
        # ABR context flags
        "abr_conflict_flag":     clf["abr_conflict_flag"],
        "abr_confirmation_flag": clf["abr_confirmation_flag"],
        "abr_context_type":      clf["abr_context_type"],
    }
    if debug:
        r["debug_trace"] = clf["debug_trace"]
    return r


def _make_error_clf(ticker: str, date: str, error_type: str, error_msg: str = "") -> dict:
    """Return a classifier-compatible placeholder for error / missing-data rows."""
    _blank_abr = {
        "abr_category": "UNKNOWN", "abr_sequence": "",
        "abr_prev1_composite": "", "abr_prev2_composite": "",
        "abr_prev1_comp_med10d": None, "abr_prev2_comp_med10d": None,
        "abr_prev1_quality": "UNKNOWN", "abr_prev2_quality": "UNKNOWN",
        "abr_gate_pass": False, "abr_rule_found": False,
        "abr_n": 0, "abr_med10d_pct": None, "abr_avg10d_pct": None,
        "abr_fail10d_pct": None, "abr_win10d_pct": None,
        "abr_action_hint": "NO_ABR_EDGE", "abr_role_suggestion": "",
        "abr_conflict_flag": "", "abr_confirmation_flag": "", "abr_context_type": "",
    }
    return {
        "ticker": ticker, "date": date,
        "final_signal": "", "composite_pattern": "", "seq4": "",
        "lane1": "", "lane3": "",
        "role": error_type,   # e.g. "CLASSIFICATION_ERROR" or "DATA_MISSING"
        "score": 0, "quality": "—", "action": "IGNORE",
        "vol_bucket": "", "wick_suffix": "",
        "explanation": error_msg,
        "reason_codes": [error_msg] if error_msg else [],
        "above_ema20": False, "above_ema50": False, "above_ema89": False,
        "ema20_reclaim": False, "ema50_reclaim": False, "ema89_reclaim": False,
        "conflict_flag": "", "conflict_resolution": "", "conflicting_rule_ids": [],
        "good_flags": [], "reject_flags": [],
        "price_position_4bar": None, "breaks_4bar_high": False, "breaks_4bar_low": False,
        "final_volume_vs_prev1": None, "final_volume_vs_prev2": None, "final_volume_vs_prev3": None,
        "matched_rule_id": "", "matched_rule_type": "", "matched_universe": "",
        "matched_status": "", "matched_med10d_pct": None, "matched_fail10d_pct": None,
        "matched_avg10d_pct": None, "matched_source_file": "", "matched_rule_notes": "",
        "matched_composite_rule_id": "", "matched_seq4_rule_id": "", "matched_reject_rule_id": "",
        "prior_pullback_ready_found": False, "prior_pullback_ready_bars_ago": None,
        "prior_pullback_ready_signal": "", "prior_pullback_ready_composite": "",
        "prior_pullback_ready_role": "", "pullback_high": None,
        "current_close_above_pullback_high": False,
        "dollar_volume": 0.0, "liquidity_tier": "UNKNOWN",
        "debug_trace": [],
        **_blank_abr,
    }


def _classify_bar(bar_row: dict, history: list, all4: list,
                  matrix, universe: str, debug: bool) -> dict | None:
    """Classify one bar. Returns result dict or None if it should be skipped."""
    # For intraday, expose bar_datetime as the date so it appears in clf["date"]
    bar = bar_row
    if bar_row.get("bar_datetime") and bar_row["bar_datetime"] != bar_row.get("date", ""):
        bar = {**bar_row, "date": bar_row["bar_datetime"]}

    try:
        low4  = min(float(b.get("low")  or float("inf")) for b in all4)
        high4 = max(float(b.get("high") or 0)            for b in all4)
    except (TypeError, ValueError):
        low4 = high4 = None

    clf = classify_tz_event(
        bar, history, matrix,
        current_low_4bar=low4,
        current_high_4bar=high4,
        scan_universe=universe,
        debug=debug,
    )
    return clf


def run_intelligence_scan(
    universe: str = "sp500",
    tf: str = "1d",
    nasdaq_batch: str = "",
    min_price: float = 0,
    # note: nasdaq_gt5 auto-enforces min_price >= 5 regardless of caller value
    max_price: float = 1e9,
    min_volume: float = 0,
    role_filter: str = "all",
    scan_mode: str = "latest",
    limit: int = 500,
    debug: bool = False,
    stat_path: str | None = None,
) -> dict:
    """
    Read the existing TZ/WLNBB stock_stat CSV, classify every ticker,
    return sorted results.

    scan_mode='latest'  — one result per ticker (most recent bar only).
    scan_mode='history' — one result per bar across all history.

    stat_path — optional override (used by ULTRA's lazy enrichment to point
    at a private subset CSV instead of the canonical file). When None, the
    canonical resolution is used unchanged.
    """
    # ── Input validation (must happen before any path construction) ───────────
    if universe not in _VALID_UNIVERSES:
        return {"results": [], "error": f"Invalid universe '{universe}'. "
                f"Allowed: {sorted(_VALID_UNIVERSES)}"}
    if tf not in _VALID_TFS:
        return {"results": [], "error": f"Invalid timeframe '{tf}'. "
                f"Allowed: {sorted(_VALID_TFS)}"}
    if nasdaq_batch not in _VALID_NASDAQ_BATCHES:
        return {"results": [], "error": f"Invalid nasdaq_batch '{nasdaq_batch}'. "
                f"Allowed: {sorted(_VALID_NASDAQ_BATCHES)}"}
    if scan_mode not in _VALID_SCAN_MODES:
        return {"results": [], "error": f"Invalid scan_mode '{scan_mode}'. "
                f"Allowed: {sorted(_VALID_SCAN_MODES)}"}

    # nasdaq_gt5 enforces price >= 5 — cannot be overridden by caller
    if universe == "nasdaq_gt5":
        min_price = max(min_price, 5.0)

    # ULTRA may pass an explicit subset CSV path. Otherwise resolve canonical.
    if stat_path is not None:
        if not os.path.exists(stat_path):
            return {
                "results": [],
                "error": (
                    f"ULTRA stat_path override not found: {stat_path}"
                ),
            }
    else:
        stat_path = _stat_path(universe, tf, nasdaq_batch)
        if not os.path.exists(stat_path):
            stat_path = f"stock_stat_tz_wlnbb_{universe}_{tf}.csv"
        if not os.path.exists(stat_path):
            stat_path = f"stock_stat_tz_wlnbb_{tf}.csv"
    if not os.path.exists(stat_path):
        return {
            "results": [],
            "error": (
                f"No stock_stat_tz_wlnbb CSV found for universe={universe} tf={tf}. "
                "Run TZ/WLNBB → Generate Stock Stat first."
                + (" Use the NASDAQ > $5 universe option." if universe == "nasdaq_gt5" else "")
            ),
        }

    matrix = load_matrix()

    # No live cross-filter at query time.
    # The stock_stat CSV IS the authoritative ticker source for the split universe —
    # it was generated from split_service at generation time (split_universe_latest.csv).
    # Filtering here against the current live split window would silently drop tickers
    # whose split event has shifted phases since generation.
    rows_by_ticker: dict[str, list] = {}
    with open(stat_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ticker = row.get("ticker", "")
            if not ticker:
                continue
            rows_by_ticker.setdefault(ticker, []).append(row)

    results: list = []
    dropped_tickers:      list = []
    classification_errors: list = []
    classified_count = 0
    stock_stat_unique = len(rows_by_ticker)

    # For split universe latest mode: all tickers from stock_stat must appear in output.
    # NO_EDGE is a valid result — do not silently drop.  Classification exceptions must
    # produce a CLASSIFICATION_ERROR row rather than silently skipping the ticker.
    require_all_tickers = (universe == "split" and scan_mode == "latest")

    for ticker, rows in rows_by_ticker.items():
        rows.sort(key=_sort_key)

        if scan_mode == "latest":
            # ── Latest mode: classify only the most recent bar ────────────────
            latest = rows[-1]
            try:
                cl  = float(latest.get("close")  or 0)
                vol = float(latest.get("volume") or 0)
            except (TypeError, ValueError):
                cl = vol = 0.0
            if not require_all_tickers:
                if cl < min_price or cl > max_price:
                    dropped_tickers.append(ticker)
                    continue
                if min_volume > 0 and vol < min_volume:
                    dropped_tickers.append(ticker)
                    continue

            history = rows[-4:-1]
            all4    = rows[-4:]
            try:
                clf = _classify_bar(latest, history, all4, matrix, universe, debug)
            except Exception as exc:
                if require_all_tickers:
                    date_str = latest.get("bar_datetime") or latest.get("date", "")
                    clf = _make_error_clf(ticker, date_str, "CLASSIFICATION_ERROR", str(exc))
                    classification_errors.append({"ticker": ticker, "error": str(exc)})
                else:
                    dropped_tickers.append(ticker)
                    continue

            is_no_edge = (clf["role"] == "NO_EDGE" and clf["score"] == 0)
            # Split: always include (NO_EDGE is meaningful — no rule matched)
            if is_no_edge and not require_all_tickers:
                dropped_tickers.append(ticker)
                continue
            if role_filter and role_filter.upper() not in ("ALL", clf["role"]):
                continue

            results.append(_build_result(clf, latest, debug))
            classified_count += 1

        else:
            # ── History mode: classify every bar ─────────────────────────────
            for i, bar in enumerate(rows):
                try:
                    cl  = float(bar.get("close")  or 0)
                    vol = float(bar.get("volume") or 0)
                except (TypeError, ValueError):
                    cl = vol = 0.0
                if cl < min_price or cl > max_price:
                    continue
                if min_volume > 0 and vol < min_volume:
                    continue

                history = rows[max(0, i - 3):i]
                all4    = rows[max(0, i - 3):i + 1]
                try:
                    clf = _classify_bar(bar, history, all4, matrix, universe, debug)
                except Exception as exc:
                    continue  # history mode: skip erroring bars

                if clf["role"] == "NO_EDGE" and clf["score"] == 0:
                    continue
                if role_filter and role_filter.upper() not in ("ALL", clf["role"]):
                    continue

                results.append(_build_result(clf, bar, debug))

    _role_sort = {
        "SHORT_GO": 0, "BULL_A": 1, "PULLBACK_READY_A": 2,
        "SHORT_WATCH": 3, "BULL_B": 4, "PULLBACK_READY_B": 5,
        "PULLBACK_WATCH": 6, "BULL_WATCH": 7,
        "REJECT": 8, "NO_EDGE": 9,
        "CLASSIFICATION_ERROR": 98, "DATA_MISSING": 99,
    }
    results.sort(key=lambda r: (_role_sort.get(r["role"], 50), -(r["score"] or 0)))
    return {
        "results": results[:limit],
        "total":   len(results),
        "debug": {
            "stock_stat_unique_tickers": stock_stat_unique,
            "classified_tickers":        classified_count,
            "dropped_tickers_count":     len(dropped_tickers),
            "dropped_tickers":           dropped_tickers,
            "classification_errors":     classification_errors,
        },
    }

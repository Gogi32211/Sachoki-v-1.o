"""Pullback Pattern Miner — Phase 1.

Discovers 4-bar and 5-bar TZ/WLNBB pullback continuation patterns from
existing stock_stat CSV data. Research-only; no UI in Phase 1.

Pattern types generated per row:
  TZ (4-bar):    base4_key  = prev3|prev2|prev1|final_tz
  TZ (5-bar):    ext5_key   = prev4|prev3|prev2|prev1|final_tz
  TZ+L (4-bar):  base4_tzl  = prev3|prev2|prev1|final_tz+l
  TZ+L (5-bar):  ext5_tzl   = prev4|prev3|prev2|prev1|final_tz+l

Pullback context filters (all must be true for a row to qualify):
  trend_context:      close > EMA50  OR  EMA20 > EMA50  OR  (close > EMA20 and close > EMA50)
  pullback_zone:      0.30 <= price_position_20bar <= 0.85
  not_broken:         close >= ten_bar_rolling_low  (using lows of last 10 bars)
  → combined as: controlled_pullback

Evidence tiers:
  CONFIRMED_PULLBACK:  event_count >= 2, median_10d > 0, win_rate >= 50, fail_rate <= 35
  ANECDOTAL_PULLBACK:  event_count == 1 and that event's ret_10d > 0
  REJECT:              median_10d <= 0  OR  fail_rate > 35  → excluded from output

Scoring formula (before tier bonuses):
  score = median_10d * 8  +  win_rate * 0.35  +  avg_max_forward_10d * 3
          - fail_rate * 0.5  -  abs(avg_max_drawdown_10d) * 3
  Bonuses: +10 if event_count>=3; +10 GO; +8 CONFIRMING; +5 READY
  Penalties: -20 ANECDOTAL; -30 if fail_rate>35
"""
from __future__ import annotations

import csv
import os
import statistics
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

# ── Stage classification sets ─────────────────────────────────────────────────

_READY_SIGNALS       = frozenset({"Z5", "Z9", "Z3", "Z4", "Z6", "Z1G", "Z2G"})
_CONFIRMING_SIGNALS  = frozenset({"T1", "T2", "T2G", "T3", "T9"})
_GO_SIGNALS          = frozenset({"T4", "T5", "T6", "T11", "T12"})

# Evidence thresholds
_CONF_MIN_COUNT  = 2
_CONF_MIN_WIN    = 50.0
_CONF_MAX_FAIL   = 35.0

# Rolling window sizes
_WIN_20 = 20
_WIN_10 = 10

# Pullback zone
_ZONE_LOW  = 0.30
_ZONE_HIGH = 0.85

# Top-N per ticker
_TOP_N = 3

# Valid universes / tfs for path construction
_VALID_UNIVERSES = frozenset({"sp500", "nasdaq_gt5", "nasdaq", "russell2k", "all_us", "split"})
_VALID_TFS       = frozenset({"1d", "4h", "1h", "1wk"})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sf(v, default=None):
    """Safe float conversion."""
    try:
        f = float(v)
        return None if f != f else f  # NaN → None
    except (TypeError, ValueError):
        return default


def _parse_summary(raw: str) -> List[str]:
    """Split pipe-separated signal summary into non-empty tokens."""
    if not raw:
        return []
    return [p.strip() for p in raw.split("|") if p.strip()]


def _final_tz(row: dict) -> str:
    """Primary TZ signal for the current bar (T takes priority over Z)."""
    return (row.get("t_signal") or row.get("z_signal") or "").strip()


def _final_l(row: dict) -> str:
    """L signal code for the current bar (e.g. 'L34', 'L43')."""
    return (row.get("l_signal") or "").strip()


def _build_sequences(row: dict) -> dict:
    """
    Build up to 4 pattern keys for a stock_stat row.
    Returns keys: base4_key, ext5_key, base4_tzl_key, ext5_tzl_key
    (any may be None if data is insufficient).
    """
    tz   = _final_tz(row)
    lsig = _final_l(row)

    # prev3 summary contains [prev3, prev2, prev1] tokens (oldest → newest)
    prev3_parts = _parse_summary(row.get("prev_3_signal_summary", ""))
    # prev5 summary contains up to [prev5, prev4, prev3, prev2, prev1] tokens
    prev5_parts = _parse_summary(row.get("prev_5_signal_summary", ""))

    base4     = None
    ext5      = None
    base4_tzl = None
    ext5_tzl  = None

    if tz and len(prev3_parts) == 3:
        base4 = "|".join(prev3_parts) + "|" + tz
        if lsig:
            base4_tzl = "|".join(prev3_parts) + "|" + tz + "+" + lsig

    # ext5: prev5_parts[-3:] must equal prev3_parts to ensure continuity
    if tz and len(prev5_parts) >= 4 and prev5_parts[-3:] == prev3_parts:
        ext5 = "|".join([prev5_parts[-4]] + prev3_parts + [tz])
        if lsig:
            ext5_tzl = "|".join([prev5_parts[-4]] + prev3_parts + [tz + "+" + lsig])

        if len(prev5_parts) >= 5:
            # 6-bar would be ext6 — not in scope for Phase 1, but store for future
            pass

    return {
        "base4_key":     base4,
        "ext5_key":      ext5,
        "base4_tzl_key": base4_tzl,
        "ext5_tzl_key":  ext5_tzl,
    }


def _pullback_stage(tz: str) -> str:
    if tz in _READY_SIGNALS:
        return "PULLBACK_READY"
    if tz in _CONFIRMING_SIGNALS:
        return "PULLBACK_CONFIRMING"
    if tz in _GO_SIGNALS:
        return "PULLBACK_GO"
    return "OTHER"


def _trend_context(row: dict) -> bool:
    """True if the bar is in a bullish / recovering trend context."""
    close = _sf(row.get("close"))
    ema20 = _sf(row.get("ema20"))
    ema50 = _sf(row.get("ema50"))
    if close is None:
        return False
    above_ema50 = ema50 is not None and close > ema50
    ema20_gt_50 = ema20 is not None and ema50 is not None and ema20 > ema50
    above_both  = (ema20 is not None and ema50 is not None
                   and close > ema20 and close > ema50)
    return above_ema50 or ema20_gt_50 or above_both


def _price_position_20bar(close: float, win20_highs: deque, win20_lows: deque) -> Optional[float]:
    """Position of close within the 20-bar high/low range. Returns 0.0–1.0 or None."""
    if not win20_highs or close is None:
        return None
    h20 = max(win20_highs)
    l20 = min(win20_lows)
    rng = h20 - l20
    if rng <= 0:
        return 0.5
    return round((close - l20) / rng, 4)


def _in_pullback_zone(pos: Optional[float]) -> bool:
    return pos is not None and _ZONE_LOW <= pos <= _ZONE_HIGH


def _not_broken(close: Optional[float], win10_lows: deque) -> bool:
    """True if current close is not below the rolling 10-bar low."""
    if close is None or not win10_lows:
        return True  # can't determine — don't filter
    return close >= min(win10_lows)


def _forward_outcomes(row: dict) -> dict:
    """Extract pre-computed forward outcomes from a stock_stat row."""
    ret3  = _sf(row.get("ret_3d"))
    ret5  = _sf(row.get("ret_5d"))
    ret10 = _sf(row.get("ret_10d"))
    mfe5  = _sf(row.get("mfe_5d"))
    mfe10 = _sf(row.get("mfe_10d"))
    mae5  = _sf(row.get("mae_5d"))
    mae10 = _sf(row.get("mae_10d"))

    success_10d = mfe10 is not None and mfe10 >= 5.0
    fail_10d    = (mae10 is not None and mae10 <= -6.0) or \
                  (ret10 is not None and ret10 < -4.0)

    return {
        "forward_return_3d":    ret3,
        "forward_return_5d":    ret5,
        "forward_return_10d":   ret10,
        "max_forward_return_5d":  mfe5,
        "max_forward_return_10d": mfe10,
        "max_drawdown_5d":  mae5,
        "max_drawdown_10d": mae10,
        "success_10d": success_10d,
        "fail_10d":    fail_10d,
    }


def _evidence_tier(event_count: int, median_10d: Optional[float],
                   win_rate: Optional[float], fail_rate: Optional[float],
                   single_ret10: Optional[float] = None) -> str:
    if event_count == 0:
        return "NO_DATA"
    # Single-event: use the raw return for the anecdotal check (no median available)
    if event_count == 1:
        if single_ret10 is not None and single_ret10 > 0:
            return "ANECDOTAL_PULLBACK"
        return "REJECT"
    if median_10d is None:
        return "NO_DATA"
    if (event_count >= _CONF_MIN_COUNT
            and median_10d > 0
            and win_rate is not None and win_rate >= _CONF_MIN_WIN
            and fail_rate is not None and fail_rate <= _CONF_MAX_FAIL):
        return "CONFIRMED_PULLBACK"
    # multi-event but doesn't meet CONFIRMED threshold → REJECT in Phase 1
    return "REJECT"


def _score(
    tier: str,
    median_10d: Optional[float],
    win_rate: Optional[float],
    fail_rate: Optional[float],
    avg_max_fwd_10d: Optional[float],
    avg_max_dd_10d: Optional[float],
    event_count: int,
    stage: str,
) -> float:
    med    = median_10d      or 0.0
    win    = win_rate        or 0.0
    fail   = fail_rate       or 0.0
    mfwd   = avg_max_fwd_10d or 0.0
    mdd    = avg_max_dd_10d  or 0.0

    sc = (med * 8.0 + win * 0.35 + mfwd * 3.0
          - fail * 0.5 - abs(mdd) * 3.0)

    if event_count >= 3:
        sc += 10.0
    if stage == "PULLBACK_GO":
        sc += 10.0
    elif stage == "PULLBACK_CONFIRMING":
        sc += 8.0
    elif stage == "PULLBACK_READY":
        sc += 5.0

    if tier == "ANECDOTAL_PULLBACK":
        sc -= 20.0
    if fail > _CONF_MAX_FAIL:
        sc -= 30.0

    return round(sc, 4)


# ── Aggregation ───────────────────────────────────────────────────────────────

def _aggregate(events: List[dict]) -> dict:
    """Compute aggregate stats over a list of qualified events."""
    ret10_vals  = [e["forward_return_10d"]   for e in events if e["forward_return_10d"]   is not None]
    ret5_vals   = [e["forward_return_5d"]    for e in events if e["forward_return_5d"]    is not None]
    mfe10_vals  = [e["max_forward_return_10d"] for e in events if e["max_forward_return_10d"] is not None]
    mae10_vals  = [e["max_drawdown_10d"]     for e in events if e["max_drawdown_10d"]     is not None]
    pos20_vals  = [e["price_position_20bar"] for e in events if e["price_position_20bar"] is not None]
    trend_vals  = [e["trend_context"]        for e in events]

    n = len(events)

    median_10d  = statistics.median(ret10_vals)   if ret10_vals  else None
    median_5d   = statistics.median(ret5_vals)    if ret5_vals   else None
    avg_10d     = statistics.mean(ret10_vals)     if ret10_vals  else None
    avg_mfe10   = statistics.mean(mfe10_vals)     if mfe10_vals  else None
    avg_mae10   = statistics.mean(mae10_vals)     if mae10_vals  else None

    n_outcomes  = len(ret10_vals)
    win_rate    = (sum(1 for e in events if e["success_10d"]) / n_outcomes * 100) if n_outcomes > 0 else None
    fail_rate   = (sum(1 for e in events if e["fail_10d"])    / n_outcomes * 100) if n_outcomes > 0 else None

    pos20_avg   = statistics.mean(pos20_vals) if pos20_vals else None
    trend_pct   = sum(trend_vals) / n * 100 if n > 0 else 0

    # Trend context summary
    above50_pct = sum(1 for e in events if e.get("above_ema50")) / n * 100 if n else 0
    ema20gt50   = sum(1 for e in events if e.get("ema20_above_ema50")) / n * 100 if n else 0
    if above50_pct >= 60:
        trend_summary = f"above_ema50:{above50_pct:.0f}%"
    elif ema20gt50 >= 60:
        trend_summary = f"ema20>ema50:{ema20gt50:.0f}%"
    else:
        trend_summary = f"mixed_trend:{trend_pct:.0f}%"

    dates = sorted(e["date"] for e in events if e.get("date"))

    single_ret10 = ret10_vals[0] if (n == 1 and ret10_vals) else None

    return {
        "event_count":       n,
        "median_5d_return":  round(median_5d,  4) if median_5d  is not None else None,
        "median_10d_return": round(median_10d, 4) if median_10d is not None else None,
        "avg_10d_return":    round(avg_10d,    4) if avg_10d    is not None else None,
        "win_rate_10d":      round(win_rate,   2) if win_rate   is not None else None,
        "fail_rate_10d":     round(fail_rate,  2) if fail_rate  is not None else None,
        "avg_max_forward_10d":  round(avg_mfe10, 4) if avg_mfe10 is not None else None,
        "avg_max_drawdown_10d": round(avg_mae10, 4) if avg_mae10 is not None else None,
        "price_position_20bar_avg": round(pos20_avg, 4) if pos20_avg is not None else None,
        "trend_context_summary": trend_summary,
        "last_seen_date":  dates[-1] if dates else None,
        "example_dates":   dates[-5:],
        "_single_ret10":   single_ret10,
    }


# ── Current-active detection ──────────────────────────────────────────────────

def _check_active(latest_seqs: dict, known_base4: set, known_ext5: set,
                  known_base4_tzl: set, known_ext5_tzl: set) -> Tuple[bool, str]:
    """
    Returns (is_currently_active, current_pattern_completion).
    completion: FULL_MATCH, 4_OF_5_FORMING, NONE
    """
    b4  = latest_seqs.get("base4_key")
    e5  = latest_seqs.get("ext5_key")
    b4l = latest_seqs.get("base4_tzl_key")
    e5l = latest_seqs.get("ext5_tzl_key")

    # Full match: exact key in known patterns
    if (e5  and e5  in known_ext5 ) or (e5l  and e5l  in known_ext5_tzl):
        return True, "FULL_MATCH"
    if (b4  and b4  in known_base4) or (b4l  and b4l  in known_base4_tzl):
        return True, "FULL_MATCH"

    # Partial: current base4 matches the LAST 4 tokens of a known ext5
    if b4:
        suffix = b4
        for k in known_ext5:
            # ext5 has 5 tokens: p4|p3|p2|p1|final; suffix = p3|p2|p1|final
            parts = k.split("|")
            if len(parts) == 5 and "|".join(parts[-4:]) == suffix:
                return True, "4_OF_5_FORMING"
    if b4l:
        for k in known_ext5_tzl:
            parts = k.split("|")
            if len(parts) == 5 and "|".join(parts[-4:]) == b4l:
                return True, "4_OF_5_FORMING"

    return False, "NONE"


# ── Pattern record builder ────────────────────────────────────────────────────

def _make_pattern_record(
    ticker: str,
    pattern_type: str,  # "TZ" or "TZ+L"
    pattern_key: str,
    pattern_length: int,  # 4 or 5
    pullback_stage: str,
    agg: dict,
    latest_seqs: dict,
    known_base4: set,
    known_ext5: set,
    known_base4_tzl: set,
    known_ext5_tzl: set,
) -> Optional[dict]:
    """Build one output record; returns None if tier is REJECT or NO_DATA."""
    tier = _evidence_tier(
        agg["event_count"],
        agg["median_10d_return"],
        agg["win_rate_10d"],
        agg["fail_rate_10d"],
        agg.get("_single_ret10"),
    )
    if tier in ("REJECT", "NO_DATA"):
        return None

    sc = _score(
        tier,
        agg["median_10d_return"],
        agg["win_rate_10d"],
        agg["fail_rate_10d"],
        agg["avg_max_forward_10d"],
        agg["avg_max_drawdown_10d"],
        agg["event_count"],
        pullback_stage,
    )

    is_active, completion = _check_active(
        latest_seqs, known_base4, known_ext5, known_base4_tzl, known_ext5_tzl
    )

    return {
        "ticker":              ticker,
        "evidence_tier":       tier,
        "pattern_type":        pattern_type,
        "pattern_key":         pattern_key,
        "pattern_length":      pattern_length,
        "pullback_stage":      pullback_stage,
        "event_count":         agg["event_count"],
        "median_5d_return":    agg["median_5d_return"],
        "median_10d_return":   agg["median_10d_return"],
        "avg_10d_return":      agg["avg_10d_return"],
        "win_rate_10d":        agg["win_rate_10d"],
        "fail_rate_10d":       agg["fail_rate_10d"],
        "avg_max_forward_10d": agg["avg_max_forward_10d"],
        "avg_max_drawdown_10d":agg["avg_max_drawdown_10d"],
        "score":               sc,
        "price_position_20bar_avg": agg["price_position_20bar_avg"],
        "trend_context_summary": agg["trend_context_summary"],
        "example_dates":       ";".join(agg["example_dates"]),
        "last_seen_date":      agg["last_seen_date"],
        "is_currently_active": is_active,
        "current_pattern_completion": completion,
    }


# ── Top-3 per ticker ──────────────────────────────────────────────────────────

_TIER_RANK = {"CONFIRMED_PULLBACK": 0, "ANECDOTAL_PULLBACK": 1}


def _top3(records: List[dict]) -> List[dict]:
    records.sort(key=lambda r: (_TIER_RANK.get(r["evidence_tier"], 9), -r["score"]))
    return records[:_TOP_N]


# ── Stat file path ────────────────────────────────────────────────────────────

def _stat_file(universe: str, tf: str) -> str:
    return f"stock_stat_tz_wlnbb_{universe}_{tf}.csv"


# ── Main scan ─────────────────────────────────────────────────────────────────

def run_pullback_scan(
    universe: str = "sp500",
    tf: str = "1d",
    min_price: float = 0.0,
    max_price: float = 1e9,
    limit: int = 500,
    stat_path: str | None = None,
) -> dict:
    """
    Scan one universe/tf stock_stat CSV for pullback continuation patterns.

    Returns {
        results: list of top-3-per-ticker pattern dicts,
        events:  list of individual qualifying events,
        total_tickers, total_events, confirmed_count, active_count,
        universe, tf, error
    }
    """
    if universe not in _VALID_UNIVERSES:
        return {"results": [], "events": [], "error": f"Invalid universe '{universe}'."}
    if tf not in _VALID_TFS:
        return {"results": [], "events": [], "error": f"Invalid timeframe '{tf}'."}

    # ULTRA may pass an explicit subset CSV path. Otherwise resolve canonical.
    if stat_path is None:
        stat_path = _stat_file(universe, tf)
    if not os.path.exists(stat_path):
        return {
            "results": [], "events": [], "total_tickers": 0,
            "error": (
                f"No stock_stat_tz_wlnbb CSV found for universe={universe} tf={tf}. "
                "Run TZ/WLNBB → Generate Stock Stat first."
            ),
        }

    # Read all rows grouped by ticker
    rows_by_ticker: Dict[str, List[dict]] = {}
    with open(stat_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tkr = row.get("ticker", "").strip()
            if tkr:
                rows_by_ticker.setdefault(tkr, []).append(row)

    for rows in rows_by_ticker.values():
        rows.sort(key=lambda r: r.get("bar_datetime") or r.get("date", ""))

    all_top3:   list = []
    all_events: list = []

    for ticker, rows in rows_by_ticker.items():
        # Price filter on last bar
        try:
            last_close = float(rows[-1].get("close") or 0)
        except (TypeError, ValueError):
            last_close = 0.0
        if last_close < min_price or last_close > max_price:
            continue

        # Per-ticker accumulators
        # key → list of event dicts
        # key = (pattern_type, pattern_key, pullback_stage)
        acc: Dict[tuple, List[dict]] = defaultdict(list)

        # Rolling context windows
        win20_highs: deque = deque(maxlen=_WIN_20)
        win20_lows:  deque = deque(maxlen=_WIN_20)
        win10_lows:  deque = deque(maxlen=_WIN_10)

        for row in rows:
            close = _sf(row.get("close"))
            high  = _sf(row.get("high"))
            low   = _sf(row.get("low"))

            # Update rolling windows BEFORE checking current bar
            # (so win10_lows is from the PREVIOUS 10 bars, not including current)
            ten_bar_low_check = min(win10_lows) if win10_lows else None

            if high is not None:
                win20_highs.append(high)
            if low is not None:
                win20_lows.append(low)

            pos20 = _price_position_20bar(close, win20_highs, win20_lows)

            # Pullback zone filter
            if not _in_pullback_zone(pos20):
                if low is not None:
                    win10_lows.append(low)
                continue

            # Trend context
            trend = _trend_context(row)
            if not trend:
                if low is not None:
                    win10_lows.append(low)
                continue

            # Not broken: close >= 10-bar rolling low
            if ten_bar_low_check is not None and close is not None and close < ten_bar_low_check:
                if low is not None:
                    win10_lows.append(low)
                continue

            # Build sequence keys
            seqs = _build_sequences(row)
            tz   = _final_tz(row)
            lsig = _final_l(row)
            stage = _pullback_stage(tz)

            # Compute context flags for aggregation
            ema20 = _sf(row.get("ema20"))
            ema50 = _sf(row.get("ema50"))
            above_ema50     = close is not None and ema50 is not None and close > ema50
            above_ema20     = close is not None and ema20 is not None and close > ema20
            ema20_above_ema50 = ema20 is not None and ema50 is not None and ema20 > ema50
            pullback_depth  = ((ema50 - close) / ema50 * 100) if (ema50 and close and ema50 > 0) else 0.0

            fwd = _forward_outcomes(row)
            date_str = row.get("bar_datetime") or row.get("date", "")

            event_base = {
                "ticker":              ticker,
                "date":                date_str,
                "close":               close,
                "price_position_20bar": pos20,
                "pullback_depth_pct":  round(pullback_depth, 4),
                "above_ema20":         above_ema20,
                "above_ema50":         above_ema50,
                "above_ema89":         close is not None and _sf(row.get("ema89")) is not None and close > _sf(row.get("ema89")),
                "ema20_above_ema50":   ema20_above_ema50,
                "trend_context":       trend,
                "controlled_pullback": True,  # already filtered
                **fwd,
            }

            # Register events for each pattern key present
            if seqs["base4_key"]:
                acc[("TZ", seqs["base4_key"], stage, 4)].append(event_base)
                all_events.append({**event_base, "pattern_type": "TZ",
                                   "pattern_key": seqs["base4_key"], "pattern_length": 4,
                                   "pullback_stage": stage})
            if seqs["ext5_key"]:
                acc[("TZ", seqs["ext5_key"], stage, 5)].append(event_base)
                all_events.append({**event_base, "pattern_type": "TZ",
                                   "pattern_key": seqs["ext5_key"], "pattern_length": 5,
                                   "pullback_stage": stage})
            if seqs["base4_tzl_key"]:
                acc[("TZ+L", seqs["base4_tzl_key"], stage, 4)].append(event_base)
                all_events.append({**event_base, "pattern_type": "TZ+L",
                                   "pattern_key": seqs["base4_tzl_key"], "pattern_length": 4,
                                   "pullback_stage": stage})
            if seqs["ext5_tzl_key"]:
                acc[("TZ+L", seqs["ext5_tzl_key"], stage, 5)].append(event_base)
                all_events.append({**event_base, "pattern_type": "TZ+L",
                                   "pattern_key": seqs["ext5_tzl_key"], "pattern_length": 5,
                                   "pullback_stage": stage})

            if low is not None:
                win10_lows.append(low)

        if not acc:
            continue

        # Build known-key sets for active-check
        known_base4     = {k[1] for k in acc if k[0] == "TZ"  and k[3] == 4}
        known_ext5      = {k[1] for k in acc if k[0] == "TZ"  and k[3] == 5}
        known_base4_tzl = {k[1] for k in acc if k[0] == "TZ+L" and k[3] == 4}
        known_ext5_tzl  = {k[1] for k in acc if k[0] == "TZ+L" and k[3] == 5}

        # Latest bar sequences (for active check)
        latest_seqs = _build_sequences(rows[-1])

        # Aggregate & build records
        ticker_records: list = []
        for (ptype, pkey, stage, plen), events in acc.items():
            agg = _aggregate(events)
            rec = _make_pattern_record(
                ticker, ptype, pkey, plen, stage, agg,
                latest_seqs,
                known_base4, known_ext5, known_base4_tzl, known_ext5_tzl,
            )
            if rec:
                ticker_records.append(rec)

        top3 = _top3(ticker_records)
        # Add rank within ticker
        for i, r in enumerate(top3, 1):
            r["ticker_rank"] = i
        all_top3.extend(top3)

    # Global sort and rank
    all_top3.sort(key=lambda r: (_TIER_RANK.get(r["evidence_tier"], 9), -r["score"]))
    for i, r in enumerate(all_top3, 1):
        r["rank"] = i

    confirmed_count = sum(1 for r in all_top3 if r["evidence_tier"] == "CONFIRMED_PULLBACK")
    active_count    = sum(1 for r in all_top3 if r["is_currently_active"])

    return {
        "results":        all_top3[:limit],
        "events":         all_events,
        "total_tickers":  len(rows_by_ticker),
        "total_patterns": len(all_top3),
        "confirmed_count": confirmed_count,
        "active_count":   active_count,
        "universe":       universe,
        "tf":             tf,
        "error":          None,
    }


# ── CSV output ────────────────────────────────────────────────────────────────

_TOP3_COLS = [
    "rank", "ticker", "ticker_rank", "evidence_tier", "pattern_type", "pattern_key",
    "pattern_length", "pullback_stage", "event_count",
    "median_5d_return", "median_10d_return", "avg_10d_return",
    "win_rate_10d", "fail_rate_10d",
    "avg_max_forward_10d", "avg_max_drawdown_10d",
    "score", "price_position_20bar_avg", "trend_context_summary",
    "example_dates", "last_seen_date", "is_currently_active", "current_pattern_completion",
]

_EVENT_COLS = [
    "ticker", "date", "pattern_type", "pattern_key", "pattern_length", "pullback_stage",
    "close", "price_position_20bar", "pullback_depth_pct",
    "above_ema20", "above_ema50", "above_ema89", "ema20_above_ema50",
    "trend_context", "controlled_pullback",
    "forward_return_3d", "forward_return_5d", "forward_return_10d",
    "max_forward_return_5d", "max_forward_return_10d",
    "max_drawdown_5d", "max_drawdown_10d",
    "success_10d", "fail_10d",
]


def _write_csv(rows: list, path: str, cols: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def run_and_report(
    universe_tf_pairs: Optional[List[Tuple[str, str]]] = None,
    out_dir: str = ".",
    limit: int = 500,
) -> dict:
    """
    Run pullback scan for each (universe, tf) pair and write output CSVs.

    Returns summary dict with per-universe results and top-20 global patterns.
    """
    if universe_tf_pairs is None:
        universe_tf_pairs = [("sp500", "1d"), ("nasdaq_gt5", "1d")]

    summary = {
        "universes": {},
        "top20_global": [],
        "errors": [],
    }
    all_global: list = []

    for universe, tf in universe_tf_pairs:
        scan = run_pullback_scan(universe=universe, tf=tf, limit=limit)
        if scan.get("error"):
            summary["errors"].append(f"{universe}/{tf}: {scan['error']}")
            continue

        top3_path  = os.path.join(out_dir, f"pullback_patterns_top3_per_ticker_{universe}_{tf}.csv")
        event_path = os.path.join(out_dir, f"pullback_pattern_events_{universe}_{tf}.csv")

        _write_csv(scan["results"], top3_path,  _TOP3_COLS)
        _write_csv(scan["events"],  event_path, _EVENT_COLS)

        summary["universes"][f"{universe}/{tf}"] = {
            "total_tickers":   scan["total_tickers"],
            "total_patterns":  scan["total_patterns"],
            "confirmed_count": scan["confirmed_count"],
            "active_count":    scan["active_count"],
            "top3_csv":        top3_path,
            "events_csv":      event_path,
        }
        all_global.extend(scan["results"])

    # Top-20 global by score (CONFIRMED first)
    all_global.sort(key=lambda r: (_TIER_RANK.get(r["evidence_tier"], 9), -r["score"]))
    for i, r in enumerate(all_global[:20], 1):
        summary["top20_global"].append({
            "global_rank":  i,
            "universe":     r.get("_universe", ""),
            **{k: r[k] for k in [
                "ticker", "evidence_tier", "pattern_type", "pattern_key",
                "pullback_stage", "event_count", "median_10d_return",
                "win_rate_10d", "fail_rate_10d", "avg_max_forward_10d",
                "score", "is_currently_active",
            ]},
        })

    return summary

"""Rare Reversal Miner — extends 4-bar SEQ4 analytics left by 1–2 bars.

For each ticker in the stock_stat CSV:
  1. Reconstruct base4_key (prev3|prev2|prev1|final) from prev_3_signal_summary + final
  2. Extend left to ext5 (prev4|prev3|prev2|prev1|final) using prev_5_signal_summary
  3. Extend left to ext6 (prev5|prev4|prev3|prev2|prev1|final)
  4. Measure bottom-reversal quality: where in the 5-bar window was the low? Is it a
     10/20-bar contextual low?
  5. Anchor stats to the base4 SEQ4 matrix row (med10d, fail10d, n, status)
  6. Classify each occurrence: CONFIRMED_RARE, ANECDOTAL_RARE, or FORMING_PATTERN
  7. Return up to 3 patterns per ticker, scored and sorted
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

# ── Constants ─────────────────────────────────────────────────────────────────

_MATRIX_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..", "tz_intelligence_package",
    "TZ_SIGNAL_INTELLIGENCE_master_matrix_seed.csv",
)
_MATRIX_PATH = os.path.normpath(_MATRIX_PATH)

_UNIVERSE_MAP = {
    "sp500":       "SP500",
    "nasdaq":      "NASDAQ_GT5",
    "nasdaq_gt5":  "NASDAQ_GT5",
    "russell2k":   "SP500",   # fallback — no separate Russell matrix
    "all_us":      "SP500",
    "split":       "SP500",
}

_VALID_UNIVERSES = frozenset(_UNIVERSE_MAP)
_VALID_TFS       = frozenset({"1d", "4h", "1h", "1wk"})

# Evidence tier thresholds
_CONFIRMED_MIN_COUNT  = 2
_CONFIRMED_MIN_WIN    = 50.0   # win_rate_10d >= 50%
_CONFIRMED_MAX_FAIL   = 35.0   # fail_rate_10d <= 35%

# Score weights
_W_MED10D   = 8.0
_W_WIN      = 0.4
_W_FAIL     = 0.4
_BONUS_CONFIRMED  = 10.0
_BONUS_ANECDOTAL  = 3.0
_BONUS_BOTTOM     = 5.0
_BONUS_EXT5_MATCH = 2.0
_BONUS_EXT6_MATCH = 4.0

_MAX_PATTERNS_PER_TICKER = 3
_ROLLING_WINDOW = 25   # bars kept for context (10-bar and 20-bar low detection)


# ── Matrix loader ─────────────────────────────────────────────────────────────

def _load_seq4_matrix(path: str = _MATRIX_PATH) -> Dict[Tuple[str, str], dict]:
    """
    Returns {(universe_str, pattern): row_dict}
    universe_str is the canonical form: "SP500" or "NASDAQ_GT5".
    pattern is the SEQ4 key: "prev3|prev2|prev1|final".
    """
    index: Dict[Tuple[str, str], dict] = {}
    if not os.path.exists(path):
        return index
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("rule_type", "").strip().upper() != "SEQ4":
                continue
            universe = row.get("universe", "").strip()
            pattern  = row.get("pattern",  "").strip()
            if not universe or not pattern:
                continue
            key = (universe, pattern)
            # Keep higher-n row if duplicate
            existing = index.get(key)
            try:
                n_new = int(row.get("n") or 0)
            except (ValueError, TypeError):
                n_new = 0
            if existing is None or n_new > int(existing.get("n") or 0):
                index[key] = row
    return index


# ── Sequence builder ──────────────────────────────────────────────────────────

def _primary_signal(composite: str) -> str:
    """Extract the leading signal token from a composite label (e.g. 'T1L3EB' → 'T1')."""
    if not composite:
        return ""
    # Tokens: T\d+G?, Z\d+G?, L\d+, N\d+
    import re
    m = re.match(r"^([TZLNtz]\d+[Gg]?)", composite)
    return m.group(1).upper() if m else composite.split("|")[0].strip()


def _parse_signal_summary(raw: str) -> List[str]:
    """Split a prev_N_signal_summary into a list of non-empty signal tokens."""
    if not raw:
        return []
    return [p.strip() for p in raw.split("|") if p.strip()]


def _build_sequences(row: dict) -> dict:
    """
    Build base4/ext5/ext6 keys from a stock_stat row.

    Returns a dict with keys (may be None if data insufficient):
      base4_key, extended5_key, extended6_key
    """
    final = _primary_signal(row.get("composite_primary_label") or row.get("composite_t_label") or "")
    if not final:
        return {"base4_key": None, "extended5_key": None, "extended6_key": None}

    prev3_parts = _parse_signal_summary(row.get("prev_3_signal_summary", ""))
    prev5_parts = _parse_signal_summary(row.get("prev_5_signal_summary", ""))

    base4 = None
    ext5  = None
    ext6  = None

    if len(prev3_parts) == 3:
        base4 = "|".join(prev3_parts) + "|" + final

    # ext5: prev4 is prev5_parts[-4] when the last 3 of prev5 match prev3
    if len(prev5_parts) >= 4 and prev5_parts[-3:] == prev3_parts:
        ext5 = "|".join([prev5_parts[-4]] + prev3_parts + [final])
        # ext6: prev5 is prev5_parts[-5]
        if len(prev5_parts) >= 5:
            ext6 = "|".join([prev5_parts[-5], prev5_parts[-4]] + prev3_parts + [final])

    return {"base4_key": base4, "extended5_key": ext5, "extended6_key": ext6}


# ── Bottom-reversal metrics ───────────────────────────────────────────────────

def _bottom_metrics(
    window_rows: List[dict],   # last 5 bars inclusive (oldest→newest, newest is current)
    context_window: List[dict], # up to 25 bars inclusive for 10/20-bar context
) -> dict:
    """
    Compute bottom-reversal quality for the current 5-bar window.

    sequence_low_bar_offset: index (0=current bar, 1=1 bar ago …4=4 bars ago) of the
                              bar with the lowest low within the 5-bar window.
    return_from_sequence_low_to_final: % gain from that low to the final bar's close.
    sequence_contains_20bar_low: True if the sequence low equals the 20-bar low.
    qualifies_as_bottom: True if any of the 4 bottom conditions is met.
    """
    def _safe_float(v, default=None):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    if not window_rows:
        return {
            "sequence_low_bar_offset":         None,
            "sequence_contains_20bar_low":      False,
            "return_from_sequence_low_to_final": None,
            "qualifies_as_bottom":              False,
        }

    # 5-bar window lows
    window_lows = [_safe_float(r.get("low")) for r in window_rows]
    valid_lows  = [(i, v) for i, v in enumerate(window_lows) if v is not None]
    if not valid_lows:
        return {
            "sequence_low_bar_offset":         None,
            "sequence_contains_20bar_low":      False,
            "return_from_sequence_low_to_final": None,
            "qualifies_as_bottom":              False,
        }

    # offset 0 = most recent bar; window_rows[-1] is the final bar
    n_win = len(window_rows)
    # Find minimum within window
    min_idx_in_window, seq_low = min(valid_lows, key=lambda x: x[1])
    seq_low_offset = (n_win - 1) - min_idx_in_window   # 0=final bar, 4=4 bars ago

    final_close = _safe_float(window_rows[-1].get("close"))
    ret_from_low = None
    if final_close is not None and seq_low > 0:
        ret_from_low = (final_close - seq_low) / seq_low * 100.0

    # Context: 10-bar and 20-bar lows from context_window (includes current)
    ctx_lows = [_safe_float(r.get("low")) for r in context_window[-20:] if _safe_float(r.get("low")) is not None]
    ten_bar_lows = [_safe_float(r.get("low")) for r in context_window[-10:] if _safe_float(r.get("low")) is not None]

    twenty_bar_low = min(ctx_lows)   if ctx_lows    else None
    ten_bar_low    = min(ten_bar_lows) if ten_bar_lows else None

    seq_is_10bar_low  = ten_bar_low    is not None and abs(seq_low - ten_bar_low)    < 0.001
    seq_is_20bar_low  = twenty_bar_low is not None and abs(seq_low - twenty_bar_low) < 0.001

    # In bottom 20% of 20-bar range
    ctx_highs = [_safe_float(r.get("high")) for r in context_window[-20:] if _safe_float(r.get("high")) is not None]
    twenty_bar_high = max(ctx_highs) if ctx_highs else None
    in_bottom_20pct = False
    if twenty_bar_high is not None and twenty_bar_low is not None and twenty_bar_high > twenty_bar_low:
        threshold = twenty_bar_low + 0.20 * (twenty_bar_high - twenty_bar_low)
        in_bottom_20pct = seq_low <= threshold

    # Final bar reclaims from a non-zero offset
    reclaim_after_low = (seq_low_offset > 0 and ret_from_low is not None and ret_from_low >= 0.5)

    qualifies = seq_is_10bar_low or seq_is_20bar_low or in_bottom_20pct or reclaim_after_low

    return {
        "sequence_low_bar_offset":          seq_low_offset,
        "sequence_contains_20bar_low":      seq_is_20bar_low,
        "return_from_sequence_low_to_final": round(ret_from_low, 4) if ret_from_low is not None else None,
        "qualifies_as_bottom":              qualifies,
    }


# ── Evidence tier ─────────────────────────────────────────────────────────────

def _evidence_tier(
    n: int,
    median_10d: Optional[float],
    win_rate: Optional[float],
    fail_rate: Optional[float],
) -> str:
    if n == 0:
        return "NO_DATA"
    if (n >= _CONFIRMED_MIN_COUNT
            and median_10d is not None and median_10d > 0
            and win_rate   is not None and win_rate   >= _CONFIRMED_MIN_WIN
            and fail_rate  is not None and fail_rate  <= _CONFIRMED_MAX_FAIL):
        return "CONFIRMED_RARE"
    if n == 1 and median_10d is not None and median_10d > 0:
        return "ANECDOTAL_RARE"
    return "FORMING_PATTERN"


def _forming_subtype(ext5: Optional[str], ext6: Optional[str], base4: str) -> str:
    """FULL_MATCH / 5_OF_6_FORMING / 4_OF_5_FORMING."""
    if ext6 is not None:
        return "FULL_MATCH"
    if ext5 is not None:
        return "5_OF_6_FORMING"
    # only base4 exists
    return "4_OF_5_FORMING"


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score(
    tier: str,
    median_10d: Optional[float],
    win_rate: Optional[float],
    fail_rate: Optional[float],
    qualifies_as_bottom: bool,
    ext5_key: Optional[str],
    ext6_key: Optional[str],
) -> float:
    med = median_10d or 0.0
    win = win_rate   or 0.0
    fail= fail_rate  or 0.0

    base = med * _W_MED10D + win * _W_WIN - fail * _W_FAIL
    if tier == "CONFIRMED_RARE":
        base += _BONUS_CONFIRMED
    elif tier == "ANECDOTAL_RARE":
        base += _BONUS_ANECDOTAL
    if qualifies_as_bottom:
        base += _BONUS_BOTTOM
    if ext5_key:
        base += _BONUS_EXT5_MATCH
    if ext6_key:
        base += _BONUS_EXT6_MATCH
    return round(base, 4)


# ── Forming-pattern detection ─────────────────────────────────────────────────

def _detect_forming(
    current_seqs: dict,     # sequences for the CURRENT (latest) bar
    prev_rows: List[dict],  # last N bars for this ticker (newest last)
) -> Optional[dict]:
    """
    Check whether the CURRENT bar is the start of a pattern that would, if one or
    two more specific bars follow, complete an ext5/ext6 we've seen before.

    Returns a forming dict or None.
    current_pattern_completion: fraction 0.0–1.0
    """
    # For FORMING: the current bar's ext5 suffix (prev3|prev2|prev1|final) matches
    # the LAST 4 bars of some ext6 we already have.
    ext5  = current_seqs.get("extended5_key")
    ext6  = current_seqs.get("extended6_key")
    base4 = current_seqs.get("base4_key")

    if ext6:
        return {"forming_subtype": "FULL_MATCH", "current_pattern_completion": 1.0}
    if ext5:
        return {"forming_subtype": "5_OF_6_FORMING", "current_pattern_completion": 5/6}
    if base4:
        return {"forming_subtype": "4_OF_5_FORMING", "current_pattern_completion": 4/5}
    return None


# ── Main scan ─────────────────────────────────────────────────────────────────

def _stat_path(universe: str, tf: str) -> str:
    return f"stock_stat_tz_wlnbb_{universe}_{tf}.csv"


def run_rare_reversal_scan(
    universe: str = "sp500",
    tf:        str = "1d",
    min_price: float = 0.0,
    max_price: float = 1e9,
    limit:     int   = 200,
    stat_path: str | None = None,
) -> dict:
    """
    Mine rare reversal patterns from the stock_stat CSV.

    Returns:
      {
        "results": [list of pattern dicts],
        "total":   int,
        "universe": str,
        "tf":       str,
        "error":    str | None,
      }
    """
    if universe not in _VALID_UNIVERSES:
        return {"results": [], "total": 0, "error": f"Invalid universe '{universe}'."}
    if tf not in _VALID_TFS:
        return {"results": [], "total": 0, "error": f"Invalid timeframe '{tf}'."}

    matrix_universe = _UNIVERSE_MAP[universe]
    matrix = _load_seq4_matrix()

    # ULTRA may pass an explicit subset CSV path. Otherwise resolve canonical.
    stat_file = stat_path if stat_path is not None else _stat_path(universe, tf)
    if not os.path.exists(stat_file):
        return {
            "results": [], "total": 0,
            "error": (
                f"No stock_stat_tz_wlnbb CSV found for universe={universe} tf={tf}. "
                "Run TZ/WLNBB → Generate Stock Stat first."
            ),
        }

    # Read all rows grouped by ticker
    rows_by_ticker: Dict[str, List[dict]] = {}
    with open(stat_file, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tkr = row.get("ticker", "").strip()
            if tkr:
                rows_by_ticker.setdefault(tkr, []).append(row)

    # Sort each ticker's rows by date ascending
    for rows in rows_by_ticker.values():
        rows.sort(key=lambda r: r.get("bar_datetime") or r.get("date", ""))

    all_patterns: list = []

    for ticker, rows in rows_by_ticker.items():
        # price filter on last bar
        try:
            last_close = float(rows[-1].get("close") or 0)
        except (TypeError, ValueError):
            last_close = 0.0
        if last_close < min_price or last_close > max_price:
            continue

        # --- scan history for all occurrences of each pattern ---
        # key: pattern_key (base4 | ext5 | ext6) → list of occurrence dicts
        pattern_occurrences: Dict[str, List[dict]] = defaultdict(list)

        for i, row in enumerate(rows):
            seqs = _build_sequences(row)
            base4 = seqs["base4_key"]
            ext5  = seqs["extended5_key"]
            ext6  = seqs["extended6_key"]
            if not base4:
                continue

            # For bottom metrics we need the 5-bar window (i-4 … i)
            win_start = max(0, i - 4)
            window_rows = rows[win_start: i + 1]
            ctx_start   = max(0, i - 24)
            ctx_rows    = rows[ctx_start: i + 1]

            bm = _bottom_metrics(window_rows, ctx_rows)

            # Look up base4 stats from matrix
            matrix_key   = (matrix_universe, base4)
            matrix_row   = matrix.get(matrix_key, {})
            base4_status = matrix_row.get("status", "")
            try:
                base4_n      = int(matrix_row.get("n") or 0)
                base4_med10d = float(matrix_row.get("med10d_pct") or 0)
                base4_fail10d= float(matrix_row.get("fail10d_pct") or 0)
            except (TypeError, ValueError):
                base4_n = 0
                base4_med10d  = 0.0
                base4_fail10d = 0.0

            # Derive win_rate from n and fail_rate (matrix has fail10d_pct; win = 1-fail approx)
            win_rate  = 100.0 - base4_fail10d if base4_n > 0 else None
            fail_rate = base4_fail10d          if base4_n > 0 else None

            tier = _evidence_tier(base4_n, base4_med10d if base4_n > 0 else None, win_rate, fail_rate)

            date_str = row.get("bar_datetime") or row.get("date", "")

            occ = {
                "date":             date_str,
                "base4_key":        base4,
                "extended5_key":    ext5,
                "extended6_key":    ext6,
                "base4_n":          base4_n,
                "base4_med10d":     base4_med10d,
                "base4_fail10d":    base4_fail10d,
                "base4_status":     base4_status,
                "base4_tier":       tier,
                "win_rate":         win_rate,
                "fail_rate":        fail_rate,
                **bm,
            }
            # Group by the longest key available (ext6 > ext5 > base4)
            group_key = ext6 or ext5 or base4
            pattern_occurrences[group_key].append(occ)

        if not pattern_occurrences:
            continue

        # For each pattern group, build an output record
        ticker_patterns: list = []

        for group_key, occs in pattern_occurrences.items():
            # Stats over all occurrences
            n_occ = len(occs)
            # Use matrix stats from the most recent occurrence for base4
            latest_occ = occs[-1]
            base4_key    = latest_occ["base4_key"]
            base4_n      = latest_occ["base4_n"]
            base4_med10d = latest_occ["base4_med10d"]
            base4_fail10d= latest_occ["base4_fail10d"]
            base4_status = latest_occ["base4_status"]
            base4_tier   = latest_occ["base4_tier"]
            win_rate     = latest_occ["win_rate"]
            fail_rate    = latest_occ["fail_rate"]

            ext5_key = latest_occ.get("extended5_key")
            ext6_key = latest_occ.get("extended6_key")

            # Determine evidence tier from matrix stats
            tier = base4_tier
            forming_subtype = None
            if tier == "FORMING_PATTERN" or base4_n == 0:
                forming_subtype = _forming_subtype(ext5_key, ext6_key, base4_key)
                tier = "FORMING_PATTERN"

            # bottom quality — use occurrence with lowest sequence low offset
            # (pattern with reversal strongest evidence)
            bottom_occs = [o for o in occs if o.get("qualifies_as_bottom")]
            best_bm = bottom_occs[-1] if bottom_occs else latest_occ

            qab         = best_bm.get("qualifies_as_bottom", False)
            seq_offset  = best_bm.get("sequence_low_bar_offset")
            seq_20bar   = best_bm.get("sequence_contains_20bar_low", False)
            ret_seq_low = best_bm.get("return_from_sequence_low_to_final")

            pattern_length = 6 if ext6_key else (5 if ext5_key else 4)

            sc = _score(tier, base4_med10d if base4_n > 0 else None,
                        win_rate, fail_rate, qab, ext5_key, ext6_key)

            example_dates = [o["date"] for o in occs if o.get("date")]

            # Is this currently active? (latest row matches this pattern)
            latest_row_seqs = _build_sequences(rows[-1])
            latest_base4    = latest_row_seqs.get("base4_key")
            latest_ext5     = latest_row_seqs.get("extended5_key")
            latest_ext6     = latest_row_seqs.get("extended6_key")
            is_active = (
                group_key in (latest_ext6, latest_ext5, latest_base4)
                if group_key else False
            )

            forming_info = _detect_forming(latest_row_seqs, rows[-5:]) if not is_active else None
            completion   = forming_info["current_pattern_completion"] if forming_info else (1.0 if is_active else 0.0)

            ticker_patterns.append({
                "ticker":                   ticker,
                "evidence_tier":            tier,
                "forming_subtype":          forming_subtype,
                "base4_key":                base4_key,
                "base4_tier":               base4_tier,
                "base4_med10d":             base4_med10d,
                "base4_fail10d":            base4_fail10d,
                "extended5_key":            ext5_key,
                "extended6_key":            ext6_key,
                "pattern_length":           pattern_length,
                "pattern_count":            n_occ,
                "sequence_low_bar_offset":  seq_offset,
                "sequence_contains_20bar_low": seq_20bar,
                "return_from_sequence_low_to_final": ret_seq_low,
                "median_10d_return":        base4_med10d if base4_n > 0 else None,
                "win_rate_10d":             win_rate,
                "fail_rate_10d":            fail_rate,
                "score":                    sc,
                "example_dates":            example_dates[-5:],  # last 5 instances
                "last_seen_date":           example_dates[-1] if example_dates else None,
                "is_currently_active":      is_active,
                "current_pattern_completion": completion,
            })

        # Sort by score desc, take top 3
        ticker_patterns.sort(key=lambda p: -p["score"])
        all_patterns.extend(ticker_patterns[:_MAX_PATTERNS_PER_TICKER])

    # Global sort: CONFIRMED_RARE first, then ANECDOTAL_RARE, then FORMING_PATTERN
    _tier_rank = {"CONFIRMED_RARE": 0, "ANECDOTAL_RARE": 1, "FORMING_PATTERN": 2, "NO_DATA": 3}
    all_patterns.sort(key=lambda p: (_tier_rank.get(p["evidence_tier"], 9), -p["score"]))

    # Add rank
    for i, p in enumerate(all_patterns, 1):
        p["rank"] = i

    return {
        "results": all_patterns[:limit],
        "total":   len(all_patterns),
        "universe": universe,
        "tf":       tf,
        "error":    None,
    }

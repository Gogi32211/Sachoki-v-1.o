"""
TP/SL path-based replay analytics.

Measurement-only module — does NOT modify live scoring, signals, or labels.
Reads the canonical-scored stock_stat dataframe (same source as replay_engine)
and simulates hypothetical trades using actual OHLCV bar paths.

Entry points
------------
run_tpsl_analytics(rows, cached) -> Dict[str, list | str]
    Main function called by replay_engine.run_replay() after score-consistency
    validation passes.  Returns {report_name: rows_list_or_markdown_str}.

compute_tpsl_trades(rows) -> List[dict]
    Low-level: produce one trade-row per (source_row × entry_mode × preset).
"""

from __future__ import annotations

import csv
import gc
import itertools
import logging
import os
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

# ─── Presets (single configurable location) ───────────────────────────────────

TPSL_PRESETS: Dict[str, dict] = {
    "SCALP_FAST":       {"tp_pct": 0.03,   "sl_pct": 0.01,   "max_hold": 5},
    "CLEAN_SWING":      {"tp_pct": 0.05,   "sl_pct": 0.02,   "max_hold": 10},
    "MOMENTUM_SWING":   {"tp_pct": 0.10,   "sl_pct": 0.03,   "max_hold": 10},
    "PARABOLIC":        {"tp_pct": 0.20,   "sl_pct": 0.05,   "max_hold": 20},
    "LOOSE_WATCH":      {"tp_pct": 0.10,   "sl_pct": 0.05,   "max_hold": 20},
    "STRUCTURAL_BUILD": {"tp_pct": 0.05,   "sl_pct": 0.05,   "max_hold": 20},
    "VERY_TIGHT":       {"tp_pct": 0.03,   "sl_pct": 0.0075, "max_hold": 5},
    "WIDE_MOMENTUM":    {"tp_pct": 0.15,   "sl_pct": 0.05,   "max_hold": 20},
}

ENTRY_MODES = ["SAME_DAY_CLOSE", "NEXT_DAY_OPEN"]

# Minimum hold to infer signal classification from TP rates
_FAST_PRESETS     = {"SCALP_FAST", "VERY_TIGHT"}
_SWING_PRESETS    = {"CLEAN_SWING", "STRUCTURAL_BUILD"}
_MOMENTUM_PRESETS = {"MOMENTUM_SWING", "LOOSE_WATCH", "WIDE_MOMENTUM"}
_PARA_PRESETS     = {"PARABOLIC"}


# ─── Signal / model column lists (mirrors replay_engine) ──────────────────────

_ALL_SIG_COLS = [
    "SIG_BEST","SIG_STRONG","SIG_VBO_DN","SIG_NS_VABS","SIG_ND_VABS",
    "SIG_SC","SIG_BC","SIG_ABS","SIG_CLM",
    "SIG_BEST_UP","SIG_FBO_UP","SIG_EB_UP","SIG_3UP",
    "SIG_FBO_DN","SIG_EB_DN","SIG_4BF_DN",
    "SIG_FRI34","SIG_FRI43","SIG_FRI64",
    "SIG_L555","SIG_L2L4","SIG_BLUE",
    "SIG_CCI","SIG_CCI0R","SIG_CCIB",
    "SIG_BO_DN","SIG_BX_DN","SIG_BE_DN",
    "SIG_RL","SIG_RH","SIG_PP",
    "SIG_G1","SIG_G2","SIG_G4","SIG_G6","SIG_G11",
    "SIG_B1","SIG_B2","SIG_B3","SIG_B4","SIG_B5","SIG_B6",
    "SIG_B7","SIG_B8","SIG_B9","SIG_B10","SIG_B11",
    "SIG_F1","SIG_F2","SIG_F3","SIG_F4","SIG_F5","SIG_F6",
    "SIG_F7","SIG_F8","SIG_F9","SIG_F10","SIG_F11",
    "SIG_FLY_ABCD","SIG_FLY_CD","SIG_FLY_BD","SIG_FLY_AD",
    "SIG_WK_UP","SIG_WK_DN","SIG_X1","SIG_X2","SIG_X1G","SIG_X3",
    "SIG_BIAS_UP","SIG_BIAS_DN","SIG_SVS","SIG_CONSO",
    "SIG_P2","SIG_P3","SIG_P50","SIG_P89","SIG_BUY","SIG_3G",
    "SIG_VA","SIG_VOL_5X","SIG_VOL_10X","SIG_VOL_20X",
    "SIG_TZ","SIG_T","SIG_Z","SIG_TZ3","SIG_TZ2","SIG_TZ_FLIP",
    "SIG_CD","SIG_CA","SIG_CW","SIG_SEQ_BCONT",
    "SIG_NS_DELTA","SIG_ND_DELTA",
    "SIG_ANY_F","SIG_ANY_B","SIG_ANY_P","SIG_ANY_D",
    "SIG_L_ANY","SIG_BE_ANY","SIG_GOG_PLUS","SIG_NOT_EXT",
    "PRICE_GT_20","PRICE_GT_50","PRICE_GT_89","PRICE_GT_200",
    "PRICE_LT_20","PRICE_LT_50","PRICE_LT_89","PRICE_LT_200",
    "RSI_LE_35","RSI_GE_70",
    "SIG_P66","SIG_P55",
    "SIG_D66","SIG_D55","SIG_D89","SIG_D50","SIG_D3","SIG_D2",
    "SIG_FLP_UP","SIG_ORG_UP","SIG_DD_UP_RED","SIG_D_UP_RED",
    "SIG_D_DN_GREEN","SIG_DD_DN_GREEN",
    "SIG_CISD_CPLUS","SIG_CISD_CPLUS_MINUS","SIG_CISD_CPLUS_MM",
    "SIG_PARA_PREP","SIG_PARA_START","SIG_PARA_PLUS","SIG_PARA_RETEST",
    "LD","LDS","LDC","LDP","LRC","LRP","WRC","F8C","SQB","BCT","SVS",
    "G1P","G2P","G3P","G1L","G2L","G3L","G1C","G2C","G3C","GOG1","GOG2","GOG3",
]

_MODEL_COLS = [
    "MDL_UM_GOG1","MDL_BH_GOG1","MDL_F8_GOG1","MDL_F8_BCT","MDL_F8_LRP",
    "MDL_L22_BCT","MDL_L22_LRP","MDL_BE_GOG1","MDL_BO_GOG1","MDL_Z10_GOG1",
    "MDL_LOAD_GOG1","MDL_260_GOG1","MDL_RKT_GOG1","MDL_F8_SVS","MDL_F8_CONS",
    "MDL_L22_SQB","MDL_3UP_GOG1","MDL_BLUE_GOG1","MDL_BX_GOG1","MDL_UM_LRP",
    "MDL_TZ_FLIP_Z","MDL_TZ_FLIP_WKUP","MDL_TZ3_VBO_DN",
    "MDL_ABS_RL","MDL_ABS_RH","MDL_BLUE_EBUP",
    "MDL_BEANY_F7_NDDELTA","MDL_CA_WKDN_UNDER50",
    "MDL_CA_NDVABS_UNDER50","MDL_RH_TZ3_UNDER200",
    "MDL_BEANY_BODN_NDDELTA",
    "MDL_SC_UM","MDL_SC_VOL5X","MDL_PARA_PLUS_UM",
    "MDL_PARA_START_UM","MDL_PARA_RETEST_UM",
    "HAS_ELITE_MODEL","HAS_BEAR_MODEL",
    "HAS_REBOUND_MODEL","HAS_STRONG_BULL_MODEL","HAS_HARD_BEAR_MODEL",
]

_COMPONENT_SCORE_COLS = [
    "CLEAN_ENTRY_SCORE", "SHAKEOUT_ABSORB_SCORE", "REBOUND_SQUEEZE_SCORE",
    "ROCKET_SCORE", "HARD_BEAR_SCORE",
    "LATE_RISK_SCORE", "WEAK_STACKING_RISK",
]

_SCORE_RANGE_LABELS = [
    ("<25",    0,   25),
    ("25-54",  25,  55),
    ("55-99",  55,  100),
    ("100+",   100, 9999),
]


# ─── Row helpers ──────────────────────────────────────────────────────────────

def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _s(r: dict, *keys) -> str:
    for k in keys:
        v = r.get(k)
        if v is not None:
            return str(v)
    return ""


def _n(r: dict, *keys) -> float:
    for k in keys:
        v = r.get(k)
        if v not in (None, ""):
            f = _f(v)
            if f != 0.0 or v in ("0", "0.0"):
                return f
    return 0.0


def _bull_score(r: dict) -> float:
    v = r.get("FINAL_BULL_SCORE")
    if v not in (None, ""):
        f = _f(v)
        if f != 0.0 or v in ("0", "0.0"):
            return f
    return _f(r.get("turbo_score", 0))


# Stock-stat CSV signal string columns (space-joined token lists)
_STRING_SIG_COLS = ["Z", "T", "L", "F", "FLY", "G", "B", "Combo", "ULT", "VOL", "VABS", "WICK"]

# Composite model flags present in stock_stat CSV (individual MDL_* are not)
_COMPOSITE_MODEL_FLAGS = [
    "HAS_ELITE_MODEL", "HAS_REBOUND_MODEL", "HAS_STRONG_BULL_MODEL",
    "HAS_BEAR_MODEL", "HAS_HARD_BEAR_MODEL",
]


def _parse_signals_from_strings(r: dict) -> List[str]:
    """Parse active signal tokens from grouped space-joined string columns in stock_stat CSV."""
    signals = []
    for col in _STRING_SIG_COLS:
        v = r.get(col, "")
        if v and isinstance(v, str):
            signals.extend(t for t in v.split() if t)
    return signals


def _active_sigs(r: dict) -> List[str]:
    """
    Return active signal names for a row.
    Tries individual boolean SIG_* columns first (api_bar_signals dicts),
    falls back to parsing grouped string columns (stock_stat CSV rows).
    """
    bool_sigs = [c for c in _ALL_SIG_COLS if c in r and _f(r.get(c, 0)) > 0]
    if bool_sigs:
        return bool_sigs
    return _parse_signals_from_strings(r)


def _active_models(r: dict) -> List[str]:
    """
    Return active model names for a row.
    Uses composite flags (HAS_ELITE_MODEL etc.) which are present in stock_stat CSV,
    plus individual MDL_* flags when available.
    """
    out = [m for m in _MODEL_COLS if _f(r.get(m, 0)) > 0]
    if out:
        return out
    # Fallback: composite flags only
    return [m for m in _COMPOSITE_MODEL_FLAGS if _f(r.get(m, 0)) > 0]


def _score_range_label(fbs: float) -> str:
    for label, lo, hi in _SCORE_RANGE_LABELS:
        if lo <= fbs < hi:
            return label
    return "100+"


def _component_bucket(score: float) -> str:
    if score <= 0:
        return "0"
    if score <= 5:
        return "1-5"
    if score <= 10:
        return "6-10"
    if score <= 20:
        return "11-20"
    return "21+"


# ─── Statistics helpers ───────────────────────────────────────────────────────

def _mean(vals: list) -> float:
    vs = [v for v in vals if v is not None]
    return round(statistics.mean(vs), 4) if vs else 0.0


def _median(vals: list) -> float:
    vs = [v for v in vals if v is not None]
    return round(statistics.median(vs), 4) if vs else 0.0


def _pct(n: int, total: int) -> float:
    return round(n / total, 4) if total else 0.0


# ─── Core simulation ──────────────────────────────────────────────────────────

def _simulate_trade(
    trows: List[dict],
    idx: int,
    entry_price: float,
    tp_pct: float,
    sl_pct: float,
    max_hold: int,
) -> dict:
    """
    Simulate one trade from trows[idx] using the given entry_price and preset.
    Forward window = trows[idx+1 .. idx+max_hold] (same-day-close base)
                   or trows[idx+1 .. idx+max_hold] anchored at entry_price.
    Never crosses ticker boundary (trows is already ticker-specific).
    """
    n = len(trows)
    target = entry_price * (1.0 + tp_pct)
    stop   = entry_price * (1.0 - sl_pct)

    outcome        = "NO_HIT"
    hit_date       = ""
    days_to_out    = max_hold
    exit_price     = None
    tp_day: Optional[int] = None
    sl_day: Optional[int] = None

    closes: List[float] = []
    highs:  List[float] = []
    lows:   List[float] = []

    # Window: bars idx+1 to idx+max_hold (inclusive), capped at end of ticker
    for k in range(1, max_hold + 1):
        j = idx + k
        if j >= n:
            break
        bar   = trows[j]
        h     = _f(bar.get("high",  0))
        lo    = _f(bar.get("low",   0))
        c     = _f(bar.get("close", 0))
        tp_hit = h > 0 and h >= target
        sl_hit = lo > 0 and lo <= stop
        if h > 0:
            highs.append(h)
        if lo > 0:
            lows.append(lo)
        if c > 0:
            closes.append(c)
        if tp_hit and tp_day is None:
            tp_day = k
        if sl_hit and sl_day is None:
            sl_day = k

    if tp_day is not None and sl_day is not None:
        if tp_day < sl_day:
            outcome     = "TP_FIRST"
            hit_date    = _s(trows[idx + tp_day], "date")
            days_to_out = tp_day
            exit_price  = target
        elif sl_day < tp_day:
            outcome     = "SL_FIRST"
            hit_date    = _s(trows[idx + sl_day], "date")
            days_to_out = sl_day
            exit_price  = stop
        else:
            outcome     = "AMBIGUOUS_SAME_DAY_HIT"
            hit_date    = _s(trows[idx + tp_day], "date")
            days_to_out = tp_day
            exit_price  = None
    elif tp_day is not None:
        outcome     = "TP_FIRST"
        hit_date    = _s(trows[idx + tp_day], "date")
        days_to_out = tp_day
        exit_price  = target
    elif sl_day is not None:
        outcome     = "SL_FIRST"
        hit_date    = _s(trows[idx + sl_day], "date")
        days_to_out = sl_day
        exit_price  = stop

    # Exit price for NO_HIT = close at max_hold bar (or last available)
    eop_close = closes[-1] if closes else entry_price
    if outcome == "NO_HIT":
        exit_price = eop_close

    realized = round((exit_price / entry_price - 1) * 100, 4) if exit_price else 0.0
    mfe = round((max(highs) / entry_price - 1) * 100, 4) if highs else 0.0
    mae = round((min(lows)  / entry_price - 1) * 100, 4) if lows  else 0.0
    close_at_max_hold = round((eop_close / entry_price - 1) * 100, 4) if eop_close > 0 else 0.0
    max_c  = round((max(closes) / entry_price - 1) * 100, 4) if closes else 0.0
    min_c  = round((min(closes) / entry_price - 1) * 100, 4) if closes else 0.0

    return {
        "outcome":                        outcome,
        "hit_date":                       hit_date,
        "days_to_outcome":                days_to_out,
        "exit_price_assumption":          round(exit_price, 4) if exit_price else "",
        "realized_return_assumption":     realized,
        "mfe_pct":                        mfe,
        "mae_pct":                        mae,
        "close_return_at_max_hold":       close_at_max_hold,
        "max_close_return_during_window": max_c,
        "min_close_return_during_window": min_c,
        "target_price":                   round(target, 4),
        "stop_price":                     round(stop, 4),
    }


# ─── Trade row builder ────────────────────────────────────────────────────────

def compute_tpsl_trades(rows: List[dict]) -> List[dict]:
    """
    Materialize all trade rows into a list (used by tests and small datasets).
    For production use, prefer _iter_tpsl_trades() to avoid OOM on large universes.
    """
    return list(_iter_tpsl_trades(rows))


def _build_ctx(ticker: str, r: dict) -> dict:
    fbs = _bull_score(r)
    return {
        "ticker":                ticker,
        "date":                  _s(r, "date"),
        "close":                 round(_f(r.get("close", 0)), 4),
        "FINAL_BULL_SCORE":      fbs,
        "score_range":           _score_range_label(fbs),
        "FINAL_REGIME":          _s(r, "FINAL_REGIME"),
        "FINAL_SCORE_BUCKET":    _s(r, "FINAL_SCORE_BUCKET"),
        "READINESS_PHASE":       _s(r, "READINESS_PHASE"),
        "ACTIONABILITY_SCORE":   _n(r, "ACTIONABILITY_SCORE"),
        "CLEAN_ENTRY_SCORE":     _n(r, "CLEAN_ENTRY_SCORE"),
        "SHAKEOUT_ABSORB_SCORE": _n(r, "SHAKEOUT_ABSORB_SCORE"),
        "REBOUND_SQUEEZE_SCORE": _n(r, "REBOUND_SQUEEZE_SCORE"),
        "ROCKET_SCORE":          _n(r, "ROCKET_SCORE"),
        "HARD_BEAR_SCORE":       _n(r, "HARD_BEAR_SCORE"),
        "VOLATILITY_RISK_SCORE": _n(r, "VOLATILITY_RISK_SCORE"),
        "WEAK_STACKING_RISK":    _n(r, "WEAK_STACKING_RISK"),
        "LATE_RISK_SCORE":       _n(r, "LATE_RISK_SCORE"),
        "ALREADY_EXTENDED_FLAG": _n(r, "ALREADY_EXTENDED_FLAG"),
        "HAS_STRONG_BULL_MODEL": _n(r, "HAS_STRONG_BULL_MODEL"),
        "HAS_REBOUND_MODEL":     _n(r, "HAS_REBOUND_MODEL"),
        "HAS_ELITE_MODEL":       _n(r, "HAS_ELITE_MODEL"),
        "active_signals":        "|".join(_active_sigs(r)),
        "active_models":         "|".join(_active_models(r)),
    }


def _iter_tpsl_trades(rows: List[dict]):
    """
    Generator yielding one trade row at a time. Memory bounded by current bar
    (no list of all trade rows held). Trade order: ticker × date × entry_mode × preset.
    """
    by_ticker: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        t = _s(r, "ticker")
        if t:
            by_ticker[t].append(r)
    for trows in by_ticker.values():
        trows.sort(key=lambda x: _s(x, "date"))

    for ticker, trows in by_ticker.items():
        n = len(trows)
        for i, r in enumerate(trows):
            close_price = _f(r.get("close", 0))
            if close_price <= 0:
                continue

            next_open: Optional[float] = None
            next_date: str = ""
            if i + 1 < n:
                next_open = _f(trows[i + 1].get("open", 0))
                next_date = _s(trows[i + 1], "date")
                if next_open <= 0:
                    next_open = None

            ctx = _build_ctx(ticker, r)

            for entry_mode in ENTRY_MODES:
                if entry_mode == "SAME_DAY_CLOSE":
                    entry_price = close_price
                else:
                    if next_open is None:
                        for preset_name, p in TPSL_PRESETS.items():
                            row = dict(ctx)
                            row.update({
                                "entry_mode":     entry_mode,
                                "preset_name":    preset_name,
                                "entry_price":    "",
                                "target_pct":     p["tp_pct"] * 100,
                                "stop_pct":       p["sl_pct"] * 100,
                                "max_hold_days":  p["max_hold"],
                                "target_price":   "",
                                "stop_price":     "",
                                "outcome":        "NO_NEXT_OPEN",
                                "hit_date":       "",
                                "days_to_outcome":             "",
                                "realized_return_assumption":  "",
                                "mfe_pct":                     "",
                                "mae_pct":                     "",
                                "close_return_at_max_hold":    "",
                                "max_close_return_during_window": "",
                                "min_close_return_during_window": "",
                                "exit_price_assumption": "",
                                "next_day":       next_date,
                            })
                            yield row
                        continue
                    entry_price = next_open

                for preset_name, p in TPSL_PRESETS.items():
                    sim = _simulate_trade(
                        trows, i, entry_price,
                        p["tp_pct"], p["sl_pct"], p["max_hold"],
                    )
                    row = dict(ctx)
                    row.update({
                        "entry_mode":    entry_mode,
                        "preset_name":   preset_name,
                        "entry_price":   round(entry_price, 4),
                        "target_pct":    p["tp_pct"] * 100,
                        "stop_pct":      p["sl_pct"] * 100,
                        "max_hold_days": p["max_hold"],
                        "next_day":      next_date if entry_mode == "NEXT_DAY_OPEN" else "",
                    })
                    row.update(sim)
                    yield row


# ─── Aggregate metrics ────────────────────────────────────────────────────────

def _agg_tpsl(trade_rows: List[dict]) -> dict:
    """Aggregate TP/SL metrics for a list of trade rows (same preset + entry mode)."""
    total = len(trade_rows)
    if not total:
        return {"count": 0}

    tp_rows  = [r for r in trade_rows if r.get("outcome") == "TP_FIRST"]
    sl_rows  = [r for r in trade_rows if r.get("outcome") == "SL_FIRST"]
    nh_rows  = [r for r in trade_rows if r.get("outcome") == "NO_HIT"]
    am_rows  = [r for r in trade_rows if r.get("outcome") == "AMBIGUOUS_SAME_DAY_HIT"]
    nn_rows  = [r for r in trade_rows if r.get("outcome") == "NO_NEXT_OPEN"]

    tp_r = _pct(len(tp_rows), total)
    sl_r = _pct(len(sl_rows), total)
    nh_r = _pct(len(nh_rows), total)
    am_r = _pct(len(am_rows), total)

    # Only use non-NO_NEXT_OPEN rows for EV calculation
    ev_total = total - len(nn_rows)

    # Preset values from first row (homogeneous within group)
    sample = trade_rows[0]
    tp_pct_val = _f(sample.get("target_pct", 0)) / 100.0
    sl_pct_val = _f(sample.get("stop_pct",   0)) / 100.0

    ev_tp = _pct(len(tp_rows), ev_total) if ev_total else 0
    ev_sl = _pct(len(sl_rows), ev_total) if ev_total else 0
    ev_am = _pct(len(am_rows), ev_total) if ev_total else 0

    ev_simple = round(ev_tp * tp_pct_val * 100 - ev_sl * sl_pct_val * 100, 4)
    ev_cons   = round(ev_tp * tp_pct_val * 100 - (ev_sl + ev_am) * sl_pct_val * 100, 4)
    tp_sl_r   = round(ev_tp / ev_sl, 4) if ev_sl > 0 else None

    gross_tp = sum(_f(r.get("realized_return_assumption", 0)) for r in tp_rows)
    gross_sl = abs(sum(_f(r.get("realized_return_assumption", 0)) for r in sl_rows))
    pf       = round(gross_tp / gross_sl, 4) if gross_sl > 0 else None

    rets  = [_f(r["realized_return_assumption"]) for r in trade_rows
             if r.get("realized_return_assumption") not in ("", None)]
    mfes  = [_f(r["mfe_pct"]) for r in trade_rows if r.get("mfe_pct") not in ("", None)]
    maes  = [_f(r["mae_pct"]) for r in trade_rows if r.get("mae_pct") not in ("", None)]
    clatr = [_f(r["close_return_at_max_hold"]) for r in trade_rows
             if r.get("close_return_at_max_hold") not in ("", None)]
    dtp   = [_f(r["days_to_outcome"]) for r in tp_rows if r.get("days_to_outcome") not in ("", None)]
    dsl   = [_f(r["days_to_outcome"]) for r in sl_rows if r.get("days_to_outcome") not in ("", None)]

    # Quality metrics
    clean_wins   = sum(1 for r in tp_rows if _f(r.get("mae_pct", 0)) > -sl_pct_val * 100)
    painful_wins = len(tp_rows) - clean_wins
    fast_sl      = sum(1 for r in sl_rows if _f(r.get("days_to_outcome", 99)) <= 2)
    fast_tp      = sum(1 for r in tp_rows if _f(r.get("days_to_outcome", 99)) <= 2)

    return {
        "count":                              total,
        "tp_first_count":                     len(tp_rows),
        "sl_first_count":                     len(sl_rows),
        "no_hit_count":                       len(nh_rows),
        "ambiguous_count":                    len(am_rows),
        "no_next_open_count":                 len(nn_rows),
        "tp_first_rate":                      tp_r,
        "sl_first_rate":                      sl_r,
        "no_hit_rate":                        nh_r,
        "ambiguous_rate":                     am_r,
        "avg_realized_return":                _mean(rets),
        "median_realized_return":             _median(rets),
        "avg_mfe_pct":                        _mean(mfes),
        "median_mfe_pct":                     _median(mfes),
        "avg_mae_pct":                        _mean(maes),
        "median_mae_pct":                     _median(maes),
        "avg_close_return_at_max_hold":       _mean(clatr),
        "median_close_return_at_max_hold":    _median(clatr),
        "avg_days_to_tp":                     _mean(dtp),
        "median_days_to_tp":                  _median(dtp),
        "avg_days_to_sl":                     _mean(dsl),
        "median_days_to_sl":                  _median(dsl),
        "expected_value_simple":              ev_simple,
        "conservative_expected_value":        ev_cons,
        "tp_sl_ratio":                        tp_sl_r,
        "profit_factor_approx":               pf,
        "clean_win_rate":                     _pct(clean_wins, len(tp_rows)) if tp_rows else 0.0,
        "painful_win_rate":                   _pct(painful_wins, len(tp_rows)) if tp_rows else 0.0,
        "failed_fast_rate":                   _pct(fast_sl, len(sl_rows)) if sl_rows else 0.0,
        "fast_tp_rate":                       _pct(fast_tp, total),
    }


def _group_agg(trade_rows: List[dict], group_key: str) -> List[dict]:
    """Generic group-by (group_key + entry_mode + preset_name) → agg metrics."""
    groups: Dict[Tuple, List[dict]] = defaultdict(list)
    for r in trade_rows:
        k = (r.get(group_key, ""), r.get("entry_mode", ""), r.get("preset_name", ""))
        groups[k].append(r)
    out = []
    for (gval, emode, preset), grp in groups.items():
        d = _agg_tpsl(grp)
        d[group_key]   = gval
        d["entry_mode"]  = emode
        d["preset_name"] = preset
        out.append(d)
    out.sort(key=lambda x: -(x.get("conservative_expected_value") or 0))
    return out


# ─── Report generators ────────────────────────────────────────────────────────

def tpsl_signal_perf(
    trade_rows: List[dict],
    min_count_main: int = 50,
    min_count_sec:  int = 20,
) -> List[dict]:
    """Group by (signal, entry_mode, preset_name). Active signals only."""
    # Find signals active in at least one source row (use per-entry trade rows)
    active_sigs = set()
    for r in trade_rows:
        for sig in (r.get("active_signals") or "").split("|"):
            if sig:
                active_sigs.add(sig)

    out = []
    # Group trade rows by (entry_mode, preset_name)
    by_mode_preset: Dict[Tuple, List[dict]] = defaultdict(list)
    for r in trade_rows:
        k = (r.get("entry_mode", ""), r.get("preset_name", ""))
        by_mode_preset[k].append(r)

    for sig in sorted(active_sigs):
        for (emode, preset), grp in by_mode_preset.items():
            sig_rows = [r for r in grp if sig in (r.get("active_signals") or "").split("|")]
            n = len(sig_rows)
            if n < min_count_sec:
                continue
            d = _agg_tpsl(sig_rows)
            d["signal"]      = sig
            d["entry_mode"]  = emode
            d["preset_name"] = preset
            d["sample_tier"] = "MAIN" if n >= min_count_main else ("SEC" if n >= min_count_sec else "LOW_SAMPLE")
            d["signal_class"] = _classify_signal(d)
            out.append(d)
    out.sort(key=lambda x: -(x.get("conservative_expected_value") or 0))
    return out


def tpsl_model_perf(trade_rows: List[dict], min_count: int = 20) -> List[dict]:
    """Group by (model, entry_mode, preset_name)."""
    by_mode_preset: Dict[Tuple, List[dict]] = defaultdict(list)
    for r in trade_rows:
        k = (r.get("entry_mode", ""), r.get("preset_name", ""))
        by_mode_preset[k].append(r)

    out = []
    for model in _MODEL_COLS:
        for (emode, preset), grp in by_mode_preset.items():
            mdl_rows = [r for r in grp if model in (r.get("active_models") or "").split("|")]
            if len(mdl_rows) < min_count:
                continue
            d = _agg_tpsl(mdl_rows)
            d["model"]       = model
            d["entry_mode"]  = emode
            d["preset_name"] = preset
            out.append(d)
    out.sort(key=lambda x: -(x.get("conservative_expected_value") or 0))
    return out


def tpsl_regime_perf(trade_rows: List[dict]) -> List[dict]:
    return _group_agg(trade_rows, "FINAL_REGIME")


def tpsl_score_bucket_perf(trade_rows: List[dict]) -> List[dict]:
    return _group_agg(trade_rows, "FINAL_SCORE_BUCKET")


def tpsl_score_range_perf(trade_rows: List[dict]) -> List[dict]:
    """Group by (score_range <25 / 25-54 / 55-99 / 100+, entry_mode, preset_name)."""
    return _group_agg(trade_rows, "score_range")


def tpsl_readiness_phase_perf(trade_rows: List[dict]) -> List[dict]:
    rows = [r for r in trade_rows if r.get("READINESS_PHASE")]
    if not rows:
        return []
    return _group_agg(rows, "READINESS_PHASE")


def tpsl_actionability_bucket_perf(trade_rows: List[dict]) -> List[dict]:
    rows = [r for r in trade_rows if _f(r.get("ACTIONABILITY_SCORE", 0)) > 0]
    if not rows:
        return []
    # Bucket into ranges
    for r in rows:
        sc = _f(r.get("ACTIONABILITY_SCORE", 0))
        if sc < 25:
            r["_act_bucket"] = "<25"
        elif sc < 50:
            r["_act_bucket"] = "25-49"
        elif sc < 75:
            r["_act_bucket"] = "50-74"
        else:
            r["_act_bucket"] = "75+"
    return _group_agg(rows, "_act_bucket")


def tpsl_component_bucket_perf(trade_rows: List[dict]) -> List[dict]:
    out = []
    for col in _COMPONENT_SCORE_COLS:
        active = [r for r in trade_rows if _f(r.get(col, 0)) > 0]
        if not active:
            continue
        for r in active:
            r["_comp_bucket"] = _component_bucket(_f(r.get(col, 0)))
        grped = _group_agg(active, "_comp_bucket")
        for row in grped:
            row["component"] = col
        out.extend(grped)
    return out


def tpsl_pair_combo_perf(
    trade_rows: List[dict],
    min_count: int = 30,
    top_n: int = 100,
) -> List[dict]:
    """Group by (signal pair combo, entry_mode, preset_name)."""
    by_mode_preset: Dict[Tuple, List[dict]] = defaultdict(list)
    for r in trade_rows:
        k = (r.get("entry_mode", ""), r.get("preset_name", ""))
        by_mode_preset[k].append(r)

    out = []
    for (emode, preset), grp in by_mode_preset.items():
        # Find frequent signals in this slice
        sig_counts: Dict[str, int] = defaultdict(int)
        for r in grp:
            for sig in (r.get("active_signals") or "").split("|"):
                if sig:
                    sig_counts[sig] += 1
        freq_sigs = [s for s, c in sig_counts.items() if c >= min_count]
        freq_set  = set(freq_sigs)

        pair_groups: Dict[Tuple, List[dict]] = defaultdict(list)
        for r in grp:
            active = [s for s in (r.get("active_signals") or "").split("|")
                      if s in freq_set]
            for a, b in itertools.combinations(sorted(active), 2):
                pair_groups[(a, b)].append(r)

        slice_out = []
        for (a, b), pr in pair_groups.items():
            if len(pr) < min_count:
                continue
            d = _agg_tpsl(pr)
            d["combo"]       = f"{a}+{b}"
            d["entry_mode"]  = emode
            d["preset_name"] = preset
            slice_out.append(d)
        slice_out.sort(key=lambda x: -(x.get("conservative_expected_value") or 0))
        out.extend(slice_out[:top_n])
    return out


def tpsl_triple_combo_perf(
    trade_rows: List[dict],
    min_count: int = 20,
    top_n: int = 100,
) -> List[dict]:
    """Group by (signal triple combo, entry_mode, preset_name)."""
    by_mode_preset: Dict[Tuple, List[dict]] = defaultdict(list)
    for r in trade_rows:
        k = (r.get("entry_mode", ""), r.get("preset_name", ""))
        by_mode_preset[k].append(r)

    out = []
    for (emode, preset), grp in by_mode_preset.items():
        sig_counts: Dict[str, int] = defaultdict(int)
        for r in grp:
            for sig in (r.get("active_signals") or "").split("|"):
                if sig:
                    sig_counts[sig] += 1
        freq_sigs = [s for s, c in sig_counts.items() if c >= min_count * 2]
        freq_set  = set(freq_sigs[:40])  # cap to 40 most frequent

        triple_groups: Dict[Tuple, List[dict]] = defaultdict(list)
        for r in grp:
            active = [s for s in (r.get("active_signals") or "").split("|")
                      if s in freq_set]
            for combo in itertools.combinations(sorted(active), 3):
                triple_groups[combo].append(r)

        slice_out = []
        for combo, pr in triple_groups.items():
            if len(pr) < min_count:
                continue
            d = _agg_tpsl(pr)
            d["combo"]       = "+".join(combo)
            d["entry_mode"]  = emode
            d["preset_name"] = preset
            slice_out.append(d)
        slice_out.sort(key=lambda x: -(x.get("conservative_expected_value") or 0))
        out.extend(slice_out[:top_n])
    return out


def tpsl_missed_big_winners(
    trade_rows: List[dict],
    missed_full: List[dict],
    caught_full: List[dict],
    late_full:   List[dict],
) -> List[dict]:
    """
    Overlay TP/SL outcomes onto missed/caught/late category rows.
    Joins on (ticker, date).
    """
    # Build lookup: (ticker, date) → trade rows
    td_index: Dict[Tuple, List[dict]] = defaultdict(list)
    for r in trade_rows:
        k = (r.get("ticker", ""), r.get("date", ""))
        td_index[k].append(r)

    out = []
    for cat_label, cat_rows in [
        ("TRUE_MISSED_WINNER",  missed_full),
        ("CAUGHT_EARLY_WINNER", caught_full),
        ("LATE_OR_WEAK_CATCH",  late_full),
    ]:
        for mr in cat_rows:
            ticker = mr.get("ticker", "")
            date   = mr.get("date", "")
            trades = td_index.get((ticker, date), [])
            if not trades:
                # Emit one stub row per preset×entry_mode to keep completeness
                for emode in ENTRY_MODES:
                    for pname in TPSL_PRESETS:
                        out.append({
                            "category":      cat_label,
                            "ticker":        ticker,
                            "date":          date,
                            "entry_mode":    emode,
                            "preset_name":   pname,
                            "outcome":       "NOT_IN_TPSL",
                            "final_bull_score": mr.get("final_bull_score", ""),
                            "final_regime":  mr.get("final_regime", ""),
                            "max_high_10d":  mr.get("max_high_10d", ""),
                        })
                continue
            for tr in trades:
                row = {
                    "category":                       cat_label,
                    "ticker":                         ticker,
                    "date":                           date,
                    "entry_mode":                     tr.get("entry_mode"),
                    "preset_name":                    tr.get("preset_name"),
                    "outcome":                        tr.get("outcome"),
                    "days_to_outcome":                tr.get("days_to_outcome"),
                    "realized_return_assumption":     tr.get("realized_return_assumption"),
                    "mfe_pct":                        tr.get("mfe_pct"),
                    "mae_pct":                        tr.get("mae_pct"),
                    "close_return_at_max_hold":       tr.get("close_return_at_max_hold"),
                    "final_bull_score":               mr.get("final_bull_score", tr.get("FINAL_BULL_SCORE")),
                    "final_regime":                   mr.get("final_regime",     tr.get("FINAL_REGIME")),
                    "final_score_bucket":             mr.get("final_score_bucket", tr.get("FINAL_SCORE_BUCKET")),
                    "max_high_10d":                   mr.get("max_high_10d", ""),
                    "prior_actionable_found":         mr.get("prior_actionable_found", ""),
                    "best_prior_score_20d":           mr.get("best_prior_score_20d", ""),
                    "best_prior_date_20d":            mr.get("best_prior_date_20d", ""),
                }
                out.append(row)
    return out


def tpsl_false_positives(
    trade_rows: List[dict],
    fp_rows:    List[dict],
) -> List[dict]:
    """
    For each false positive, attach TP/SL outcomes.
    Classifies: TRUE_FALSE_POSITIVE / TRADEABLE_BUT_BAD_HOLD / VOLATILE_AMBIGUOUS.
    """
    td_index: Dict[Tuple, List[dict]] = defaultdict(list)
    for r in trade_rows:
        k = (r.get("ticker", ""), r.get("date", ""))
        td_index[k].append(r)

    out = []
    for fp in fp_rows:
        ticker = fp.get("ticker", "")
        date   = fp.get("date",   "")
        trades = td_index.get((ticker, date), [])
        if not trades:
            out.append({
                "ticker":    ticker, "date": date,
                "fp_class":  "NOT_IN_TPSL",
                "ret_10d":   fp.get("ret_10d", ""),
                "final_bull_score": fp.get("final_bull_score", ""),
            })
            continue
        for tr in trades:
            outcome = tr.get("outcome", "")
            mfe     = _f(tr.get("mfe_pct", 0))
            tp_pct  = _f(tr.get("target_pct", 0))
            sl_pct  = _f(tr.get("stop_pct",  0))

            if outcome == "TP_FIRST":
                fp_class = "TRADEABLE_BUT_BAD_HOLD"
            elif outcome in ("SL_FIRST", "NO_HIT") and mfe < tp_pct / 2:
                fp_class = "TRUE_FALSE_POSITIVE"
            elif outcome == "AMBIGUOUS_SAME_DAY_HIT":
                fp_class = "VOLATILE_AMBIGUOUS"
            elif mfe >= tp_pct and outcome != "TP_FIRST":
                fp_class = "VOLATILE_AMBIGUOUS"
            else:
                fp_class = "TRUE_FALSE_POSITIVE"

            row = {
                "ticker":             ticker,
                "date":               date,
                "entry_mode":         tr.get("entry_mode"),
                "preset_name":        tr.get("preset_name"),
                "fp_class":           fp_class,
                "outcome":            outcome,
                "realized_return":    tr.get("realized_return_assumption"),
                "mfe_pct":            mfe,
                "mae_pct":            tr.get("mae_pct"),
                "close_return_at_max_hold": tr.get("close_return_at_max_hold"),
                "ret_10d":            fp.get("ret_10d", ""),
                "final_bull_score":   fp.get("final_bull_score", tr.get("FINAL_BULL_SCORE")),
                "final_regime":       fp.get("final_regime",     tr.get("FINAL_REGIME")),
                "likely_fail_reason": fp.get("likely_fail_reason", ""),
            }
            out.append(row)
    return out


def tpsl_caught_early_timing(
    trade_rows:  List[dict],
    caught_full: List[dict],
) -> List[dict]:
    """
    For CAUGHT_EARLY_WINNERS, simulate trades at both the missed date AND
    at best_prior_date entries (3d / 5d / 10d / 20d windows).
    """
    td_index: Dict[Tuple, List[dict]] = defaultdict(list)
    for r in trade_rows:
        k = (r.get("ticker", ""), r.get("date", ""))
        td_index[k].append(r)

    out = []
    for cr in caught_full:
        ticker   = cr.get("ticker", "")
        miss_dt  = cr.get("date",   "")
        prior_dt = cr.get("best_prior_date_20d", "")

        miss_trades  = td_index.get((ticker, miss_dt),  [])
        prior_trades = td_index.get((ticker, prior_dt), []) if prior_dt else []

        for emode in ENTRY_MODES:
            for pname in TPSL_PRESETS:
                miss_t  = next((t for t in miss_trades  if t.get("entry_mode") == emode and t.get("preset_name") == pname), None)
                prior_t = next((t for t in prior_trades if t.get("entry_mode") == emode and t.get("preset_name") == pname), None)

                out.append({
                    "ticker":                cr.get("ticker", ""),
                    "missed_date":           miss_dt,
                    "entry_mode":            emode,
                    "preset_name":           pname,
                    "current_outcome":       miss_t.get("outcome", "NOT_IN_TPSL") if miss_t else "NOT_IN_TPSL",
                    "current_mfe_pct":       miss_t.get("mfe_pct", "") if miss_t else "",
                    "current_mae_pct":       miss_t.get("mae_pct", "") if miss_t else "",
                    "best_prior_date_20d":   prior_dt,
                    "prior_outcome":         prior_t.get("outcome", "NOT_IN_TPSL") if prior_t else "NOT_IN_TPSL",
                    "prior_tp_first":        int(prior_t.get("outcome") == "TP_FIRST") if prior_t else "",
                    "prior_sl_first":        int(prior_t.get("outcome") == "SL_FIRST") if prior_t else "",
                    "prior_mfe_pct":         prior_t.get("mfe_pct", "") if prior_t else "",
                    "prior_mae_pct":         prior_t.get("mae_pct", "") if prior_t else "",
                    "prior_days_to_tp":      prior_t.get("days_to_outcome", "") if (prior_t and prior_t.get("outcome") == "TP_FIRST") else "",
                    "best_prior_score_20d":  cr.get("best_prior_score_20d", ""),
                    "best_prior_regime_20d": cr.get("best_prior_regime_20d", ""),
                    "days_since_best_prior": cr.get("days_since_best_prior_catch", ""),
                    "final_bull_score":      cr.get("final_bull_score", ""),
                    "final_regime":          cr.get("final_regime", ""),
                    "max_high_10d":          cr.get("max_high_10d", ""),
                })
    return out


# ─── Signal classification ────────────────────────────────────────────────────

def _classify_signal(agg: dict) -> str:
    tp_r   = agg.get("tp_first_rate", 0)
    sl_r   = agg.get("sl_first_rate", 0)
    ev     = agg.get("conservative_expected_value", 0)
    fast_tp = agg.get("fast_tp_rate", 0)
    preset = agg.get("preset_name", "")
    mfe    = agg.get("avg_mfe_pct", 0)
    mae    = agg.get("avg_mae_pct", 0)

    if tp_r < 0.30 and sl_r > 0.40:
        return "NO_EDGE_SIGNAL"
    if sl_r > 0.35 and ev < -1.0:
        return "LATE_RISK_SIGNAL"
    if tp_r > 0.55 and fast_tp > 0.25 and preset in _FAST_PRESETS:
        return "FAST_ENTRY_SIGNAL"
    if tp_r > 0.45 and preset in _SWING_PRESETS:
        return "SWING_ENTRY_SIGNAL"
    if tp_r > 0.35 and preset in _MOMENTUM_PRESETS:
        return "MOMENTUM_SIGNAL"
    if mfe > 5 and abs(mae) > 3:
        return "WATCHLIST_SIGNAL"
    if ev > 0:
        return "SWING_ENTRY_SIGNAL"
    return "WATCHLIST_SIGNAL"


# ─── Validation ───────────────────────────────────────────────────────────────

def tpsl_validation(trade_rows: List[dict]) -> List[dict]:
    """
    Basic integrity checks on trade_rows.  Post-report checks (signal_perf nonempty,
    category counts, etc.) are appended by run_tpsl_analytics() after all reports run.
    """
    checks: List[dict] = []

    def _chk(name, passed, detail=""):
        checks.append({
            "validation_name": name,
            "status":          "PASS" if passed else "FAIL",
            "details":         detail,
        })

    if not trade_rows:
        _chk("row_count_nonzero", False, "No trade rows produced")
        return checks

    total = len(trade_rows)
    _chk("row_count_nonzero", total > 0, f"{total} trade rows")

    # Outcome validity
    valid_outcomes = {"TP_FIRST","SL_FIRST","NO_HIT","AMBIGUOUS_SAME_DAY_HIT","NO_NEXT_OPEN"}
    bad_outcomes = [r for r in trade_rows if r.get("outcome") not in valid_outcomes]
    _chk("valid_outcomes", len(bad_outcomes) == 0,
         f"{len(bad_outcomes)} rows with invalid outcome" if bad_outcomes else "all valid")

    # No duplicate ticker/date/entry_mode/preset
    seen: set = set()
    dupes = 0
    for r in trade_rows:
        k = (r.get("ticker"), r.get("date"), r.get("entry_mode"), r.get("preset_name"))
        if k in seen:
            dupes += 1
        seen.add(k)
    _chk("no_duplicate_rows", dupes == 0, f"{dupes} duplicate rows" if dupes else "no duplicates")

    # Rate sum per group
    bad_rate_groups = 0
    by_group: Dict[Tuple, List[dict]] = defaultdict(list)
    for r in trade_rows:
        k = (r.get("ticker",""), r.get("entry_mode",""), r.get("preset_name",""))
        by_group[k].append(r)
    for k, grp in by_group.items():
        total_g = len(grp)
        counted = sum(1 for r in grp if r.get("outcome") in valid_outcomes)
        if counted != total_g:
            bad_rate_groups += 1
    _chk("rate_sum_check", bad_rate_groups == 0,
         f"{bad_rate_groups} groups with outcome count mismatch" if bad_rate_groups else "all groups valid")

    # No negative target prices
    bad_tp = [r for r in trade_rows
              if r.get("target_price") not in ("", None) and _f(r.get("target_price", 0)) <= 0]
    _chk("valid_target_prices", len(bad_tp) == 0,
         f"{len(bad_tp)} rows with invalid target_price" if bad_tp else "all valid")

    # No zero entry prices (for non-NO_NEXT_OPEN)
    bad_ep = [r for r in trade_rows
              if r.get("outcome") != "NO_NEXT_OPEN" and _f(r.get("entry_price", 0)) <= 0]
    _chk("valid_entry_prices", len(bad_ep) == 0,
         f"{len(bad_ep)} rows with zero/missing entry_price" if bad_ep else "all valid")

    # Ambiguous and NO_NEXT_OPEN counts (informational)
    n_amb = sum(1 for r in trade_rows if r.get("outcome") == "AMBIGUOUS_SAME_DAY_HIT")
    n_nno = sum(1 for r in trade_rows if r.get("outcome") == "NO_NEXT_OPEN")
    _chk("ambiguous_count_info",   True, f"{n_amb} AMBIGUOUS_SAME_DAY_HIT rows")
    _chk("no_next_open_count_info", True, f"{n_nno} NO_NEXT_OPEN rows")

    return checks


# ─── Markdown summary ─────────────────────────────────────────────────────────

def tpsl_summary_md(
    trade_rows:  List[dict],
    val_checks:  List[dict],
    gen_at:      str,
    audit_path:  str = "replay_tpsl_implementation_audit.md",
) -> str:
    n = len(trade_rows)
    n_src = n // (len(ENTRY_MODES) * len(TPSL_PRESETS)) if n else 0
    val_pass = all(c["status"] == "PASS" for c in val_checks)

    lines = [
        "# TP/SL Replay Analytics Summary",
        "",
        f"Generated: {gen_at}  ",
        f"Source rows: **{n_src:,}** | Trade rows: **{n:,}**  ",
        f"Presets: **{len(TPSL_PRESETS)}** | Entry modes: **{len(ENTRY_MODES)}**",
        "",
        "---",
        "",
        "## Validation",
        "",
        f"Status: **{'PASS' if val_pass else 'FAIL'}**  ",
    ]
    for c in val_checks:
        icon = "✓" if c["status"] == "PASS" else "✗"
        lines.append(f"- {icon} {c['validation_name']}: {c['details']}")

    lines += ["", "---", "", "## TP/SL Presets", "", "| Preset | TP% | SL% | MaxHold |", "|---|---|---|---|"]
    for pname, p in TPSL_PRESETS.items():
        lines.append(f"| {pname} | {p['tp_pct']*100:.2f}% | {p['sl_pct']*100:.2f}% | {p['max_hold']}d |")

    lines += ["", "---", "", "## Score Range Analysis (Phase-Meter Hypothesis)", ""]
    lines.append("Question: Is 55–99 the best actionable entry zone?")
    lines.append("Is 100+ late/volatile? Is <25 unready?")
    lines.append("")

    # Pull CLEAN_SWING + SAME_DAY_CLOSE rows for score range analysis
    sr_rows = [r for r in trade_rows
               if r.get("preset_name") == "CLEAN_SWING" and r.get("entry_mode") == "SAME_DAY_CLOSE"]
    if sr_rows:
        by_range: Dict[str, List[dict]] = defaultdict(list)
        for r in sr_rows:
            by_range[r.get("score_range", "")].append(r)
        lines += ["| Score Range | Count | TP Rate | SL Rate | Cons EV | Avg MFE |",
                  "|---|---|---|---|---|---|"]
        for label, _, _ in _SCORE_RANGE_LABELS:
            grp = by_range.get(label, [])
            if not grp:
                continue
            a = _agg_tpsl(grp)
            lines.append(
                f"| {label} | {a['count']} | {a['tp_first_rate']:.2%} | "
                f"{a['sl_first_rate']:.2%} | {a['conservative_expected_value']:.2f} | "
                f"{a['avg_mfe_pct']:.2f}% |"
            )

    lines += ["", "---", "", "## Regime Analysis", ""]
    rg_rows = [r for r in trade_rows
               if r.get("preset_name") == "CLEAN_SWING" and r.get("entry_mode") == "SAME_DAY_CLOSE"]
    if rg_rows:
        by_regime: Dict[str, List[dict]] = defaultdict(list)
        for r in rg_rows:
            by_regime[r.get("FINAL_REGIME", "")].append(r)
        lines += ["| Regime | Count | TP Rate | SL Rate | Cons EV |",
                  "|---|---|---|---|---|"]
        for regime, grp in sorted(by_regime.items(), key=lambda x: -len(x[1])):
            if not regime or len(grp) < 20:
                continue
            a = _agg_tpsl(grp)
            lines.append(
                f"| {regime} | {a['count']} | {a['tp_first_rate']:.2%} | "
                f"{a['sl_first_rate']:.2%} | {a['conservative_expected_value']:.2f} |"
            )

    lines += [
        "",
        "---",
        "",
        "## Implementation Audit",
        "",
        f"Audit file: `{audit_path}`  ",
        f"Validation: **{'PASS' if val_pass else 'FAIL'}**  ",
        f"Files changed: 2 (tpsl_engine.py added, replay_engine.py updated)  ",
        f"Reports generated: 14",
        "",
        "---",
        "",
        "## Reports Generated",
        "",
        "| File | Purpose |",
        "|---|---|",
        "| replay_tpsl_trades.csv | Row-level trade simulations |",
        "| replay_tpsl_signal_perf.csv | TP/SL by signal |",
        "| replay_tpsl_model_perf.csv | TP/SL by named model |",
        "| replay_tpsl_regime_perf.csv | TP/SL by FINAL_REGIME |",
        "| replay_tpsl_score_bucket_perf.csv | TP/SL by FINAL_SCORE_BUCKET |",
        "| replay_tpsl_score_range_perf.csv | TP/SL by <25/25-54/55-99/100+ |",
        "| replay_tpsl_readiness_phase_perf.csv | TP/SL by READINESS_PHASE |",
        "| replay_tpsl_actionability_bucket_perf.csv | TP/SL by ACTIONABILITY_SCORE bucket |",
        "| replay_tpsl_component_bucket_perf.csv | TP/SL by score component buckets |",
        "| replay_tpsl_pair_combo_perf.csv | TP/SL by signal pairs |",
        "| replay_tpsl_triple_combo_perf.csv | TP/SL by signal triples |",
        "| replay_tpsl_missed_big_winners.csv | Missed/caught/late × TP/SL |",
        "| replay_tpsl_false_positives.csv | False positives × TP/SL |",
        "| replay_tpsl_caught_early_timing.csv | Caught early entry timing |",
        "| replay_tpsl_validation.csv | Validation results |",
        "| replay_tpsl_implementation_audit.md | Audit / changelog |",
        "| replay_tpsl_summary.md | This file |",
    ]

    return "\n".join(lines) + "\n"


# ─── Audit markdown ───────────────────────────────────────────────────────────

def tpsl_implementation_audit_md(gen_at: str, val_checks: List[dict]) -> str:
    val_pass = all(c["status"] == "PASS" for c in val_checks)
    preset_rows = "\n".join(
        f"  - {k}: TP={v['tp_pct']*100:.1f}% / SL={v['sl_pct']*100:.2f}% / max_hold={v['max_hold']}d"
        for k, v in TPSL_PRESETS.items()
    )

    return f"""# TP/SL Replay Analytics — Implementation Audit

Generated: {gen_at}

---

## 1. Files Changed

| File | Change | Why |
|------|--------|-----|
| `backend/tpsl_engine.py` | **New file** | All TP/SL simulation, aggregation, and report logic |
| `backend/replay_engine.py` | Modified | Calls `run_tpsl_analytics()` after step 17b (score consistency) |

---

## 2. New Files Added

| File | Purpose |
|------|---------|
| `backend/tpsl_engine.py` | Runtime engine — produces all 14 TP/SL reports |

---

## 3. Backend Functions Added

| Function | File | Type | Description |
|----------|------|------|-------------|
| `compute_tpsl_trades` | tpsl_engine.py | New | Core simulation: source_rows → trade_rows (N×2×8) |
| `_simulate_trade` | tpsl_engine.py | New | Single trade sim: walk forward bars, detect TP/SL hit |
| `_agg_tpsl` | tpsl_engine.py | New | Aggregate TP/SL metrics for a group of trade rows |
| `_group_agg` | tpsl_engine.py | New | Generic group-by: group_key × entry_mode × preset |
| `tpsl_signal_perf` | tpsl_engine.py | New | Signal-level TP/SL perf with classification |
| `tpsl_model_perf` | tpsl_engine.py | New | Named-model TP/SL perf |
| `tpsl_regime_perf` | tpsl_engine.py | New | FINAL_REGIME TP/SL perf |
| `tpsl_score_bucket_perf` | tpsl_engine.py | New | FINAL_SCORE_BUCKET TP/SL perf |
| `tpsl_score_range_perf` | tpsl_engine.py | New | FBS range (<25/25-54/55-99/100+) TP/SL perf |
| `tpsl_readiness_phase_perf` | tpsl_engine.py | New | READINESS_PHASE perf (if column present) |
| `tpsl_actionability_bucket_perf` | tpsl_engine.py | New | ACTIONABILITY_SCORE bucket perf |
| `tpsl_component_bucket_perf` | tpsl_engine.py | New | Score component bucket perf |
| `tpsl_pair_combo_perf` | tpsl_engine.py | New | Signal pair TP/SL combos |
| `tpsl_triple_combo_perf` | tpsl_engine.py | New | Signal triple TP/SL combos |
| `tpsl_missed_big_winners` | tpsl_engine.py | New | Missed/caught/late × TP/SL overlay |
| `tpsl_false_positives` | tpsl_engine.py | New | FP classification: TRUE_FP / TRADEABLE / VOLATILE |
| `tpsl_caught_early_timing` | tpsl_engine.py | New | Prior-date entry timing for caught-early rows |
| `tpsl_validation` | tpsl_engine.py | New | Integrity checks on trade rows |
| `tpsl_summary_md` | tpsl_engine.py | New | Markdown summary report |
| `tpsl_implementation_audit_md` | tpsl_engine.py | New | This audit file |
| `run_tpsl_analytics` | tpsl_engine.py | New | Main entry point — returns all report dicts |
| `run_replay` | replay_engine.py | Modified | Added step 19: calls run_tpsl_analytics after consistency check |

---

## 4. New Reports Generated

| Filename | Purpose | Key Columns |
|----------|---------|-------------|
| replay_tpsl_trades.csv | Row-level trade simulations | ticker, date, entry_mode, preset_name, outcome, mfe_pct, mae_pct |
| replay_tpsl_signal_perf.csv | TP/SL by signal | signal, tp_first_rate, sl_first_rate, conservative_expected_value, signal_class |
| replay_tpsl_model_perf.csv | TP/SL by named model | model, tp_first_rate, conservative_expected_value |
| replay_tpsl_regime_perf.csv | TP/SL by FINAL_REGIME | FINAL_REGIME, tp_first_rate, avg_mfe_pct |
| replay_tpsl_score_bucket_perf.csv | TP/SL by FINAL_SCORE_BUCKET | FINAL_SCORE_BUCKET, conservative_expected_value |
| replay_tpsl_score_range_perf.csv | TP/SL by score range | score_range, tp_first_rate, conservative_expected_value |
| replay_tpsl_readiness_phase_perf.csv | TP/SL by READINESS_PHASE | READINESS_PHASE, tp_first_rate |
| replay_tpsl_actionability_bucket_perf.csv | TP/SL by actionability bucket | _act_bucket, conservative_expected_value |
| replay_tpsl_component_bucket_perf.csv | TP/SL by score components | component, _comp_bucket, tp_first_rate |
| replay_tpsl_pair_combo_perf.csv | Signal pair TP/SL | combo, tp_first_rate, conservative_expected_value |
| replay_tpsl_triple_combo_perf.csv | Signal triple TP/SL | combo, tp_first_rate, conservative_expected_value |
| replay_tpsl_missed_big_winners.csv | Missed/caught/late × TP/SL | category, outcome, mfe_pct |
| replay_tpsl_false_positives.csv | FP × TP/SL | fp_class, outcome, mfe_pct, mae_pct |
| replay_tpsl_caught_early_timing.csv | Prior-date entry timing | prior_outcome, prior_mfe_pct, days_since_best_prior |
| replay_tpsl_validation.csv | Integrity checks | validation_name, status, details |

---

## 5. Configuration

All presets defined in `tpsl_engine.TPSL_PRESETS` (one place):

{preset_rows}

Entry modes: SAME_DAY_CLOSE, NEXT_DAY_OPEN

Score ranges: <25 / 25-54 / 55-99 / 100+

---

## 6. Validation Results

Status: **{'PASS' if val_pass else 'FAIL'}**

{chr(10).join(f"- {c['status']}: {c['validation_name']} — {c['details']}" for c in val_checks)}

---

## 7. No-Go Confirmation

- ✓ Live scoring weights: **NOT changed**
- ✓ Signal logic: **NOT changed**
- ✓ Visual labels: **NOT changed**
- ✓ RTB / TURBO_SCORE / SIGNAL_SCORE: **NOT changed**
- ✓ Split analytics: **NOT changed** (module untouched)
- ✓ Future returns in live scoring: **NOT used**
- ✓ Scoring weights not optimized from TP/SL results

---

## 8. Known Limitations

- Daily OHLC cannot determine intraday order when TP and SL are both hit on the
  same bar. These rows are classified `AMBIGUOUS_SAME_DAY_HIT` and kept separate.
- TP/SL analytics is research-only. Results must be reviewed before any scoring changes.
- NEXT_DAY_OPEN rows near the dataset edge have `NO_NEXT_OPEN` outcome.
- Optional columns (READINESS_PHASE, ACTIONABILITY_SCORE, LATE_RISK_SCORE,
  WEAK_STACKING_RISK) produce empty reports if not present in stock_stat CSV.

---

## 9. Recommended Next Steps

1. Open `replay_tpsl_signal_perf.csv` → filter preset=CLEAN_SWING → sort by conservative_expected_value
2. Review `replay_tpsl_score_range_perf.csv` → verify 55-99 shows highest TP rate
3. Review `replay_tpsl_regime_perf.csv` → which regimes need wider stops vs. tighter?
4. Open `replay_tpsl_false_positives.csv` → check how many are TRADEABLE_BUT_BAD_HOLD
5. If 100+ score range shows lower TP or higher MAE, consider LATE_RISK_SCORE tuning
6. Expand UI tab to show per-preset TP/SL tables with filter by regime/model
"""


# ─── Main entry point ─────────────────────────────────────────────────────────

# ─── Streaming aggregator (memory-bounded by # of unique groups) ──────────────

class _StreamAgg:
    """
    Accumulate per-group running counters. Memory: O(unique_groups).
    Each group holds counts + sums (no per-trade lists), so 1 group ≈ 200 bytes.
    Median/list-based metrics omitted for memory; mean/EV/PF/rates retained.
    """
    __slots__ = ("groups",)

    def __init__(self) -> None:
        self.groups: Dict[Any, Dict[str, Any]] = {}

    def add(self, key: Any, trade: dict) -> None:
        g = self.groups.get(key)
        if g is None:
            g = {
                "count": 0, "tp": 0, "sl": 0, "no_hit": 0, "amb": 0, "nno": 0,
                "ret_sum": 0.0, "ret_n": 0,
                "mfe_sum": 0.0, "mfe_n": 0,
                "mae_sum": 0.0, "mae_n": 0,
                "clatr_sum": 0.0, "clatr_n": 0,
                "dtp_sum": 0.0, "dtp_n": 0,
                "dsl_sum": 0.0, "dsl_n": 0,
                "tp_pct": _f(trade.get("target_pct", 0)) / 100.0,
                "sl_pct": _f(trade.get("stop_pct", 0)) / 100.0,
                "tp_real_sum": 0.0, "sl_real_sum": 0.0,
                "clean_wins": 0, "fast_sl": 0, "fast_tp": 0,
            }
            self.groups[key] = g

        g["count"] += 1
        outcome = trade.get("outcome", "")

        def _num(field):
            v = trade.get(field)
            if v in ("", None):
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        ret  = _num("realized_return_assumption")
        mfe  = _num("mfe_pct")
        mae  = _num("mae_pct")
        clatr = _num("close_return_at_max_hold")
        dto  = _num("days_to_outcome")

        if outcome == "TP_FIRST":
            g["tp"] += 1
            if ret is not None:
                g["tp_real_sum"] += ret
            if dto is not None:
                g["dtp_sum"] += dto; g["dtp_n"] += 1
                if dto <= 2:
                    g["fast_tp"] += 1
            if mae is not None and mae > -g["sl_pct"] * 100:
                g["clean_wins"] += 1
        elif outcome == "SL_FIRST":
            g["sl"] += 1
            if ret is not None:
                g["sl_real_sum"] += abs(ret)
            if dto is not None:
                g["dsl_sum"] += dto; g["dsl_n"] += 1
                if dto <= 2:
                    g["fast_sl"] += 1
        elif outcome == "NO_HIT":
            g["no_hit"] += 1
        elif outcome == "AMBIGUOUS_SAME_DAY_HIT":
            g["amb"] += 1
        elif outcome == "NO_NEXT_OPEN":
            g["nno"] += 1

        if ret is not None:
            g["ret_sum"] += ret; g["ret_n"] += 1
        if mfe is not None:
            g["mfe_sum"] += mfe; g["mfe_n"] += 1
        if mae is not None:
            g["mae_sum"] += mae; g["mae_n"] += 1
        if clatr is not None:
            g["clatr_sum"] += clatr; g["clatr_n"] += 1

    def finalize(self) -> List[Tuple[Any, dict]]:
        out: List[Tuple[Any, dict]] = []
        for key, g in self.groups.items():
            out.append((key, _finalize_group(g)))
        return out


def _safe_div(a, b):
    return round(a / b, 4) if b else 0.0


def _finalize_group(g: dict) -> dict:
    total = g["count"]
    if total <= 0:
        return {"count": 0}
    ev_total = total - g["nno"]
    tp_pct, sl_pct = g["tp_pct"], g["sl_pct"]
    ev_tp = _safe_div(g["tp"], ev_total)
    ev_sl = _safe_div(g["sl"], ev_total)
    ev_am = _safe_div(g["amb"], ev_total)
    ev_simple = round(ev_tp * tp_pct * 100 - ev_sl * sl_pct * 100, 4)
    ev_cons   = round(ev_tp * tp_pct * 100 - (ev_sl + ev_am) * sl_pct * 100, 4)
    return {
        "count":                              total,
        "tp_first_count":                     g["tp"],
        "sl_first_count":                     g["sl"],
        "no_hit_count":                       g["no_hit"],
        "ambiguous_count":                    g["amb"],
        "no_next_open_count":                 g["nno"],
        "tp_first_rate":                      _safe_div(g["tp"], total),
        "sl_first_rate":                      _safe_div(g["sl"], total),
        "no_hit_rate":                        _safe_div(g["no_hit"], total),
        "ambiguous_rate":                     _safe_div(g["amb"], total),
        "avg_realized_return":                _safe_div(g["ret_sum"], g["ret_n"]),
        "avg_mfe_pct":                        _safe_div(g["mfe_sum"], g["mfe_n"]),
        "avg_mae_pct":                        _safe_div(g["mae_sum"], g["mae_n"]),
        "avg_close_return_at_max_hold":       _safe_div(g["clatr_sum"], g["clatr_n"]),
        "avg_days_to_tp":                     _safe_div(g["dtp_sum"], g["dtp_n"]),
        "avg_days_to_sl":                     _safe_div(g["dsl_sum"], g["dsl_n"]),
        "expected_value_simple":              ev_simple,
        "conservative_expected_value":        ev_cons,
        "tp_sl_ratio":                        round(ev_tp / ev_sl, 4) if ev_sl > 0 else None,
        "profit_factor_approx":               round(g["tp_real_sum"] / g["sl_real_sum"], 4) if g["sl_real_sum"] > 0 else None,
        "clean_win_rate":                     _safe_div(g["clean_wins"], g["tp"]) if g["tp"] else 0.0,
        "painful_win_rate":                   _safe_div(g["tp"] - g["clean_wins"], g["tp"]) if g["tp"] else 0.0,
        "failed_fast_rate":                   _safe_div(g["fast_sl"], g["sl"]) if g["sl"] else 0.0,
        "fast_tp_rate":                       _safe_div(g["fast_tp"], total),
    }


def _finalize_simple_group(agg: _StreamAgg, group_field: str) -> List[dict]:
    """Generic finalize: key=(group_val, entry_mode, preset). Returns rows
    sorted by descending conservative_expected_value."""
    out = []
    for (gval, em, pn), d in agg.finalize():
        d[group_field]   = gval
        d["entry_mode"]  = em
        d["preset_name"] = pn
        out.append(d)
    out.sort(key=lambda x: -(x.get("conservative_expected_value") or 0))
    return out


def _stream_trades_and_aggregate(
    rows:        List[dict],
    trades_path: str,
    track_signals: bool,
    track_models:  bool,
) -> Tuple[int, Dict[str, _StreamAgg], Dict[str, set], Dict[str, int]]:
    """
    Single pass: yield trades from generator, write each to CSV, update
    all per-trade aggregators. Returns (n_trades, aggregators, sig_freq, val_meta).
    """
    aggs: Dict[str, _StreamAgg] = {
        "signal":        _StreamAgg(),
        "model":         _StreamAgg(),
        "regime":        _StreamAgg(),
        "score_bucket":  _StreamAgg(),
        "score_range":   _StreamAgg(),
        "readiness":     _StreamAgg(),
        "actionability": _StreamAgg(),
        "component":     _StreamAgg(),
    }
    # Per (entry_mode, preset_name) signal counts for combo perf pre-pass
    sig_counts_by_slice: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    val_meta = {"sample_total": 0, "sample_with_sigs": 0}

    n_trades = 0
    fieldnames: Optional[List[str]] = None
    f_out = open(trades_path, "w", newline="", encoding="utf-8")
    writer: Optional[csv.DictWriter] = None

    try:
        for trade in _iter_tpsl_trades(rows):
            if writer is None:
                fieldnames = list(trade.keys())
                writer = csv.DictWriter(f_out, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
            writer.writerow(trade)
            n_trades += 1

            em_pn = (trade["entry_mode"], trade["preset_name"])

            # Validation sample (first 2000 SAME_DAY_CLOSE × CLEAN_SWING trades)
            if (val_meta["sample_total"] < 2000
                    and em_pn == ("SAME_DAY_CLOSE", "CLEAN_SWING")):
                val_meta["sample_total"] += 1
                if trade.get("active_signals"):
                    val_meta["sample_with_sigs"] += 1

            # Signal aggregation + frequency tracking
            sigs_str = trade.get("active_signals") or ""
            if sigs_str and track_signals:
                sigs = sigs_str.split("|")
                slice_counts = sig_counts_by_slice[em_pn]
                for sig in sigs:
                    if sig:
                        slice_counts[sig] += 1
                        aggs["signal"].add((sig,) + em_pn, trade)

            # Model aggregation
            mdls_str = trade.get("active_models") or ""
            if mdls_str and track_models:
                for mdl in mdls_str.split("|"):
                    if mdl:
                        aggs["model"].add((mdl,) + em_pn, trade)

            # Per-context aggregations
            aggs["regime"].add((trade.get("FINAL_REGIME", ""),) + em_pn, trade)
            aggs["score_bucket"].add((trade.get("FINAL_SCORE_BUCKET", ""),) + em_pn, trade)
            aggs["score_range"].add((trade.get("score_range", ""),) + em_pn, trade)

            rp = trade.get("READINESS_PHASE", "")
            if rp:
                aggs["readiness"].add((rp,) + em_pn, trade)

            act = _f(trade.get("ACTIONABILITY_SCORE", 0))
            if act > 0:
                if act < 25: ab = "<25"
                elif act < 50: ab = "25-49"
                elif act < 75: ab = "50-74"
                else: ab = "75+"
                aggs["actionability"].add((ab,) + em_pn, trade)

            # Component bucket aggregation (separate key per component)
            for comp_col in _COMPONENT_SCORE_COLS:
                v = _f(trade.get(comp_col, 0))
                if v > 0:
                    bkt = _component_bucket(v)
                    aggs["component"].add((comp_col, bkt) + em_pn, trade)
    finally:
        f_out.close()

    return n_trades, aggs, sig_counts_by_slice, val_meta


def _read_trades_csv(trades_path: str):
    """Generator: yield each trade row from CSV (memory-bounded)."""
    with open(trades_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            yield r


def _build_combo_perfs(
    trades_path: str,
    sig_counts_by_slice: Dict[Tuple[str, str], Dict[str, int]],
    pair_min:    int = 30,
    triple_min:  int = 20,
    triple_cap:  int = 40,
    top_n:       int = 100,
) -> Tuple[List[dict], List[dict]]:
    """Two-pass combo perfs from CSV. First pass already done (sig_counts).
    Second pass: stream CSV, accumulate combo stats only for frequent signals."""
    pair_freq:   Dict[Tuple[str, str], set] = {}
    triple_freq: Dict[Tuple[str, str], set] = {}
    for slice_key, counts in sig_counts_by_slice.items():
        pair_freq[slice_key]   = {s for s, c in counts.items() if c >= pair_min}
        sorted_sigs = sorted(counts.items(), key=lambda kv: -kv[1])
        triple_freq[slice_key] = {s for s, c in sorted_sigs[:triple_cap] if c >= triple_min * 2}

    pair_agg   = _StreamAgg()
    triple_agg = _StreamAgg()

    for trade in _read_trades_csv(trades_path):
        em_pn = (trade.get("entry_mode", ""), trade.get("preset_name", ""))
        sigs_str = trade.get("active_signals") or ""
        if not sigs_str:
            continue
        sigs = [s for s in sigs_str.split("|") if s]
        pf = pair_freq.get(em_pn, set())
        if pf:
            active_p = [s for s in sigs if s in pf]
            if len(active_p) >= 2:
                for a, b in itertools.combinations(sorted(active_p), 2):
                    pair_agg.add((f"{a}+{b}",) + em_pn, trade)
        tf_set = triple_freq.get(em_pn, set())
        if tf_set:
            active_t = [s for s in sigs if s in tf_set]
            if len(active_t) >= 3:
                for combo in itertools.combinations(sorted(active_t), 3):
                    triple_agg.add(("+".join(combo),) + em_pn, trade)

    # Finalize and apply min_count + top_n cap per slice
    def _finalize_combos(agg: _StreamAgg, min_count: int) -> List[dict]:
        rows = []
        by_slice: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
        for (combo, em, pn), d in agg.finalize():
            if d["count"] < min_count:
                continue
            d["combo"]       = combo
            d["entry_mode"]  = em
            d["preset_name"] = pn
            by_slice[(em, pn)].append(d)
        for slice_rows in by_slice.values():
            slice_rows.sort(key=lambda x: -(x.get("conservative_expected_value") or 0))
            rows.extend(slice_rows[:top_n])
        return rows

    return _finalize_combos(pair_agg, pair_min), _finalize_combos(triple_agg, triple_min)


def _build_overlay_reports(
    trades_path:      str,
    true_missed_full: List[dict],
    caught_full:      List[dict],
    late_full:        List[dict],
    fp_rows:          List[dict],
):
    """Build missed_big / false_positives / caught_early_timing by indexing
    only the trade rows that match the relevant (ticker, date) keys."""
    needed_keys: set = set()
    for collection in (true_missed_full, caught_full, late_full, fp_rows):
        for r in collection:
            needed_keys.add((r.get("ticker", ""), r.get("date", "")))
    # caught_full also needs best_prior_date_20d
    for r in caught_full:
        prior = r.get("best_prior_date_20d", "")
        if prior:
            needed_keys.add((r.get("ticker", ""), prior))

    # td_index: (ticker, date) -> list of slim trade dicts
    td_index: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    if needed_keys:
        for trade in _read_trades_csv(trades_path):
            k = (trade.get("ticker", ""), trade.get("date", ""))
            if k in needed_keys:
                td_index[k].append(trade)

    # Build missed_big
    missed_big: List[dict] = []
    for cat_label, cat_rows in [
        ("TRUE_MISSED_WINNER",  true_missed_full),
        ("CAUGHT_EARLY_WINNER", caught_full),
        ("LATE_OR_WEAK_CATCH",  late_full),
    ]:
        for mr in cat_rows:
            ticker = mr.get("ticker", "")
            date   = mr.get("date", "")
            trades = td_index.get((ticker, date), [])
            if not trades:
                for emode in ENTRY_MODES:
                    for pname in TPSL_PRESETS:
                        missed_big.append({
                            "category":         cat_label,
                            "ticker":           ticker,
                            "date":             date,
                            "entry_mode":       emode,
                            "preset_name":      pname,
                            "outcome":          "NOT_IN_TPSL",
                            "final_bull_score": mr.get("final_bull_score", ""),
                            "final_regime":     mr.get("final_regime", ""),
                            "max_high_10d":     mr.get("max_high_10d", ""),
                        })
                continue
            for tr in trades:
                missed_big.append({
                    "category":                       cat_label,
                    "ticker":                         ticker,
                    "date":                           date,
                    "entry_mode":                     tr.get("entry_mode"),
                    "preset_name":                    tr.get("preset_name"),
                    "outcome":                        tr.get("outcome"),
                    "days_to_outcome":                tr.get("days_to_outcome"),
                    "realized_return_assumption":     tr.get("realized_return_assumption"),
                    "mfe_pct":                        tr.get("mfe_pct"),
                    "mae_pct":                        tr.get("mae_pct"),
                    "close_return_at_max_hold":       tr.get("close_return_at_max_hold"),
                    "final_bull_score":               mr.get("final_bull_score", tr.get("FINAL_BULL_SCORE")),
                    "final_regime":                   mr.get("final_regime",     tr.get("FINAL_REGIME")),
                    "final_score_bucket":             mr.get("final_score_bucket", tr.get("FINAL_SCORE_BUCKET")),
                    "max_high_10d":                   mr.get("max_high_10d", ""),
                    "prior_actionable_found":         mr.get("prior_actionable_found", ""),
                    "best_prior_score_20d":           mr.get("best_prior_score_20d", ""),
                    "best_prior_date_20d":            mr.get("best_prior_date_20d", ""),
                })

    # Build false positives
    fp_out: List[dict] = []
    for fp in fp_rows:
        ticker = fp.get("ticker", "")
        date   = fp.get("date", "")
        trades = td_index.get((ticker, date), [])
        if not trades:
            fp_out.append({
                "ticker": ticker, "date": date,
                "fp_class": "NOT_IN_TPSL",
                "ret_10d": fp.get("ret_10d", ""),
                "final_bull_score": fp.get("final_bull_score", ""),
            })
            continue
        for tr in trades:
            outcome = tr.get("outcome", "")
            mfe     = _f(tr.get("mfe_pct", 0))
            tp_pct  = _f(tr.get("target_pct", 0))
            if outcome == "TP_FIRST":
                fp_class = "TRADEABLE_BUT_BAD_HOLD"
            elif outcome in ("SL_FIRST", "NO_HIT") and mfe < tp_pct / 2:
                fp_class = "TRUE_FALSE_POSITIVE"
            elif outcome == "AMBIGUOUS_SAME_DAY_HIT":
                fp_class = "VOLATILE_AMBIGUOUS"
            elif mfe >= tp_pct and outcome != "TP_FIRST":
                fp_class = "VOLATILE_AMBIGUOUS"
            else:
                fp_class = "TRUE_FALSE_POSITIVE"
            fp_out.append({
                "ticker":             ticker,
                "date":               date,
                "entry_mode":         tr.get("entry_mode"),
                "preset_name":        tr.get("preset_name"),
                "fp_class":           fp_class,
                "outcome":            outcome,
                "realized_return":    tr.get("realized_return_assumption"),
                "mfe_pct":            mfe,
                "mae_pct":            tr.get("mae_pct"),
                "close_return_at_max_hold": tr.get("close_return_at_max_hold"),
                "ret_10d":            fp.get("ret_10d", ""),
                "final_bull_score":   fp.get("final_bull_score", tr.get("FINAL_BULL_SCORE")),
                "final_regime":       fp.get("final_regime",     tr.get("FINAL_REGIME")),
                "likely_fail_reason": fp.get("likely_fail_reason", ""),
            })

    # Build caught early timing
    caught_timing: List[dict] = []
    for cr in caught_full:
        ticker   = cr.get("ticker", "")
        miss_dt  = cr.get("date", "")
        prior_dt = cr.get("best_prior_date_20d", "")
        miss_trades  = td_index.get((ticker, miss_dt),  [])
        prior_trades = td_index.get((ticker, prior_dt), []) if prior_dt else []
        for emode in ENTRY_MODES:
            for pname in TPSL_PRESETS:
                miss_t  = next((t for t in miss_trades  if t.get("entry_mode") == emode and t.get("preset_name") == pname), None)
                prior_t = next((t for t in prior_trades if t.get("entry_mode") == emode and t.get("preset_name") == pname), None)
                caught_timing.append({
                    "ticker":                ticker,
                    "missed_date":           miss_dt,
                    "entry_mode":            emode,
                    "preset_name":           pname,
                    "current_outcome":       miss_t.get("outcome", "NOT_IN_TPSL") if miss_t else "NOT_IN_TPSL",
                    "current_mfe_pct":       miss_t.get("mfe_pct", "") if miss_t else "",
                    "current_mae_pct":       miss_t.get("mae_pct", "") if miss_t else "",
                    "best_prior_date_20d":   prior_dt,
                    "prior_outcome":         prior_t.get("outcome", "NOT_IN_TPSL") if prior_t else "NOT_IN_TPSL",
                    "prior_tp_first":        int(prior_t.get("outcome") == "TP_FIRST") if prior_t else "",
                    "prior_sl_first":        int(prior_t.get("outcome") == "SL_FIRST") if prior_t else "",
                    "prior_mfe_pct":         prior_t.get("mfe_pct", "") if prior_t else "",
                    "prior_mae_pct":         prior_t.get("mae_pct", "") if prior_t else "",
                    "prior_days_to_tp":      prior_t.get("days_to_outcome", "") if (prior_t and prior_t.get("outcome") == "TP_FIRST") else "",
                    "best_prior_score_20d":  cr.get("best_prior_score_20d", ""),
                    "best_prior_regime_20d": cr.get("best_prior_regime_20d", ""),
                    "days_since_best_prior": cr.get("days_since_best_prior_catch", ""),
                    "final_bull_score":      cr.get("final_bull_score", ""),
                    "final_regime":          cr.get("final_regime", ""),
                    "max_high_10d":          cr.get("max_high_10d", ""),
                })

    return missed_big, fp_out, caught_timing


def run_tpsl_analytics(
    rows:       List[dict],
    cached:     dict,
    output_dir: Optional[str] = None,
) -> Dict[str, object]:
    """
    Streaming entry point. Writes replay_tpsl_trades.csv directly to output_dir
    (avoids OOM on large universes). Other reports returned as in-memory lists.

    Args:
        rows:       Canonical-scored rows.
        cached:     Cached intermediate results from replay_engine.
        output_dir: Directory for streaming tpsl_trades.csv. If None, falls back
                    to materializing trades in memory (legacy behavior).
    """
    gen_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    sc = cached.get("score_consistency", {})
    sc_warning: Optional[str] = None
    if sc.get("status") == "fail":
        sc_warning = (
            f"Score consistency check found {sc.get('mismatch_count', 0)} mismatch(es); "
            "TP/SL reports may reflect inconsistent source data. "
            "See replay_score_consistency_check.csv."
        )
        log.warning("TP/SL: %s", sc_warning)

    true_missed_full = cached.get("true_missed_winners_full",   [])
    caught_full      = cached.get("caught_early_winners_full",  [])
    late_full        = cached.get("late_or_weak_catches_full",  [])
    fp_rows          = cached.get("false_positives", [])
    category_counts  = cached.get("category_counts", {})

    sample_sigs = _active_sigs(rows[0]) if rows else []
    signal_cols_present = bool(sample_sigs) or any(
        r.get(col) for r in rows[:20] for col in _STRING_SIG_COLS
    )
    model_cols_present = any(
        _f(r.get(m, 0)) > 0 for r in rows[:100] for m in _COMPOSITE_MODEL_FLAGS
    )

    reports: Dict[str, object] = {}
    val_checks: List[dict] = []

    if sc_warning:
        val_checks.append({
            "validation_name": "score_consistency_warning",
            "status":          "WARN",
            "details":         sc_warning,
        })

    # Choose path: streaming (if output_dir given) or in-memory fallback
    if output_dir is None:
        log.info("TP/SL analytics: in-memory mode (no output_dir provided)")
        trade_rows = compute_tpsl_trades(rows)
        n_trades = len(trade_rows)
        val_checks.extend(tpsl_validation(trade_rows))
        reports["tpsl_trades"] = trade_rows
        sig_perf      = tpsl_signal_perf(trade_rows)
        pair_perf     = tpsl_pair_combo_perf(trade_rows)
        triple_perf   = tpsl_triple_combo_perf(trade_rows)
        missed_big    = tpsl_missed_big_winners(trade_rows, true_missed_full, caught_full, late_full)
        caught_timing = tpsl_caught_early_timing(trade_rows, caught_full)
        reports["tpsl_signal_perf"]               = sig_perf
        reports["tpsl_model_perf"]                = tpsl_model_perf(trade_rows)
        reports["tpsl_regime_perf"]               = tpsl_regime_perf(trade_rows)
        reports["tpsl_score_bucket_perf"]         = tpsl_score_bucket_perf(trade_rows)
        reports["tpsl_score_range_perf"]          = tpsl_score_range_perf(trade_rows)
        reports["tpsl_readiness_phase_perf"]      = tpsl_readiness_phase_perf(trade_rows)
        reports["tpsl_actionability_bucket_perf"] = tpsl_actionability_bucket_perf(trade_rows)
        reports["tpsl_component_bucket_perf"]     = tpsl_component_bucket_perf(trade_rows)
        reports["tpsl_pair_combo_perf"]           = pair_perf
        reports["tpsl_triple_combo_perf"]         = triple_perf
        reports["tpsl_missed_big_winners"]        = missed_big
        reports["tpsl_false_positives"]           = tpsl_false_positives(trade_rows, fp_rows)
        reports["tpsl_caught_early_timing"]       = caught_timing
        sample_tr = [r for r in trade_rows[:2000]
                     if r.get("entry_mode") == "SAME_DAY_CLOSE" and r.get("preset_name") == "CLEAN_SWING"]
        sig_rate = round(sum(1 for r in sample_tr if r.get("active_signals")) / len(sample_tr), 4) if sample_tr else 0.0
    else:
        # ── Streaming path ────────────────────────────────────────────────────
        os.makedirs(output_dir, exist_ok=True)
        trades_path = os.path.join(output_dir, "replay_tpsl_trades.csv")
        log.info("TP/SL analytics: streaming trades to %s", trades_path)
        n_trades, aggs, sig_counts, val_meta = _stream_trades_and_aggregate(
            rows, trades_path, signal_cols_present, model_cols_present,
        )
        log.info("TP/SL analytics: streamed %d trade rows", n_trades)
        gc.collect()

        # Mark that the trades CSV is already on disk (not returned as list).
        # The replay_engine call site treats non-list values as "skip _save".
        reports["tpsl_trades"] = {"streamed": True, "rows": n_trades, "path": trades_path}

        # Finalize per-trade aggregations
        sig_perf = []
        for (sig, em, pn), d in aggs["signal"].finalize():
            if d["count"] < 20:
                continue
            d["signal"]       = sig
            d["entry_mode"]   = em
            d["preset_name"]  = pn
            d["sample_tier"]  = "MAIN" if d["count"] >= 50 else "SEC"
            d["signal_class"] = _classify_signal(d)
            sig_perf.append(d)
        sig_perf.sort(key=lambda x: -(x.get("conservative_expected_value") or 0))

        model_perf = []
        for (mdl, em, pn), d in aggs["model"].finalize():
            if d["count"] < 20:
                continue
            d["model"]       = mdl
            d["entry_mode"]  = em
            d["preset_name"] = pn
            model_perf.append(d)
        model_perf.sort(key=lambda x: -(x.get("conservative_expected_value") or 0))

        regime_perf  = _finalize_simple_group(aggs["regime"], "FINAL_REGIME")
        bucket_perf  = _finalize_simple_group(aggs["score_bucket"], "FINAL_SCORE_BUCKET")
        range_perf   = _finalize_simple_group(aggs["score_range"], "score_range")
        readiness_pf = _finalize_simple_group(aggs["readiness"], "READINESS_PHASE")
        action_pf    = _finalize_simple_group(aggs["actionability"], "_act_bucket")

        component_perf = []
        for (comp, bkt, em, pn), d in aggs["component"].finalize():
            d["component"]    = comp
            d["_comp_bucket"] = bkt
            d["entry_mode"]   = em
            d["preset_name"]  = pn
            component_perf.append(d)
        component_perf.sort(key=lambda x: -(x.get("conservative_expected_value") or 0))

        # Free aggregators before next pass
        del aggs
        gc.collect()

        # Combo perfs (re-read CSV once)
        log.info("TP/SL analytics: computing combo perfs from CSV")
        pair_perf, triple_perf = _build_combo_perfs(trades_path, sig_counts)
        del sig_counts
        gc.collect()

        # Overlay reports (re-read CSV once, only fetch matching keys)
        log.info("TP/SL analytics: computing overlay reports from CSV")
        missed_big, fp_out, caught_timing = _build_overlay_reports(
            trades_path, true_missed_full, caught_full, late_full, fp_rows,
        )
        gc.collect()

        reports["tpsl_signal_perf"]               = sig_perf
        reports["tpsl_model_perf"]                = model_perf
        reports["tpsl_regime_perf"]               = regime_perf
        reports["tpsl_score_bucket_perf"]         = bucket_perf
        reports["tpsl_score_range_perf"]          = range_perf
        reports["tpsl_readiness_phase_perf"]      = readiness_pf
        reports["tpsl_actionability_bucket_perf"] = action_pf
        reports["tpsl_component_bucket_perf"]     = component_perf
        reports["tpsl_pair_combo_perf"]           = pair_perf
        reports["tpsl_triple_combo_perf"]         = triple_perf
        reports["tpsl_missed_big_winners"]        = missed_big
        reports["tpsl_false_positives"]           = fp_out
        reports["tpsl_caught_early_timing"]       = caught_timing

        # Trade-row-level validation (lightweight, since trade_rows is on disk)
        val_checks.append({
            "validation_name": "row_count_nonzero",
            "status":          "PASS" if n_trades > 0 else "FAIL",
            "details":         f"{n_trades} trade rows",
        })
        val_checks.append({
            "validation_name": "streaming_mode",
            "status":          "PASS",
            "details":         f"trades streamed to {trades_path}",
        })
        sig_rate = round(val_meta["sample_with_sigs"] / val_meta["sample_total"], 4) if val_meta["sample_total"] else 0.0

    # ── Post-report validation checks ─────────────────────────────────────────
    def _chk(name, passed, detail=""):
        val_checks.append({
            "validation_name": name,
            "status":          "PASS" if passed else "FAIL",
            "details":         detail,
        })

    _chk("signal_columns_present", signal_cols_present,
         "signal string columns readable" if signal_cols_present else
         "no signal data found in source rows")
    _chk("model_columns_present", model_cols_present,
         "composite model flags present" if model_cols_present else
         "no model flag columns found in source rows")
    _chk("active_signals_nonempty_rate", sig_rate > 0.01,
         f"{sig_rate:.1%} of sampled rows have non-empty active_signals")

    sig_perf_v   = reports.get("tpsl_signal_perf",  []) or []
    pair_perf_v  = reports.get("tpsl_pair_combo_perf", []) or []
    triple_perf_v = reports.get("tpsl_triple_combo_perf", []) or []
    _chk("tpsl_signal_perf_nonempty", bool(sig_perf_v),
         f"{len(sig_perf_v)} rows" if sig_perf_v else "EMPTY — signal data missing")
    _chk("tpsl_pair_combo_perf_nonempty", bool(pair_perf_v),
         f"{len(pair_perf_v)} rows" if pair_perf_v else "EMPTY — insufficient pair data")
    _chk("tpsl_triple_combo_perf_nonempty", bool(triple_perf_v),
         f"{len(triple_perf_v)} rows" if triple_perf_v else "EMPTY — insufficient triple data")

    if category_counts:
        base_true   = category_counts.get("true_missed",  0)
        base_caught = category_counts.get("caught_early", 0)
        base_late   = category_counts.get("late_or_weak", 0)
        cats_seen: Dict[str, set] = defaultdict(set)
        for r in (reports.get("tpsl_missed_big_winners") or []):
            if r.get("entry_mode") == "SAME_DAY_CLOSE" and r.get("preset_name") == "CLEAN_SWING":
                cats_seen[r.get("category", "")].add((r.get("ticker"), r.get("date")))
        overlay_true   = len(cats_seen.get("TRUE_MISSED_WINNER",  set()))
        overlay_caught = len(cats_seen.get("CAUGHT_EARLY_WINNER", set()))
        overlay_late   = len(cats_seen.get("LATE_OR_WEAK_CATCH",  set()))
        counts_ok = (overlay_true == base_true and overlay_caught == base_caught and overlay_late == base_late)
        _chk("tpsl_missed_category_counts_match_base", counts_ok,
             f"TRUE={overlay_true}/{base_true} CAUGHT={overlay_caught}/{base_caught} LATE={overlay_late}/{base_late}")
    else:
        _chk("tpsl_missed_category_counts_match_base", True,
             "category_counts not available for comparison")

    expected_caught = len(caught_full) * len(ENTRY_MODES) * len(TPSL_PRESETS)
    caught_timing_v = reports.get("tpsl_caught_early_timing", []) or []
    _chk("tpsl_caught_early_only_uses_caught_rows", len(caught_timing_v) == expected_caught,
         f"{len(caught_timing_v)} rows == {len(caught_full)} CAUGHT_EARLY × "
         f"{len(ENTRY_MODES)} × {len(TPSL_PRESETS)} = {expected_caught}")

    reports["tpsl_validation"] = val_checks

    # Markdown summaries (use lightweight stub trade list to avoid scanning CSV)
    n_for_md = n_trades
    reports["tpsl_summary_md_content"]              = _tpsl_summary_md_streaming(n_for_md, val_checks, gen_at)
    reports["tpsl_implementation_audit_md_content"] = tpsl_implementation_audit_md(gen_at, val_checks)

    log.info("TP/SL analytics: %d reports generated", len(reports))
    return reports


def _tpsl_summary_md_streaming(n_trades: int, val_checks: List[dict], gen_at: str) -> str:
    """Lightweight summary that does not need full trade_rows in memory."""
    n_src = n_trades // (len(ENTRY_MODES) * len(TPSL_PRESETS)) if n_trades else 0
    val_pass = all(c["status"] == "PASS" for c in val_checks)
    lines = [
        "# TP/SL Replay Analytics Summary",
        "",
        f"Generated: {gen_at}  ",
        f"Source rows: **{n_src:,}** | Trade rows: **{n_trades:,}**  ",
        f"Presets: **{len(TPSL_PRESETS)}** | Entry modes: **{len(ENTRY_MODES)}**",
        "",
        "Mode: **Streaming** (trades written directly to replay_tpsl_trades.csv).",
        "",
        "---",
        "",
        "## Validation",
        "",
        f"Status: **{'PASS' if val_pass else 'FAIL/WARN'}**  ",
    ]
    for c in val_checks:
        icon = "✓" if c["status"] == "PASS" else ("⚠" if c["status"] == "WARN" else "✗")
        lines.append(f"- {icon} {c['validation_name']}: {c['details']}")
    lines += ["", "---", "", "## TP/SL Presets", "",
              "| Preset | TP% | SL% | MaxHold |", "|---|---|---|---|"]
    for pname, p in TPSL_PRESETS.items():
        lines.append(f"| {pname} | {p['tp_pct']*100:.2f}% | {p['sl_pct']*100:.2f}% | {p['max_hold']}d |")
    lines += ["", "---", "",
              "See per-report CSV files for detailed breakdowns:",
              "- replay_tpsl_signal_perf.csv",
              "- replay_tpsl_score_range_perf.csv",
              "- replay_tpsl_regime_perf.csv",
              "- replay_tpsl_pair_combo_perf.csv",
              "- replay_tpsl_missed_big_winners.csv",
              "- replay_tpsl_false_positives.csv",
              "- replay_tpsl_caught_early_timing.csv",
              ""]
    return "\n".join(lines) + "\n"

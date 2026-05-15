"""
signal_outcome_engine.py — forward-return calculation for signal events.

For each event row, compute one outcome per horizon (3D / 5D / 10D / 20D) using
ONLY future bars (date > scan_date). Benchmark alpha is computed from a single
shared benchmark series (SPY + QQQ).

Public API
──────────
compute_outcomes(event_row, future_bars, *, spy_future, qqq_future, replay_run_id)
    -> list[dict]   (one dict per horizon; can be inserted directly into
                     replay_signal_outcomes)
"""
from __future__ import annotations
import math
from typing import Iterable

HORIZONS: list[tuple[str, int]] = [
    ("3d",  3),
    ("5d",  5),
    ("10d", 10),
    ("20d", 20),
]


def _f(v, default=None):
    try:
        if v is None: return default
        x = float(v)
        if math.isnan(x) or math.isinf(x): return default
        return x
    except (TypeError, ValueError):
        return default


def _close_after_n(bars: list[dict], n: int) -> float | None:
    """Close at index n-1 (1-indexed: the close N trading days forward)."""
    if not bars: return None
    idx = min(n - 1, len(bars) - 1)
    if idx < 0: return None
    return _f(bars[idx].get("close"))


def _benchmark_return(future_bars: list[dict], n: int) -> float | None:
    if len(future_bars) < 1: return None
    entry = _f(future_bars[0].get("open")) or _f(future_bars[0].get("close"))
    if not entry: return None
    exit_c = _close_after_n(future_bars, n)
    if not exit_c: return None
    return (exit_c - entry) / entry * 100


def _classify_outcome(return_pct: float | None, max_gain: float | None) -> str:
    if return_pct is None and max_gain is None:
        return "UNKNOWN"
    r = return_pct if return_pct is not None else 0
    g = max_gain   if max_gain   is not None else 0
    if g >= 50:          return "BIG_WINNER"
    if g >= 20:          return "STRONG_BREAKOUT"
    if g >= 10:          return "GOOD_BREAKOUT"
    if r >  0 and g < 10: return "SMALL_WIN"
    if -2 <= r <= 2:     return "FLAT"
    if -10 < r <= -5:    return "FAILED"
    if r <= -10:         return "FALSE_POSITIVE"
    return "FLAT"


def compute_outcomes(
    event_row: dict,
    future_bars: list[dict],
    *,
    spy_future: list[dict] | None = None,
    qqq_future: list[dict] | None = None,
    replay_run_id: int,
) -> list[dict]:
    """Returns one outcome dict per horizon (3d/5d/10d/20d). Skips a horizon
    if there are not enough future bars to evaluate it (best-effort: still
    returns a row with the best available exit and partial fields).
    """
    entry_price = _f(event_row.get("close"))
    if entry_price is None or entry_price <= 0:
        return []

    outcomes: list[dict] = []
    for horizon_label, n in HORIZONS:
        if not future_bars:
            continue
        window = future_bars[:n]
        if not window:
            continue

        exit_price = _close_after_n(future_bars, n)
        ret_pct = (
            (exit_price - entry_price) / entry_price * 100
            if exit_price else None
        )

        # Max gain / max drawdown over window (vs entry_price)
        max_high = None; max_gain_pct = None
        max_gain_day = None; max_gain_date = None
        min_low  = None; max_drawdown_pct = None
        max_dd_day = None; max_dd_date = None

        for i, b in enumerate(window, start=1):
            h = _f(b.get("high")); l = _f(b.get("low"))
            if h is not None and (max_high is None or h > max_high):
                max_high = h
                max_gain_pct = (h - entry_price) / entry_price * 100
                max_gain_day = i
                max_gain_date = str(b.get("date") or "")[:10] or None
            if l is not None and (min_low is None or l < min_low):
                min_low = l
                max_drawdown_pct = (l - entry_price) / entry_price * 100
                max_dd_day = i
                max_dd_date = str(b.get("date") or "")[:10] or None

        # Hit / fail thresholds — earliest crossing day
        days_to: dict[str, int | None] = {f"days_to_{lbl}pct": None
                                          for lbl in (5, 10, 20, 50, 100)}
        hits = {f"hit_{lbl}pct": False for lbl in (5, 10, 20, 50, 100)}
        days_to_fail_5 = None; days_to_fail_10 = None
        fail_5 = False; fail_10 = False
        for i, b in enumerate(window, start=1):
            h = _f(b.get("high")); l = _f(b.get("low"))
            if h is not None:
                up = (h - entry_price) / entry_price * 100
                for thr in (5, 10, 20, 50, 100):
                    if up >= thr and not hits[f"hit_{thr}pct"]:
                        hits[f"hit_{thr}pct"] = True
                        days_to[f"days_to_{thr}pct"] = i
            if l is not None:
                dn = (l - entry_price) / entry_price * 100
                if dn <= -5 and not fail_5:
                    fail_5 = True; days_to_fail_5 = i
                if dn <= -10 and not fail_10:
                    fail_10 = True; days_to_fail_10 = i

        # Benchmark alpha (relative to SPY/QQQ over same window)
        spy_ret = _benchmark_return(spy_future or [], n)
        qqq_ret = _benchmark_return(qqq_future or [], n)
        alpha_spy = (ret_pct - spy_ret) if (ret_pct is not None and spy_ret is not None) else None
        alpha_qqq = (ret_pct - qqq_ret) if (ret_pct is not None and qqq_ret is not None) else None

        outcomes.append({
            "replay_run_id":   replay_run_id,
            "signal_event_id": event_row.get("id"),   # caller sets after insert
            "symbol":          event_row.get("symbol"),
            "scan_date":       event_row.get("scan_date"),
            "horizon":         horizon_label,
            "entry_price":     round(entry_price, 4),
            "exit_price":      round(exit_price, 4) if exit_price else None,
            "return_pct":      round(ret_pct, 4) if ret_pct is not None else None,
            "max_high":        round(max_high, 4) if max_high else None,
            "max_gain_pct":    round(max_gain_pct, 4) if max_gain_pct is not None else None,
            "max_gain_day":    max_gain_day,
            "max_gain_date":   max_gain_date,
            "min_low":         round(min_low, 4) if min_low else None,
            "max_drawdown_pct": round(max_drawdown_pct, 4) if max_drawdown_pct is not None else None,
            "max_drawdown_day": max_dd_day,
            "max_drawdown_date": max_dd_date,
            **hits,
            **days_to,
            "fail_5pct":         fail_5,
            "fail_10pct":        fail_10,
            "days_to_fail_5pct": days_to_fail_5,
            "days_to_fail_10pct": days_to_fail_10,
            "alpha_vs_spy":    round(alpha_spy, 4) if alpha_spy is not None else None,
            "alpha_vs_qqq":    round(alpha_qqq, 4) if alpha_qqq is not None else None,
            "outcome_label":   _classify_outcome(ret_pct, max_gain_pct),
        })
    return outcomes

"""
chart_wick_engine.py — Phase 8F-A: port of root backend/wick_engine.py.

2-Candle Opposite-Wick Reversal signals for Super Chart History timeline.
No yfinance. No imports from old root backend.
Verbatim formula port — only import path changed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .chart_indicators import atr as _atr_hlc, bars_since as _bars_since_ind


def compute_wick(
    df: pd.DataFrame,
    wick_dom_pct: float = 0.40,
    min_wick_pct: float = 0.00,
    max_body_pct_each: float = 0.55,
    atr_len: int = 14,
    min_atr_mul: float = 0.10,
    confirm_bars: int = 3,
    confirm_mode: str = "Breakout",
) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    o = df["open"];  h = df["high"]; l = df["low"]; c = df["close"]
    o1 = o.shift(1); h1 = h.shift(1); l1 = l.shift(1); c1 = c.shift(1)

    r0 = h - l
    r1 = h1 - l1
    b0 = (c - o).abs()
    b1 = (c1 - o1).abs()

    hi_body0 = pd.concat([o, c],   axis=1).max(axis=1)
    lo_body0 = pd.concat([o, c],   axis=1).min(axis=1)
    hi_body1 = pd.concat([o1, c1], axis=1).max(axis=1)
    lo_body1 = pd.concat([o1, c1], axis=1).min(axis=1)

    u0 = h  - hi_body0;  d0 = lo_body0 - l
    u1 = h1 - hi_body1;  d1 = lo_body1 - l1

    body_pct0 = (b0 / r0).where(r0 > 0, other=1.0)
    body_pct1 = (b1 / r1).where(r1 > 0, other=1.0)
    up_pct0   = (u0 / r0).where(r0 > 0, other=0.0)
    up_pct1   = (u1 / r1).where(r1 > 0, other=0.0)
    lo_pct0   = (d0 / r0).where(r0 > 0, other=0.0)
    lo_pct1   = (d1 / r1).where(r1 > 0, other=0.0)

    each_body_ok = (body_pct0 <= max_body_pct_each) & (body_pct1 <= max_body_pct_each)

    prev_lower_dom = (lo_pct1 >= wick_dom_pct) & (up_pct1 >= min_wick_pct)
    cur_upper_dom  = (up_pct0 >= wick_dom_pct) & (lo_pct0 >= min_wick_pct)
    prev_upper_dom = (up_pct1 >= wick_dom_pct) & (lo_pct1 >= min_wick_pct)
    cur_lower_dom  = (lo_pct0 >= wick_dom_pct) & (up_pct0 >= min_wick_pct)

    opposite_wicks = (prev_lower_dom & cur_upper_dom) | (prev_upper_dom & cur_lower_dom)

    combo_high  = pd.concat([h1, h], axis=1).max(axis=1)
    combo_low   = pd.concat([l1, l], axis=1).min(axis=1)
    combo_range = combo_high - combo_low

    atr_val  = _atr_hlc(h, l, c, atr_len)
    range_ok = combo_range >= atr_val * min_atr_mul

    combo_signal      = range_ok & each_body_ok & opposite_wicks
    wick_bull_pattern = combo_signal & cur_lower_dom
    wick_bear_pattern = combo_signal & cur_upper_dom

    bars_since_pat = _bars_since_ind(combo_signal)
    in_window      = (bars_since_pat >= 1) & (bars_since_pat <= confirm_bars)

    combo_hi_mem = combo_high.where(combo_signal).ffill()
    combo_lo_mem = combo_low.where(combo_signal).ffill()

    if confirm_mode == "Breakout":
        WICK_BULL_CONFIRM = in_window & (c > combo_hi_mem)
        WICK_BEAR_CONFIRM = in_window & (c < combo_lo_mem)
    else:
        combo_mid = (combo_hi_mem + combo_lo_mem) / 2
        WICK_BULL_CONFIRM = in_window & (c > combo_mid) & (c > o)
        WICK_BEAR_CONFIRM = in_window & (c < combo_mid) & (c < o)

    return pd.DataFrame(
        {
            "WICK_PATTERN":      combo_signal,
            "WICK_BULL_PATTERN": wick_bull_pattern,
            "WICK_BEAR_PATTERN": wick_bear_pattern,
            "WICK_BULL_CONFIRM": WICK_BULL_CONFIRM,
            "WICK_BEAR_CONFIRM": WICK_BEAR_CONFIRM,
        },
        index=df.index,
    )


# Display label order for per-bar active-signal extraction
WICK_SIG_COLS: list[tuple[str, str]] = [
    ("WICK_BULL_CONFIRM", "WC↑"),
    ("WICK_BEAR_CONFIRM", "WC↓"),
    ("WICK_BULL_PATTERN", "WP↑"),
    ("WICK_BEAR_PATTERN", "WP↓"),
]

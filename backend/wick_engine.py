"""
wick_engine.py — 2-Candle Opposite-Wick Reversal (Pine Script 3112_2C).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from indicators import atr as _atr_hlc, bars_since as _bars_since_ind


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

    atr = _atr_hlc(h, l, c, atr_len)

    range_ok     = combo_range >= atr * min_atr_mul
    combo_signal = range_ok & each_body_ok & opposite_wicks

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


def compute_wick_x(df: pd.DataFrame, wick_mult: float = 2.0) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    o = df["open"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    mt = 1e-10

    def upper_wick(h_, o_, c_):
        return h_ - np.maximum(o_, c_)

    def lower_wick(o_, c_, l_):
        return np.minimum(o_, c_) - l_

    uw0 = upper_wick(h, o, c)
    lw0 = lower_wick(o, c, l)

    h1 = np.roll(h, 1); o1 = np.roll(o, 1); c1 = np.roll(c, 1); l1 = np.roll(l, 1)
    uw1 = upper_wick(h1, o1, c1)
    lw1 = lower_wick(o1, c1, l1)

    h2 = np.roll(h, 2); o2 = np.roll(o, 2); c2 = np.roll(c, 2); l2 = np.roll(l, 2)
    uw2 = upper_wick(h2, o2, c2)
    lw2 = lower_wick(o2, c2, l2)

    lw1_safe     = np.maximum(lw1, mt)
    wc_prev      = (lw1 == 0) | (uw1 >= wick_mult * lw1_safe)
    uw1_safe     = np.maximum(uw1, mt)
    wc_prev_bull = (uw1 == 0) | (lw1 >= wick_mult * uw1_safe)
    lw2_safe     = np.maximum(lw2, mt)
    wc_prev2     = (lw2 == 0) | (uw2 >= wick_mult * lw2_safe)
    uw0_safe     = np.maximum(uw0, mt)
    wc_curr      = (uw0 == 0) | (lw0 >= wick_mult * uw0_safe)

    is_bull  = c  > o
    is_bull1 = c1 > o1
    is_bear1 = c1 < o1

    x2g_raw = is_bull1 & (o >= o1) & (o >  c1) & (c > c1) & is_bull
    x2_raw  = is_bull1 & (o >= o1) & (o <= c1) & (c > c1) & is_bull
    x1g_raw = is_bear1 & (o > c1)  & (o > o1)  & (c > o1) & is_bull
    x1_raw  = is_bear1 & (o >= c1) & (o1 >= o) & (c > o1) & is_bull

    x2g_wick_all = ((x2g_raw | x2_raw) & wc_prev & wc_curr) | (x2g_raw & wc_prev_bull & wc_curr)
    x1x_wick_all = (x1g_raw | x1_raw) & wc_prev
    x3_wick      = wc_curr & (wc_prev | wc_prev2) & is_bull

    x2g_wick = x2g_wick_all &  x2g_raw
    x2_wick  = x2g_wick_all & ~x2g_raw
    x1g_wick = x1x_wick_all &  x1g_raw
    x1_wick  = x1x_wick_all & ~x1g_raw

    for arr in (x2g_wick, x2_wick, x1g_wick, x1_wick, x3_wick):
        arr[:2] = False

    out = pd.DataFrame(index=df.index)
    out["x2g_wick"] = x2g_wick.astype(int)
    out["x2_wick"]  = x2_wick.astype(int)
    out["x1g_wick"] = x1g_wick.astype(int)
    out["x1_wick"]  = x1_wick.astype(int)
    out["x3_wick"]  = (x3_wick & ~x2g_wick_all & ~x1x_wick_all).astype(int)
    return out
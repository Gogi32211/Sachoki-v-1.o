"""
ultra_engine.py — 260308+L88 and 260315 ULTRA v2 signal engines.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from indicators import atr as _atr_hlc


def compute_260308_l88(
    df: pd.DataFrame,
    vol_mult: float = 2.0,
    delta_mult: float = 1.5,
) -> pd.DataFrame:
    c = df["close"]
    o = df["open"]
    v = df["volume"] if "volume" in df.columns else pd.Series(1.0, index=df.index)

    vol_prev   = v.shift(1).fillna(0.0)
    vol_higher = v > vol_prev
    vol_jump   = (vol_prev > 0) & (v >= vol_prev * vol_mult)
    bull_cand  = c > o

    prev_delta = (c - o).shift(1).abs()
    curr_delta = (c - o).abs()
    delta_ok   = curr_delta >= prev_delta * delta_mult

    sig_260308 = vol_higher & vol_jump & bull_cand & delta_ok

    try:
        from wlnbb_engine import compute_wlnbb
        wl   = compute_wlnbb(df)
        l34  = wl["L34"].astype(bool)
        l43  = wl["L43"].astype(bool)
        l_ctx = l34 | l43 | l34.shift(1).fillna(False) | l43.shift(1).fillna(False)
    except Exception:
        l_ctx = pd.Series(False, index=df.index)

    sig_l88 = sig_260308 & l_ctx

    return pd.DataFrame({
        "sig_260308": sig_260308.astype(bool),
        "sig_l88":    sig_l88.astype(bool),
    }, index=df.index)


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return _atr_hlc(df["high"], df["low"], df["close"], n)


def compute_ultra_v2(
    df: pd.DataFrame,
    eb_body_mult: float = 1.5,
    eb_wick_ratio: float = 0.25,
    eb_lookback: int = 5,
    fbo_lookback: int = 10,
    vol_len: int = 20,
    hi_vol_mult: float = 1.6,
    lo_vol_mult: float = 0.80,
    atr_len: int = 14,
    narrow_mult: float = 1.0,
    clv_high: float = 0.70,
    clv_low: float = 0.30,
    vsa_test_n: int = 3,
) -> pd.DataFrame:
    c, o, h, l = df["close"], df["open"], df["high"], df["low"]
    v = df["volume"] if "volume" in df.columns else pd.Series(1.0, index=df.index)

    curr_body  = (c - o).abs()
    avg_body   = (c - o).abs().shift(1).rolling(eb_lookback, min_periods=1).mean()
    curr_top   = c.where(c >= o, o)
    curr_bot   = c.where(c <= o, o)
    upper_wick = h - curr_top
    lower_wick = curr_bot - l
    total_wick = upper_wick + lower_wick
    big_body   = curr_body >= avg_body * eb_body_mult
    little_tail = total_wick <= curr_body.replace(0, np.nan) * eb_wick_ratio
    little_tail = little_tail.fillna(False)
    eb_raw  = big_body & little_tail
    eb_bull = eb_raw & (c > o)
    eb_bear = eb_raw & (c < o)

    n_bar_high = h.shift(1).rolling(fbo_lookback, min_periods=1).max()
    n_bar_low  = l.shift(1).rolling(fbo_lookback, min_periods=1).min()
    fbo_bear = (h > n_bar_high) & (c < n_bar_high) & (c < o)
    fbo_bull = (l < n_bar_low)  & (c > n_bar_low)  & (c > o)

    bf_buy  = (c > h.shift(1)) & (c > h.shift(3))
    bf_sell = (c < l.shift(1)) & (c < l.shift(3))

    inside_bar = (h <= h.shift(1)) & (l >= l.shift(1))

    vol_ma = v.rolling(vol_len, min_periods=1).mean()
    hi_vol = v >= vol_ma * hi_vol_mult
    lo_vol = v <= vol_ma * lo_vol_mult
    spread = h - l
    atrv   = _atr(df, atr_len)
    narrow = (spread > 0) & (spread < atrv * narrow_mult)
    clv    = (c - l) / spread.replace(0, np.nan)

    up_bar = c > c.shift(1)
    dn_bar = c < c.shift(1)

    sq = hi_vol & narrow
    ns = lo_vol & (narrow | inside_bar) & dn_bar & (clv >= clv_high)
    nd = lo_vol & (narrow | inside_bar) & up_bar  & (clv <= clv_low)

    effort_recent = sq.shift(1).fillna(False) | sq.shift(2).fillna(False)
    lo_quiet      = lo_vol & (narrow | inside_bar)
    test_recent   = (ns | nd | lo_quiet).rolling(vsa_test_n, min_periods=1).max().astype(bool)
    h2_roll = h.shift(1).rolling(2, min_periods=1).max()
    l2_roll = l.shift(1).rolling(2, min_periods=1).min()
    confirm_up = (c > h2_roll) & (clv >= 0.55)
    confirm_dn = (c < l2_roll) & (clv <= 0.45)
    sig3_up = effort_recent & test_recent & confirm_up
    sig3_dn = effort_recent & test_recent & confirm_dn

    best_long  = fbo_bull & bf_buy
    best_short = fbo_bear & bf_sell

    return pd.DataFrame({
        "eb_bull":    eb_bull.astype(bool),
        "eb_bear":    eb_bear.astype(bool),
        "fbo_bull":   fbo_bull.astype(bool),
        "fbo_bear":   fbo_bear.astype(bool),
        "bf_buy":     bf_buy.astype(bool),
        "bf_sell":    bf_sell.astype(bool),
        "ultra_sq":   sq.astype(bool),
        "ultra_ns":   ns.astype(bool),
        "ultra_nd":   nd.astype(bool),
        "ultra_3up":  sig3_up.astype(bool),
        "ultra_3dn":  sig3_dn.astype(bool),
        "best_long":  best_long.astype(bool),
        "best_short": best_short.astype(bool),
    }, index=df.index)

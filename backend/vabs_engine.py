"""
vabs_engine.py — Volume Absorption & Breakout signals.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from indicators import atr as _atr_hlc, rma

_MA_PERIOD       = 20
_MIN_JUMP        = 2
_BREAK_BARS      = 10
_NS_LOOKBACK     = 5
_VOL_LEN         = 20
_MA_SPIKE        = 1.30
_Z_SCORE_NEED    = 0.70
_DELTA_ABS_NEED  = 0.40
_LOW_BASE_RATIO  = 3.0
_MID_BASE_RATIO  = 1.6
_HIGH_BASE_RATIO = 1.20
_MIN_VOL_SCORE   = 2
_ATR_LOAD_LEN    = 14
_MAX_SPREAD_ATR  = 1.50
_MAX_MOVE_PCT    = 12.0
_MIN_CLV         = 0.30
_PREV_QUIET_MULT = 0.95
_LOAD_COOLDOWN   = 2
_NARROW_MULT     = 0.8
_WIDE_MULT       = 1.5
_LOW_VOL_MULT    = 0.9
_HIGH_VOL_MULT   = 2.0
_SQ_VOL_MULT     = 1.6
_SQ_NARROW_MULT  = 1.0
_CLV_ND_MAX      = 0.55
_CLV_NS_MIN      = 0.45
_CLV_BC_MIN      = 0.80
_CLV_SC_MAX      = 0.20
_ATR_WYK_LEN     = 14
_CLIMB_MIN_END   = 2


def _atr(h: pd.Series, l: pd.Series, c: pd.Series, n: int) -> pd.Series:
    return _atr_hlc(h, l, c, n)


def compute_vabs(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    h = df["high"]
    l = df["low"]
    v = df["volume"] if "volume" in df.columns else pd.Series(1.0, index=df.index)
    o = df["open"]
    n = len(df)

    vol_mid   = v.rolling(_MA_PERIOD, min_periods=1).mean()
    vol_std   = v.rolling(_MA_PERIOD, min_periods=1).std().fillna(0)
    vol_upper = vol_mid + vol_std
    vol_lower = (vol_mid - vol_std).clip(lower=0)

    vv = v.values
    vm = vol_mid.values
    vs = vol_std.values
    vu = (vm + vs)
    vl = np.maximum(vm - vs, 0)

    bkt = np.where(vv < vl, 0,
          np.where(vv < vm, 1,
          np.where(vv < vu, 2,
          np.where(vv < (vu + vm), 3, 4))))
    bkt = pd.Series(bkt.astype(np.int8), index=df.index)

    prev_bkt = bkt.shift(1).fillna(0).astype(np.int8)
    prev2_bkt = bkt.shift(2).fillna(0).astype(np.int8)

    same_bucket  = bkt == prev_bkt
    vol_up_raw   = v > v.shift(1)
    vol_dn_raw   = v < v.shift(1)
    vol_up_adap  = (bkt > prev_bkt) | (same_bucket & vol_up_raw)
    vol_dn_adap  = (bkt < prev_bkt) | (same_bucket & vol_dn_raw)

    abs_sig = (bkt.astype(int) - prev_bkt.astype(int)) >= _MIN_JUMP

    climb_sig = (
        (bkt > prev_bkt) &
        (prev_bkt > prev2_bkt) &
        (bkt >= _CLIMB_MIN_END)
    )

    up_close   = c > c.shift(1)
    dn_close   = c < c.shift(1)
    no_new_hi  = c <= h.shift(1)

    l3 = vol_up_adap & up_close
    l4 = vol_up_adap & no_new_hi
    l6 = vol_up_adap & dn_close

    l34_vabs = l3 & l4 & (c >= o)
    l43_vabs = l6 & l4 & (c > o)

    vol_ma  = rma(v, _VOL_LEN)
    vol_std_z = v.rolling(_VOL_LEN, min_periods=2).std().fillna(0.001)
    vol_z   = (v - vol_ma) / vol_std_z.replace(0, np.nan)

    prev_vol = v.shift(1)
    delta_vol = v - prev_vol
    delta_pct = delta_vol / prev_vol.replace(0, np.nan)

    atr_load   = _atr(h, l, c, _ATR_LOAD_LEN)
    spread     = h - l
    spread_atr = spread / atr_load.replace(0, np.nan)
    move_pct   = (c - c.shift(1)).abs() / c.shift(1).replace(0, np.nan) * 100.0
    clv_load   = spread.replace(0, np.nan)
    clv_load   = ((c - l) / clv_load).fillna(0.5)

    vol_ma_prev = vol_ma.shift(1)
    base_low = prev_vol < vol_ma_prev * 0.6
    base_mid = (prev_vol >= vol_ma_prev * 0.6) & (prev_vol < vol_ma_prev * 1.2)
    ratio_need = np.where(base_low.values, _LOW_BASE_RATIO,
                 np.where(base_mid.values, _MID_BASE_RATIO, _HIGH_BASE_RATIO))
    ratio_need = pd.Series(ratio_need, index=df.index)

    ratio_ok    = (prev_vol > 0) & (v >= prev_vol * ratio_need)
    ma_ok       = v >= vol_ma * _MA_SPIKE
    z_ok        = vol_z >= _Z_SCORE_NEED
    delta_abs_ok = delta_vol >= vol_ma * _DELTA_ABS_NEED

    vol_score = (
        ratio_ok.astype(int) + ma_ok.astype(int) +
        z_ok.astype(int) + delta_abs_ok.astype(int)
    )
    vol_explosion = vol_score >= _MIN_VOL_SCORE

    prev_quiet = prev_vol <= vol_ma_prev * _PREV_QUIET_MULT
    inside_bar = (h <= h.shift(1)) & (l >= l.shift(1))
    context_ok = prev_quiet | inside_bar | (delta_pct >= 0.30) | (vol_z >= 0.80)

    small_move  = move_pct <= _MAX_MOVE_PCT
    tight_spr   = spread_atr <= _MAX_SPREAD_ATR
    good_close  = clv_load >= _MIN_CLV

    load_base = context_ok & vol_explosion & small_move & tight_spr & good_close

    lb_arr = load_base.values.astype(bool)
    ls_arr = np.zeros(n, dtype=bool)
    last_load = -_LOAD_COOLDOWN - 1
    for i in range(n):
        if lb_arr[i] and (i - last_load > _LOAD_COOLDOWN):
            ls_arr[i] = True
            last_load = i
    load_sig = pd.Series(ls_arr, index=df.index)

    vol_ma_wyk = v.rolling(_MA_PERIOD, min_periods=1).mean()
    atr_wyk    = _atr(h, l, c, _ATR_WYK_LEN)
    spread_w   = h - l
    clv_w      = ((c - l) / spread_w.replace(0, np.nan)).fillna(0.5)
    is_up      = c > c.shift(1)
    is_dn      = c < c.shift(1)

    is_narrow = spread_w < atr_wyk * _NARROW_MULT
    is_wide   = spread_w > atr_wyk * _WIDE_MULT
    is_low_v  = v < vol_ma_wyk * _LOW_VOL_MULT
    is_high_v = v > vol_ma_wyk * _HIGH_VOL_MULT
    is_sq_v   = v >= vol_ma_wyk * _SQ_VOL_MULT
    is_sq_n   = spread_w < atr_wyk * _SQ_NARROW_MULT

    ns = is_narrow & is_low_v  & is_dn & (clv_w >= _CLV_NS_MIN)
    nd = is_narrow & is_low_v  & is_up & (clv_w <= _CLV_ND_MAX)
    bc = is_wide   & is_high_v & is_up & (clv_w >= _CLV_BC_MIN)
    sc = is_wide   & is_high_v & is_dn & (clv_w <= _CLV_SC_MAX)
    sq = is_sq_v   & is_sq_n

    any_vol    = abs_sig | climb_sig | load_sig

    ns_recent  = ns.rolling(_NS_LOOKBACK, min_periods=1).max().astype(bool)
    sq_recent  = sq.rolling(_NS_LOOKBACK, min_periods=1).max().astype(bool)
    l34_recent = l34_vabs.rolling(_NS_LOOKBACK, min_periods=1).max().astype(bool)

    best_sig   = any_vol & (ns_recent | sq_recent) & l34_recent
    strong_sig = (abs_sig & load_sig) | (abs_sig & climb_sig) | (climb_sig & load_sig)

    h_arr = h.values
    l_arr = l.values
    c_arr = c.values
    trig  = any_vol.values.astype(bool)

    vbo_up_arr = np.zeros(n, dtype=bool)
    vbo_dn_arr = np.zeros(n, dtype=bool)
    cur_h = np.nan
    cur_l = np.nan
    cur_bar = -1000

    for i in range(n):
        if trig[i]:
            cur_h = h_arr[i]
            cur_l = l_arr[i]
            cur_bar = i
        if not np.isnan(cur_h) and i > cur_bar:
            since = i - cur_bar
            if since <= _BREAK_BARS:
                if c_arr[i - 1] <= cur_h and c_arr[i] > cur_h:
                    vbo_up_arr[i] = True
                if c_arr[i - 1] >= cur_l and c_arr[i] < cur_l:
                    vbo_dn_arr[i] = True
            else:
                cur_h = np.nan
                cur_l = np.nan
                cur_bar = -1000

    vbo_up = pd.Series(vbo_up_arr, index=df.index)
    vbo_dn = pd.Series(vbo_dn_arr, index=df.index)

    return pd.DataFrame({
        "vol_bucket_vabs": bkt,
        "vol_up_adap":  vol_up_adap,
        "vol_dn_adap":  vol_dn_adap,
        "l34_vabs":     l34_vabs,
        "l43_vabs":     l43_vabs,
        "abs_sig":      abs_sig,
        "climb_sig":    climb_sig,
        "load_sig":     load_sig,
        "ns":           ns,
        "nd":           nd,
        "sc":           sc,
        "bc":           bc,
        "sq":           sq,
        "best_sig":     best_sig,
        "strong_sig":   strong_sig,
        "vbo_up":       vbo_up,
        "vbo_dn":       vbo_dn,
    }, index=df.index)
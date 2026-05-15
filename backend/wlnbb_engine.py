"""
wlnbb_engine.py — Volume Bollinger Bands L-signal engine.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from indicators import norm_ohlcv, rsi as _rsi_ind, cci as _cci_ind, ffill_when, cooldown

_BB_PERIOD   = 20
_BB_STD      = 1
_RSI_PERIOD  = 14
_CCI_PERIOD  = 20
_CCI_SMA     = 14
_BLUE_Z      = 1.1
_BLUE_FLAT   = 5.0
_PP_WINDOW   = 20
_PP_MIN      = 2
_PP_COOL     = 6


def compute_wlnbb(df: pd.DataFrame) -> pd.DataFrame:
    df = norm_ohlcv(df)
    o = df["open"]
    h = df["high"]
    l = df["low"]
    c = df["close"]
    v = df["volume"]

    vol_mid   = v.rolling(_BB_PERIOD, min_periods=1).mean()
    vol_std   = v.rolling(_BB_PERIOD, min_periods=1).std().fillna(0)
    vol_upper = vol_mid + _BB_STD * vol_std
    vol_lower = (vol_mid - _BB_STD * vol_std).clip(lower=0)

    bkt = np.zeros(len(v), dtype=np.int8)
    bkt = np.where(v.values >= vol_lower.values, 1, bkt)
    bkt = np.where(v.values >= vol_mid.values,   2, bkt)
    bkt = np.where(v.values >= vol_upper.values, 3, bkt)
    bkt = np.where(v.values >= (vol_upper + vol_mid).values, 4, bkt)
    bucket = pd.Series(bkt.astype(np.int8), index=df.index)

    pv = v.shift(1)
    vol_up_adapted   = (v > pv).fillna(False)
    vol_down_adapted = (v < pv).fillna(False)

    up_close   = c > c.shift(1)
    down_close = c < c.shift(1)
    no_new_high = c <= h.shift(1)
    no_new_low  = c >= l.shift(1)

    L1 = vol_down_adapted & up_close
    L2 = vol_down_adapted & no_new_low
    L3 = vol_up_adapted   & up_close
    L4 = vol_up_adapted   & no_new_high
    L5 = vol_down_adapted & down_close
    L6 = vol_up_adapted   & down_close

    L34  = L3 & L4 & (c >= o)
    L22  = L3 & L4 & (c <  o)
    L64  = L6 & L4
    L43  = L6 & L4 & (c >  o)
    L1L2 = L1 & L2
    L2L5 = L2 & L5
    L555 = L5 & L5.shift(1).fillna(False) & L5.shift(2).fillna(False)
    ONLY_L2L4 = L2 & L4 & ~L1 & ~L3 & ~L5 & ~L6

    l_combo = _build_l_combo(L1, L2, L3, L4, L5, L6)

    rsi = _rsi_ind(c, _RSI_PERIOD, fillna_val=50)

    vol_z = ((v - vol_mid) / vol_std.replace(0, np.nan)).fillna(0)

    rsi_range3 = (rsi.rolling(3, min_periods=1).max()
                  - rsi.rolling(3, min_periods=1).min())
    BLUE  = (vol_z >= _BLUE_Z) & (rsi_range3 <= _BLUE_FLAT)
    FRI34 = BLUE & L34
    FRI43 = BLUE & L43
    FRI64 = BLUE & L64
    UI    = (BLUE.astype(int).rolling(10, min_periods=1).sum() >= 2)

    rsi_roll_max = rsi.rolling(50, min_periods=1).max().shift(1)
    rsi_roll_min = rsi.rolling(50, min_periods=1).min().shift(1)
    FUCHSIA_RH = (rsi >= rsi_roll_max.fillna(rsi)) & ~vol_up_adapted
    FUCHSIA_RL = (rsi <= rsi_roll_min.fillna(rsi)) & ~vol_up_adapted

    cci     = _cci_ind(h, l, c, _CCI_PERIOD)
    cci_sma = cci.rolling(_CCI_SMA, min_periods=1).mean()
    cci_rng6 = (cci_sma.rolling(6, min_periods=1).max()
                - cci_sma.rolling(6, min_periods=1).min())
    CCI_READY = (
        (cci_sma >= -110) & (cci_sma <= -50)
        & (cci_rng6 <= 25)
        & (cci_sma.diff() > 0)
        & (c > o)
    )
    CCI_0_RETEST_OK = (
        (cci_sma >= -15) & (cci_sma <= 30)
        & (cci_sma.diff() > 0)
        & (cci_sma.shift(1).fillna(-100.0) < 0)
    )
    CCI_BLUE_TURN = BLUE & (cci_sma.diff() > 0) & (cci_sma < 0)

    avg_rng = (h - l).rolling(20, min_periods=1).mean()
    avg_vol = v.rolling(20, min_periods=1).mean()
    rng     = h - l
    mid_px  = (h + l) / 2.0

    squat    = (rng < avg_rng * 0.7) & (v > avg_vol * 1.5)
    nosupply = (rng < avg_rng * 0.7) & (v < avg_vol * 0.7) & (c > mid_px)
    nod      = (rng < avg_rng * 0.7) & (v < avg_vol * 0.7) & (c <= mid_px)
    climax   = (rng > avg_rng * 1.5) & (v > avg_vol * 2.0)

    vsa_hits = (squat | nosupply | nod | climax).astype(int)
    vsa_sum  = vsa_hits.rolling(_PP_WINDOW, min_periods=1).sum()
    PRE_PUMP = cooldown(vsa_sum >= _PP_MIN, _PP_COOL)

    l34_hi = ffill_when(h, L34)
    l34_lo = ffill_when(l, L34)
    l43_hi = ffill_when(h, L43)
    l43_lo = ffill_when(l, L43)

    prev_above_l34 = (c.shift(1) > l34_hi.shift(1)).fillna(False)
    prev_below_l34 = (c.shift(1) < l34_lo.shift(1)).fillna(False)
    prev_above_l43 = (c.shift(1) > l43_hi.shift(1)).fillna(False)
    prev_below_l43 = (c.shift(1) < l43_lo.shift(1)).fillna(False)

    BO_UP = (c > l34_hi) & ~prev_above_l34 & ~L34 & (l34_hi > 0)
    BO_DN = (c < l34_lo) & ~prev_below_l34 & ~L34 & (l34_lo > 0)
    BX_UP = (c > l43_hi) & ~prev_above_l43 & ~L43 & (l43_hi > 0)
    BX_DN = (c < l43_lo) & ~prev_below_l43 & ~L43 & (l43_lo > 0)

    # ── BE (Breakout + full-body Engulf) ─────────────────────────────────────
    # Tracks the BODY (open/close) of the last setup candle, not wick.
    # Setup source: last L34 or L22 bar for BE_UP_BO / BE_DN_BO
    #               last L43 bar         for BE_UP_BX / BE_DN_BX
    # BE fires when the current candle opens beyond the setup body low/high
    # AND closes beyond the setup body high/low in one move (full engulf).
    body_hi = pd.concat([o, c], axis=1).max(axis=1)
    body_lo = pd.concat([o, c], axis=1).min(axis=1)

    # BO setup: last L34 (bullish body) or L22 (bearish body)
    setup_event    = L34 | L22
    setup_body_hi  = ffill_when(body_hi, setup_event)
    setup_body_lo  = ffill_when(body_lo, setup_event)

    prev_above_setup = (c.shift(1) > setup_body_hi.shift(1)).fillna(False)
    prev_below_setup = (c.shift(1) < setup_body_lo.shift(1)).fillna(False)

    bo_break_up = (c > setup_body_hi) & ~prev_above_setup & ~setup_event & (setup_body_hi > 0) & (c > o)
    bo_break_dn = (c < setup_body_lo) & ~prev_below_setup & ~setup_event & (setup_body_lo > 0) & (c < o)
    BE_UP_BO = bo_break_up & (o <= setup_body_lo)
    BE_DN_BO = bo_break_dn & (o >= setup_body_hi)

    # BX setup: last L43 (c > o so body_hi = close, body_lo = open)
    bx_body_hi = ffill_when(body_hi, L43)
    bx_body_lo = ffill_when(body_lo, L43)

    prev_above_bx = (c.shift(1) > bx_body_hi.shift(1)).fillna(False)
    prev_below_bx = (c.shift(1) < bx_body_lo.shift(1)).fillna(False)

    bx_break_up = (c > bx_body_hi) & ~prev_above_bx & ~L43 & (bx_body_hi > 0) & (c > o)
    bx_break_dn = (c < bx_body_lo) & ~prev_below_bx & ~L43 & (bx_body_lo > 0) & (c < o)
    BE_UP_BX = bx_break_up & (o <= bx_body_lo)
    BE_DN_BX = bx_break_dn & (o >= bx_body_hi)

    BE_UP = BE_UP_BO | BE_UP_BX
    BE_DN = BE_DN_BO | BE_DN_BX

    _BKT = {0: "W", 1: "L", 2: "N", 3: "B", 4: "VB"}
    vol_bucket = bucket.map(_BKT)

    candle_dir = pd.Series(
        np.where(c > o, "U", np.where(c < o, "D", "O")),
        index=df.index,
    )

    return pd.DataFrame({
        "vol_bucket":      vol_bucket,
        "vol_up_adapted":  vol_up_adapted,
        "vol_down_adapted": vol_down_adapted,
        "vol_zscore":      vol_z.round(2),
        "rsi":             rsi.round(2),
        "cci_sma":         cci_sma.round(2),
        "L1": L1, "L2": L2, "L3": L3, "L4": L4, "L5": L5, "L6": L6,
        "L34": L34, "L43": L43, "L64": L64, "L22": L22,
        "L1L2": L1L2, "L2L5": L2L5,
        "L555": L555, "ONLY_L2L4": ONLY_L2L4,
        "l_combo": l_combo,
        "BLUE": BLUE, "FRI34": FRI34, "FRI43": FRI43, "FRI64": FRI64, "UI": UI,
        "FUCHSIA_RH": FUCHSIA_RH, "FUCHSIA_RL": FUCHSIA_RL,
        "PRE_PUMP": PRE_PUMP,
        "CCI_READY": CCI_READY,
        "CCI_0_RETEST_OK": CCI_0_RETEST_OK,
        "CCI_BLUE_TURN":   CCI_BLUE_TURN,
        "BO_UP": BO_UP, "BO_DN": BO_DN,
        "BX_UP": BX_UP, "BX_DN": BX_DN,
        "BE_UP": BE_UP, "BE_DN": BE_DN,
        "candle_dir": candle_dir,
    }, index=df.index)


def score_last_bar(sig_id: int, wlnbb: pd.DataFrame) -> tuple[int, int]:
    if wlnbb.empty:
        return 0, 0

    last  = wlnbb.iloc[-1]
    last3 = wlnbb.tail(3)

    bull = 0
    bear = 0

    if sig_id in (6, 8):
        bull += 2
    elif 1 <= sig_id <= 11:
        bull += 1
    elif sig_id in (17, 19):
        bear += 2
    elif 12 <= sig_id <= 25:
        bear += 1

    if bool(last.get("L34")):      bull += 2
    if bool(last.get("L43")):      bull += 1
    if bool(last.get("CCI_READY")): bull += 2
    if bool(last.get("BLUE")):     bull += 1
    if bool(last.get("FRI34")):    bull += 2
    if bool(last.get("BO_UP")):    bull += 1
    if bool(last.get("BX_UP")):    bull += 1
    if bool(last.get("UI")):       bull += 1
    if bool(last.get("L1L2")):     bull += 1

    if last3["PRE_PUMP"].any():    bull += 1

    if bool(last.get("L22")):        bear += 2
    if bool(last.get("L64")):        bear += 1
    if bool(last.get("FUCHSIA_RH")): bear += 1
    if bool(last.get("BO_DN")):      bear += 1
    if bool(last.get("BX_DN")):      bear += 1

    return min(bull, 10), min(bear, 10)


def score_bars(sig_id_series: pd.Series, wlnbb: pd.DataFrame) -> pd.DataFrame:
    n = len(wlnbb)
    bulls = np.zeros(n, dtype=np.int32)
    bears = np.zeros(n, dtype=np.int32)

    sig_arr = sig_id_series.reindex(wlnbb.index).fillna(0).values.astype(np.int32)

    t4_t6 = (sig_arr == 6) | (sig_arr == 8)
    any_t  = (sig_arr >= 1) & (sig_arr <= 11)
    z4_z6  = (sig_arr == 17) | (sig_arr == 19)
    any_z  = (sig_arr >= 12) & (sig_arr <= 25)

    bulls += np.where(t4_t6, 2, np.where(any_t, 1, 0))
    bears += np.where(z4_z6, 2, np.where(any_z, 1, 0))

    for col, pts in [
        ("L34", 2), ("L43", 1), ("CCI_READY", 2), ("BLUE", 1),
        ("FRI34", 2), ("BO_UP", 1), ("BX_UP", 1), ("UI", 1), ("L1L2", 1),
    ]:
        if col in wlnbb.columns:
            bulls += wlnbb[col].values.astype(bool).astype(np.int32) * pts

    if "PRE_PUMP" in wlnbb.columns:
        pp_roll = wlnbb["PRE_PUMP"].rolling(3, min_periods=1).max()
        bulls += pp_roll.values.astype(bool).astype(np.int32)

    for col, pts in [
        ("L22", 2), ("L64", 1), ("FUCHSIA_RH", 1), ("BO_DN", 1), ("BX_DN", 1),
    ]:
        if col in wlnbb.columns:
            bears += wlnbb[col].values.astype(bool).astype(np.int32) * pts

    bulls = np.clip(bulls, 0, 10).astype(np.int8)
    bears = np.clip(bears, 0, 10).astype(np.int8)

    return pd.DataFrame(
        {"bull_score": bulls, "bear_score": bears},
        index=wlnbb.index,
    )


def l_signal_label(last_row: pd.Series) -> str:
    for name in ("FRI34", "L34", "L43", "L64", "L22",
                 "CCI_READY", "BLUE", "L1L2", "L2L5",
                 "BE_UP", "BE_DN", "BO_UP", "BO_DN", "BX_UP", "BX_DN", "PRE_PUMP",
                 "L3", "L1", "L2", "L4", "L6", "L5"):
        if bool(last_row.get(name)):
            return name
    return ""


def _build_l_combo(L1, L2, L3, L4, L5, L6) -> pd.Series:
    labels = ["L1", "L2", "L3", "L4", "L5", "L6"]
    arrs   = [L1.values, L2.values, L3.values, L4.values, L5.values, L6.values]
    mat    = np.column_stack(arrs)
    result = []
    for row in mat:
        active = [lbl for lbl, v in zip(labels, row) if v]
        result.append("|".join(active) if active else "NONE")
    return pd.Series(result, index=L1.index, dtype=object)
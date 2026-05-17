"""
chart_combo_engine.py — Phase 8F-A: port of root backend/combo_engine.py.

260323 Pine Script combo signals for Super Chart History timeline (I row).
No yfinance. No imports from old root backend. No signal_engine dependency.
Only compute_combo() is ported — compute_tz_state() is not needed here.
Verbatim formula port — only import paths changed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .chart_indicators import (
    rsi as _rsi, atr as _atr, macd as _macd_ind,
    crossover as _crossover, psar as _psar,
    apply_cooldown as _apply_cooldown,
    ema as _ema, bollinger_bands as _bb,
)


def compute_combo(df: pd.DataFrame) -> pd.DataFrame:
    close  = df["close"]
    open_  = df["open"]
    high   = df["high"]
    low    = df["low"]
    volume = (
        df["volume"].fillna(0)
        if "volume" in df.columns
        else pd.Series(0.0, index=df.index)
    )

    ema9  = _ema(close, 9)
    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)
    ema89 = _ema(close, 89)

    avg_vol = volume.rolling(20, min_periods=1).mean().replace(0, np.nan)
    rsi14   = _rsi(close, 14)
    atr14   = _atr(high, low, close, 14)

    bb_basis, bb_upper, _ = _bb(close, 20, 2.0)

    prev_close = close.shift(1).replace(0, np.nan)

    trend_up  = (ema9 > ema20) & (ema20 > ema50)
    roc_40    = (close - close.shift(40)) / close.shift(40).replace(0, np.nan) * 100.0
    vol_max40 = volume.rolling(40, min_periods=10).max()
    upmove_ok = trend_up & (roc_40 >= 8.0) & (vol_max40 > avg_vol * 1.4)

    um_2809 = upmove_ok & ~upmove_ok.shift(1).fillna(False)

    cons_high = high.shift(1).rolling(6, min_periods=6).max()
    cons_low  = low.shift(1).rolling(6, min_periods=6).min()

    cons_range_pct = (cons_high - cons_low) / prev_close * 100.0
    tight_by_range = cons_range_pct <= 3.5

    atr_pct      = atr14 / prev_close * 100.0
    tight_by_atr = atr_pct <= 3.0

    ema_gap_pct  = (ema9.shift(1) - ema20.shift(1)).abs() / ema20.shift(1).replace(0, np.nan) * 100.0
    tight_by_ema = ema_gap_pct <= 2.0

    tight_gate = tight_by_range | tight_by_atr | tight_by_ema
    conso_2809 = tight_gate & ~tight_gate.shift(1).fillna(False)

    bar_range = high - low
    nr_min    = bar_range.rolling(7, min_periods=4).min().shift(1)
    nr7_prev  = bar_range.shift(1) <= nr_min + 1e-10
    nr_pct_ok = (bar_range.shift(1) / prev_close * 100.0) <= 5.0
    alt_gate  = nr7_prev & nr_pct_ok

    brk_cons  = (high > cons_high) & (volume > avg_vol * 1.2)
    brk_nr    = (high > high.shift(1)) & (volume > avg_vol * 1.2)
    break_gate = (tight_gate & brk_cons) | (alt_gate & brk_nr)

    buy_raw  = upmove_ok & break_gate
    buy_2809 = _apply_cooldown(buy_raw, 6)

    vol_ratio = volume / avg_vol
    svs_2809  = _crossover(vol_ratio, 1.4) & (close > open_)

    atr_ma14  = atr14.rolling(14, min_periods=1).mean().replace(0, np.nan)
    cons_atr  = atr14 < atr_ma14 * 0.80

    _, _, macd_hist = _macd_ind(close)

    score = pd.Series(
        np.where((ema9 > ema20) & (ema20 > ema50),  2.0,
        np.where((ema9 < ema20) & (ema20 < ema50), -2.0, 0.0)),
        index=df.index,
    )
    score = score + np.where(close > bb_basis,  1.0, -1.0)
    score = score + np.where(rsi14 > 50,        1.0, -1.0)
    score = score + np.where(macd_hist > 0,     1.0, -1.0)

    bias_up   = cons_atr & (score >= 2)
    bias_down = cons_atr & (score <= -2)

    atr10          = _atr(high, low, close, 10)
    consol_atr     = atr10 < atr10.shift(1) * 0.6
    avg_vol20      = volume.rolling(20, min_periods=1).mean().replace(0, np.nan)
    highest_prev10 = high.shift(1).rolling(10, min_periods=1).max()
    brk_conf_atr   = (close > highest_prev10) & (close.shift(1) <= highest_prev10.shift(1))
    vol_ratio_atr  = volume / avg_vol20
    vol_conf_atr   = _crossover(vol_ratio_atr, 2.0)
    atr_brk        = consol_atr & brk_conf_atr & vol_conf_atr

    vol_ma_boll    = volume.rolling(20, min_periods=1).mean().replace(0, np.nan)
    vol_ratio_boll = volume / vol_ma_boll
    vol_conf_boll  = _crossover(vol_ratio_boll, 1.5)
    bb_brk         = (close > bb_upper) & vol_conf_boll & (rsi14 > 55)

    rsi2      = _rsi(close, 2)
    hilo_buy  = _crossover(rsi2, 20.0)
    hilo_sell = (rsi2.shift(1) > 80) & (rsi2 < 80)

    rsi_buy_rtv = _crossover(rsi2, 20.0)
    bearish_rtv = (close.shift(1) < open_.shift(1)) | (close.shift(2) < open_.shift(2))
    reversal    = close > open_

    roll22_max = close.rolling(22, min_periods=1).max().replace(0, np.nan)
    wvf        = (roll22_max - low) / roll22_max * 100.0
    wvf_mid    = wvf.rolling(20, min_periods=1).mean()
    wvf_upper  = wvf_mid + wvf.rolling(20, min_periods=1).std() * 2.0
    wvf_rng_hi = wvf.rolling(50, min_periods=1).max() * 0.85
    vix_fired  = (wvf >= wvf_upper) | (wvf >= wvf_rng_hi)
    vix_conf   = vix_fired | vix_fired.shift(1).fillna(False)
    rtv        = rsi_buy_rtv & bearish_rtv & reversal & vix_conf

    cx9  = (open_ < ema9)  & (close > ema9)
    cx20 = (open_ < ema20) & (close > ema20)
    cx50 = (open_ < ema50) & (close > ema50)
    cx89 = (open_ < ema89) & (close > ema89)

    preup3  = cx9 & cx20 & cx50
    preup2  = cx9 & cx20 & ~preup3
    preup50 = cx50 & ~cx9 & ~cx20
    preup89 = cx89

    prev_below_all3  = (close.shift(1) < ema9.shift(1)) & \
                       (close.shift(1) < ema20.shift(1)) & \
                       (close.shift(1) < ema50.shift(1))
    curr_open_above  = (open_ > ema9) & (open_ > ema20) & (open_ > ema50)
    curr_close_above = (close > ema9) & (close > ema20) & (close > ema50)
    sig3g            = prev_below_all3 & curr_open_above & curr_close_above

    psar_arr    = _psar(high.to_numpy(), low.to_numpy())
    psar_s      = pd.Series(psar_arr, index=df.index)
    strong_bull = (close - open_) / open_.replace(0, np.nan) * 100.0 > 2.0
    vol_burst   = volume > avg_vol * 2.0
    rocket      = buy_2809 & strong_bull & vol_burst & (close > psar_s)

    out = pd.DataFrame({
        "rocket":     rocket,
        "buy_2809":   buy_2809,
        "sig3g":      sig3g,
        "bb_brk":     bb_brk,
        "atr_brk":    atr_brk,
        "rtv":        rtv,
        "preup3":     preup3,
        "preup2":     preup2,
        "preup50":    preup50,
        "preup89":    preup89,
        "hilo_buy":   hilo_buy,
        "hilo_sell":  hilo_sell,
        "bias_up":    bias_up,
        "bias_down":  bias_down,
        "cons_atr":   cons_atr,
        "um_2809":    um_2809,
        "svs_2809":   svs_2809,
        "conso_2809": conso_2809,
    }, index=df.index)

    return out.fillna(False)


# Display label order — priority matches old COMBO_SIGNAL_PRIORITY
# Columns whose labels go to the Z row (PREUP family)
COMBO_PREUP_COLS: frozenset[str] = frozenset({"preup3", "preup2", "preup50", "preup89"})

COMBO_SIG_COLS: list[tuple[str, str]] = [
    ("rocket",     "ROCKET"),
    ("buy_2809",   "BUY"),
    ("sig3g",      "3G"),
    ("bb_brk",     "BB↑"),
    ("atr_brk",    "ATR↑"),
    ("rtv",        "RTV"),
    ("preup3",     "P3"),
    ("preup2",     "P2"),
    ("preup50",    "P50"),
    ("preup89",    "P89"),
    ("hilo_buy",   "HILO↑"),
    ("hilo_sell",  "HILO↓"),
    ("bias_up",    "↑BIAS"),
    ("bias_down",  "↓BIAS"),
    ("cons_atr",   "CONS"),
    ("um_2809",    "UM"),
    ("svs_2809",   "SVS"),
    ("conso_2809", "CONSO"),
]

"""
wyckoff_engine.py — Wyckoff Accumulation & Distribution state machines.

Ported from Pine Script 260225.

Accumulation states:
  0 = Idle / searching for SC
  1 = SC  (Selling Climax)
  2 = AR  (Automatic Rally)
  3 = ST  (Secondary Test)
  4 = Spring (bear trap)
  5 = SOS / JAC (Sign of Strength / Jump Across Creek — breakout)
  6 = LPS (Last Point of Support)

Distribution states:
  0 = Idle / searching for BC
  1 = BC   (Buying Climax)
  2 = ARD  (Automatic Reaction)
  3 = STD  (Secondary Test distribution)
  4 = UTAD (Upthrust After Distribution)
  5 = SOW  (Sign of Weakness — breakdown)
  6 = LPSY (Last Point of Supply)

Exposed column names (all boolean 0/1 unless noted):
  Accum: wyk_sc, wyk_ar, wyk_st, wyk_spring, wyk_sos, wyk_lps
         wyk_accum  (state 1-4 active)  wyk_markup (state 5-6)
  Dist:  wyk_bc, wyk_ard, wyk_std, wyk_utad, wyk_sow, wyk_lpsy
         wyk_dist   (state 1-4 active)  wyk_markdown (state 5-6)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ── Parameters (mirroring Pine Script 260225 defaults) ────────────────────────
_PIVOT_LEN      = 6
_CYCLE_MAX      = 160      # bars before a stalled cycle is reset
_EMA_FAST       = 14
_EMA_SLOW       = 50
_HI_VOL_MULT    = 1.8      # volume > vol_ma * this → "high volume"
_LO_VOL_MULT    = 0.8      # volume < vol_ma * this → "low volume"
_WIDE_SPREAD    = 1.2      # spread > atr * this → "wide spread"
_SPRING_ATR     = 0.6      # spring: dip below SC low allowed within atr * this
_RECLAIM_ATR    = 0.3      # spring must reclaim SC low within atr * this
_BREAK_BUF      = 0.05     # SOS: close above AR high by atr * this
_LPS_TOL        = 0.7      # LPS: can dip to support - atr * this
_CLOSE_LOW_FRAC = 0.35     # SC: close must be within bottom 35% of bar
_CLOSE_HIGH_FRAC= 0.65     # BC: close must be within top 35% of bar (above this)


def _atr14(df: pd.DataFrame) -> np.ndarray:
    hi = df["high"].values
    lo = df["low"].values
    cl = df["close"].values
    prev_cl = np.concatenate([[cl[0]], cl[:-1]])
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - prev_cl), np.abs(lo - prev_cl)))
    return pd.Series(tr).ewm(span=14, adjust=False).mean().values


def _vol_ma20(df: pd.DataFrame) -> np.ndarray:
    return df["volume"].astype(float).rolling(20, min_periods=10).mean().values


def _ema(series: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(series).ewm(span=span, adjust=False).mean().values


# ── Accumulation ──────────────────────────────────────────────────────────────
def compute_wyckoff_accum(df: pd.DataFrame) -> pd.DataFrame:
    """
    Wyckoff Accumulation state machine.
    Returns a DataFrame indexed like df with columns:
      wyk_sc, wyk_ar, wyk_st, wyk_spring, wyk_sos, wyk_lps,
      wyk_accum (state 1-4), wyk_markup (state 5-6)
    """
    n = len(df)
    if n < 60:
        cols = ["wyk_sc","wyk_ar","wyk_st","wyk_spring","wyk_sos","wyk_lps",
                "wyk_accum","wyk_markup"]
        return pd.DataFrame(0, index=df.index, columns=cols)

    op = df["open"].values.astype(float)
    hi = df["high"].values.astype(float)
    lo = df["low"].values.astype(float)
    cl = df["close"].values.astype(float)
    vol = df["volume"].values.astype(float)

    atr     = _atr14(df)
    vol_ma  = _vol_ma20(df)
    ema_f   = _ema(cl, _EMA_FAST)
    ema_s   = _ema(cl, _EMA_SLOW)

    # output arrays
    wyk_sc     = np.zeros(n, dtype=np.int8)
    wyk_ar     = np.zeros(n, dtype=np.int8)
    wyk_st     = np.zeros(n, dtype=np.int8)
    wyk_spring = np.zeros(n, dtype=np.int8)
    wyk_sos    = np.zeros(n, dtype=np.int8)
    wyk_lps    = np.zeros(n, dtype=np.int8)
    wyk_state  = np.zeros(n, dtype=np.int8)  # 0-6 carried forward

    state       = 0
    sc_low      = 0.0
    sc_high     = 0.0
    ar_high     = 0.0
    cycle_start = 0

    for i in range(_PIVOT_LEN, n):
        a   = atr[i]    if atr[i] > 0    else 0.001
        vm  = vol_ma[i] if vol_ma[i] > 0 else 1.0
        spread   = hi[i] - lo[i]
        is_down  = cl[i] < op[i]
        is_up    = cl[i] > op[i]
        hi_vol   = vol[i] > vm * _HI_VOL_MULT
        lo_vol   = vol[i] < vm * _LO_VOL_MULT
        wide_bar = spread > a * _WIDE_SPREAD

        # auto-reset stalled cycle
        if state > 0 and (i - cycle_start) > _CYCLE_MAX:
            state = 0

        # ── State 0: find Selling Climax ──────────────────────────────────
        if state == 0:
            # SC: down-bar, high volume, wide spread, downtrend, close near low
            close_pos = (cl[i] - lo[i]) / spread if spread > 0 else 1.0
            if (is_down
                    and hi_vol
                    and wide_bar
                    and ema_f[i] < ema_s[i]        # downtrend context
                    and close_pos < _CLOSE_LOW_FRAC):
                state       = 1
                sc_low      = lo[i]
                sc_high     = hi[i]
                ar_high     = hi[i]
                cycle_start = i
                wyk_sc[i]   = 1

        # ── State 1: SC found — wait for Automatic Rally ──────────────────
        elif state == 1:
            # AR: up bar rallying above SC high
            if is_up and cl[i] > sc_high:
                ar_high   = max(ar_high, hi[i])
                state     = 2
                wyk_ar[i] = 1
            else:
                ar_high = max(ar_high, hi[i])
                # extend SC low if another climax bar
                if hi_vol and is_down and lo[i] < sc_low:
                    sc_low = lo[i]

        # ── State 2: AR done — wait for Secondary Test ────────────────────
        elif state == 2:
            ar_high = max(ar_high, hi[i])
            # ST: price pulls back into SC zone with lower-than-SC volume
            in_sc_zone = lo[i] <= sc_high + a * 0.5
            above_sc   = lo[i] > sc_low - a * 0.5
            if in_sc_zone and above_sc and not hi_vol:
                state     = 3
                wyk_st[i] = 1

        # ── State 3: ST done — watch for Spring or direct SOS ─────────────
        elif state == 3:
            # Spring: briefly dips below SC low but closes above it
            dipped_below = lo[i] < sc_low
            recovered    = cl[i] > sc_low - a * _RECLAIM_ATR
            if dipped_below and recovered:
                state        = 4
                wyk_spring[i]= 1

            # SOS/JAC: strong close above AR high (skip spring path)
            elif cl[i] > ar_high + a * _BREAK_BUF and not lo_vol:
                state      = 5
                wyk_sos[i] = 1

        # ── State 4: Spring found — wait for SOS/JAC ─────────────────────
        elif state == 4:
            if cl[i] > ar_high + a * _BREAK_BUF and not lo_vol:
                state      = 5
                wyk_sos[i] = 1

        # ── State 5: SOS done — watch for LPS ────────────────────────────
        elif state == 5:
            # LPS: dip back toward support, holds, low volume
            near_support = lo[i] > sc_low - a * _LPS_TOL
            some_dip     = lo[i] < cl[i - 1] - a * 0.05 if i > 0 else False
            if near_support and some_dip and lo_vol:
                state      = 6
                wyk_lps[i] = 1

        # ── State 6: LPS — cycle complete, reset ─────────────────────────
        elif state == 6:
            state = 0   # ready for next accumulation cycle

        wyk_state[i] = state

    result = pd.DataFrame(index=df.index)
    result["wyk_sc"]     = wyk_sc
    result["wyk_ar"]     = wyk_ar
    result["wyk_st"]     = wyk_st
    result["wyk_spring"] = wyk_spring
    result["wyk_sos"]    = wyk_sos
    result["wyk_lps"]    = wyk_lps
    # active = in accumulation range (pre-breakout)
    result["wyk_accum"]  = ((wyk_state >= 1) & (wyk_state <= 4)).astype(int)
    # markup = breakout phase
    result["wyk_markup"] = ((wyk_state >= 5) & (wyk_state <= 6)).astype(int)
    return result


# ── Distribution ──────────────────────────────────────────────────────────────
def compute_wyckoff_dist(df: pd.DataFrame) -> pd.DataFrame:
    """
    Wyckoff Distribution state machine.
    Returns a DataFrame indexed like df with columns:
      wyk_bc, wyk_ard, wyk_std, wyk_utad, wyk_sow, wyk_lpsy,
      wyk_dist (state 1-4), wyk_markdown (state 5-6)
    """
    n = len(df)
    if n < 60:
        cols = ["wyk_bc","wyk_ard","wyk_std","wyk_utad","wyk_sow","wyk_lpsy",
                "wyk_dist","wyk_markdown"]
        return pd.DataFrame(0, index=df.index, columns=cols)

    op = df["open"].values.astype(float)
    hi = df["high"].values.astype(float)
    lo = df["low"].values.astype(float)
    cl = df["close"].values.astype(float)
    vol = df["volume"].values.astype(float)

    atr    = _atr14(df)
    vol_ma = _vol_ma20(df)
    ema_f  = _ema(cl, _EMA_FAST)
    ema_s  = _ema(cl, _EMA_SLOW)

    wyk_bc       = np.zeros(n, dtype=np.int8)
    wyk_ard      = np.zeros(n, dtype=np.int8)
    wyk_std      = np.zeros(n, dtype=np.int8)
    wyk_utad     = np.zeros(n, dtype=np.int8)
    wyk_sow      = np.zeros(n, dtype=np.int8)
    wyk_lpsy     = np.zeros(n, dtype=np.int8)
    wyk_state    = np.zeros(n, dtype=np.int8)

    state       = 0
    bc_high     = 0.0
    bc_low      = 0.0
    ard_low     = 0.0
    cycle_start = 0

    for i in range(_PIVOT_LEN, n):
        a   = atr[i]    if atr[i] > 0    else 0.001
        vm  = vol_ma[i] if vol_ma[i] > 0 else 1.0
        spread   = hi[i] - lo[i]
        is_up    = cl[i] > op[i]
        is_down  = cl[i] < op[i]
        hi_vol   = vol[i] > vm * _HI_VOL_MULT
        lo_vol   = vol[i] < vm * _LO_VOL_MULT
        wide_bar = spread > a * _WIDE_SPREAD

        if state > 0 and (i - cycle_start) > _CYCLE_MAX:
            state = 0

        # ── State 0: find Buying Climax ───────────────────────────────────
        if state == 0:
            # BC: up-bar, high volume, wide spread, uptrend, close near high
            close_pos = (cl[i] - lo[i]) / spread if spread > 0 else 0.0
            if (is_up
                    and hi_vol
                    and wide_bar
                    and ema_f[i] > ema_s[i]         # uptrend context
                    and close_pos > _CLOSE_HIGH_FRAC):
                state       = 1
                bc_high     = hi[i]
                bc_low      = lo[i]
                ard_low     = lo[i]
                cycle_start = i
                wyk_bc[i]   = 1

        # ── State 1: BC found — wait for Automatic Reaction ──────────────
        elif state == 1:
            if is_down and cl[i] < bc_low:
                ard_low    = min(ard_low, lo[i])
                state      = 2
                wyk_ard[i] = 1
            else:
                bc_high = max(bc_high, hi[i])
                # extend BC high if another climax bar
                if hi_vol and is_up and hi[i] > bc_high:
                    bc_high = hi[i]

        # ── State 2: ARD done — wait for Secondary Test ───────────────────
        elif state == 2:
            ard_low = min(ard_low, lo[i])
            # STD: rally back toward BC zone with lower volume
            in_bc_zone = hi[i] >= bc_low - a * 0.5
            below_bc   = hi[i] < bc_high + a * 0.5
            if in_bc_zone and below_bc and not hi_vol:
                state      = 3
                wyk_std[i] = 1

        # ── State 3: STD done — watch for UTAD or direct SOW ─────────────
        elif state == 3:
            # UTAD: briefly pushes above BC high but closes back below it
            pushed_above = hi[i] > bc_high
            closed_below = cl[i] < bc_high + a * _RECLAIM_ATR
            if pushed_above and closed_below:
                state       = 4
                wyk_utad[i] = 1

            # SOW: strong close below ARD low (skip UTAD path)
            elif cl[i] < ard_low - a * _BREAK_BUF and not lo_vol:
                state      = 5
                wyk_sow[i] = 1

        # ── State 4: UTAD found — wait for SOW ───────────────────────────
        elif state == 4:
            if cl[i] < ard_low - a * _BREAK_BUF and not lo_vol:
                state      = 5
                wyk_sow[i] = 1

        # ── State 5: SOW done — watch for LPSY ───────────────────────────
        elif state == 5:
            # LPSY: minor rally toward resistance, fails, low volume
            near_resist = hi[i] < bc_high + a * _LPS_TOL
            some_rally  = hi[i] > cl[i - 1] + a * 0.05 if i > 0 else False
            if near_resist and some_rally and lo_vol:
                state       = 6
                wyk_lpsy[i] = 1

        # ── State 6: LPSY — cycle complete, reset ─────────────────────────
        elif state == 6:
            state = 0

        wyk_state[i] = state

    result = pd.DataFrame(index=df.index)
    result["wyk_bc"]       = wyk_bc
    result["wyk_ard"]      = wyk_ard
    result["wyk_std"]      = wyk_std
    result["wyk_utad"]     = wyk_utad
    result["wyk_sow"]      = wyk_sow
    result["wyk_lpsy"]     = wyk_lpsy
    result["wyk_dist"]     = ((wyk_state >= 1) & (wyk_state <= 4)).astype(int)
    result["wyk_markdown"] = ((wyk_state >= 5) & (wyk_state <= 6)).astype(int)
    return result

"""
cisd_engine.py — Change in State of Delivery sequences (Pine Script 250115_CISD).

Tracks +CISD / -CISD events (market structure shifts) and detects 4 sequence patterns:
  CISD_SEQ  : ++--   (2× +CISD then 2× -CISD)  → green circle in original
  CISD_PPM  : ++-    (2× +CISD then -CISD)      → green triangle
  CISD_MPM  : -+-    (-CISD, +CISD, -CISD)      → red triangle
  CISD_PMM  : +--    (+CISD then 2× -CISD)      → magenta triangle
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_cisd(df: pd.DataFrame) -> pd.DataFrame:
    """
    Translates 250115_CISD Pine Script logic to Python.
    Input df must have lowercase open/high/low/close columns.
    Returns DataFrame (same index) with boolean signal columns.
    """
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    o = df["open"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    n = len(df)

    plus_arr  = np.zeros(n, dtype=bool)
    minus_arr = np.zeros(n, dtype=bool)

    # ── Market structure state ────────────────────────────────────────────
    top_price    = 0.0
    bottom_price = 0.0

    # Pullback tracking
    is_bull_pb   = False   # bullish pullback active
    is_bear_pb   = False   # bearish pullback active
    pot_top      = 0.0
    pot_bottom   = 0.0
    bull_brk_idx = 0
    bear_brk_idx = 0

    # Active level memory (simplified to single level each)
    bu_price    = 0.0
    bu_active   = False   # -CISD level waiting to complete
    be_price    = 0.0
    be_active   = False   # +CISD level waiting to complete

    for i in range(1, n):
        plus_ev  = False
        minus_ev = False

        prev_bull = c[i - 1] > o[i - 1]
        prev_bear = c[i - 1] < o[i - 1]

        # ── Pullback detection on previous bar ────────────────────────────
        if prev_bull and not is_bear_pb:
            is_bear_pb   = True
            pot_top      = o[i - 1]
            bull_brk_idx = i - 1

        if prev_bear and not is_bull_pb:
            is_bull_pb   = True
            pot_bottom   = o[i - 1]
            bear_brk_idx = i - 1

        # ── Update potential levels during pullbacks ──────────────────────
        if is_bull_pb:
            if o[i] < pot_bottom:
                pot_bottom   = o[i]
                bear_brk_idx = i
            if c[i] < o[i] and o[i] > pot_bottom:
                pot_bottom   = o[i]
                bear_brk_idx = i

        if is_bear_pb:
            if o[i] > pot_top:
                pot_top      = o[i]
                bull_brk_idx = i
            if c[i] > o[i] and o[i] < pot_top:
                pot_top      = o[i]
                bull_brk_idx = i

        # ── Structure breaks → create CISD events ─────────────────────────
        # Bearish break (low < bottomPrice) → +CISD
        if l[i] < bottom_price:
            bottom_price = l[i]
            if is_bear_pb and (i - bull_brk_idx) != 0:
                is_bear_pb = False
                plus_ev    = True
            elif prev_bull and c[i] < o[i]:
                is_bear_pb = False
                plus_ev    = True

        # Bullish break (high > topPrice) → -CISD
        if h[i] > top_price:
            top_price = h[i]
            if is_bull_pb and (i - bear_brk_idx) != 0:
                is_bull_pb = False
                minus_ev   = True
            elif prev_bear and c[i] > o[i]:
                is_bull_pb = False
                minus_ev   = True

        # ── Level completions ─────────────────────────────────────────────
        # -CISD level completed when close < bu_price → creates +CISD
        if bu_active and bu_price > 0 and c[i] < bu_price:
            bu_active = False
            plus_ev   = True

        # +CISD level completed when close > be_price → creates -CISD
        if be_active and be_price > 0 and c[i] > be_price:
            be_active = False
            minus_ev  = True

        # ── Store new levels ──────────────────────────────────────────────
        if plus_ev and pot_top > 0:
            be_price  = pot_top
            be_active = True

        if minus_ev and pot_bottom > 0:
            bu_price  = pot_bottom
            bu_active = True

        plus_arr[i]  = plus_ev
        minus_arr[i] = minus_ev

    # ── Sequence state machines ───────────────────────────────────────────
    seq_arr = np.zeros(n, dtype=bool)
    ppm_arr = np.zeros(n, dtype=bool)
    mpm_arr = np.zeros(n, dtype=bool)
    pmm_arr = np.zeros(n, dtype=bool)

    seq_st = ppm_st = mpm_st = pmm_st = 0

    for i in range(n):
        p = plus_arr[i]
        m = minus_arr[i]

        if not (p or m):
            continue

        # CISD_SEQ: ++ then -- (states 0→1→2→3→signal)
        if p:
            seq_st = 1 if seq_st in (0,) else (2 if seq_st == 1 else 1)
        if m:
            if seq_st == 2:
                seq_st = 3
            elif seq_st == 3:
                seq_arr[i] = True
                seq_st = 0
            else:
                seq_st = 0

        # CISD_PPM: ++-
        if p:
            ppm_st = 1 if ppm_st == 0 else (2 if ppm_st == 1 else 1)
        if m:
            if ppm_st == 2:
                ppm_arr[i] = True
                ppm_st = 0
            else:
                ppm_st = 0

        # CISD_MPM: -+-
        if m:
            if mpm_st == 0:
                mpm_st = 1
            elif mpm_st == 2:
                mpm_arr[i] = True
                mpm_st = 0
            else:
                mpm_st = 1
        if p:
            mpm_st = 2 if mpm_st == 1 else 0

        # CISD_PMM: +--
        if p:
            pmm_st = 1
        if m:
            if pmm_st == 1:
                pmm_st = 2
            elif pmm_st == 2:
                pmm_arr[i] = True
                pmm_st = 0
            else:
                pmm_st = 0

    return pd.DataFrame(
        {
            "PLUS_CISD":  plus_arr,
            "MINUS_CISD": minus_arr,
            "CISD_SEQ":   seq_arr,
            "CISD_PPM":   ppm_arr,
            "CISD_MPM":   mpm_arr,
            "CISD_PMM":   pmm_arr,
        },
        index=df.index,
    )

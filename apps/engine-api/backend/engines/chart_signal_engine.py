"""
chart_signal_engine.py — Phase 8C: T/Z signal engine for scanner-api chart endpoints.

Port of root backend/signal_engine.py. Uses Massive-fetched OHLCV DataFrames.
No yfinance. No imports from old root backend.

Signal IDs
----------
Bullish (T):  0=NONE 1=T1G 2=T1 3=T2G 4=T2 5=T3 6=T4 7=T5 8=T6 9=T9 10=T10 11=T11 12=T12
Bearish (Z):  13=Z1G 14=Z1 15=Z2G 16=Z2 17=Z3 18=Z4 19=Z5 20=Z6
              21=Z7(doji) 22=Z9 23=Z10 24=Z11 25=Z12

Priority bullish (highest wins):  T4 > T6 > T1G > T2G > T1 > T2 > T9 > T10 > T3 > T11 > T5 > T12
Priority bearish (highest wins):  Z4 > Z6 > Z1G > Z2G > Z1 > Z2 > Z9 > Z10 > Z3 > Z11 > Z5 > Z12 > Z7
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from .chart_indicators import norm_ohlcv as _norm_ohlcv

# ---------------------------------------------------------------------------
# Signal ID constants
# ---------------------------------------------------------------------------
NONE = 0
T1G, T1, T2G, T2, T3, T4, T5, T6, T9, T10, T11, T12 = 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12
Z1G, Z1, Z2G, Z2, Z3, Z4, Z5, Z6 = 13, 14, 15, 16, 17, 18, 19, 20
Z7, Z9, Z10, Z11, Z12 = 21, 22, 23, 24, 25

SIG_NAMES: dict[int, str] = {
    0: "NONE",
    1: "T1G", 2: "T1", 3: "T2G", 4: "T2", 5: "T3", 6: "T4", 7: "T5", 8: "T6",
    9: "T9", 10: "T10", 11: "T11", 12: "T12",
    13: "Z1G", 14: "Z1", 15: "Z2G", 16: "Z2", 17: "Z3", 18: "Z4", 19: "Z5", 20: "Z6",
    21: "Z7", 22: "Z9", 23: "Z10", 24: "Z11", 25: "Z12",
}

BULLISH_SIGS = frozenset(range(1, 13))
BEARISH_SIGS = frozenset(range(13, 26))

_BC_TO_SID = {1: 6, 2: 8, 3: 1, 4: 3, 5: 2, 6: 4, 7: 9, 8: 10, 9: 5, 10: 11, 11: 7, 12: 12}
_ZC_TO_SID = {1: 18, 2: 20, 3: 13, 4: 15, 5: 14, 6: 16, 7: 22,
              8: 23, 9: 17, 10: 24, 11: 19, 12: 25, 13: 21}

_BC_SID_MAP = np.zeros(13, dtype=np.int8)
for _k, _v in _BC_TO_SID.items():
    _BC_SID_MAP[_k] = _v

_ZC_SID_MAP = np.zeros(14, dtype=np.int8)
for _k, _v in _ZC_TO_SID.items():
    _ZC_SID_MAP[_k] = _v


def compute_signals(
    df: pd.DataFrame,
    use_wick: bool = False,
    min_body_ratio: float = 1.0,
    doji_thresh: float = 0.05,
) -> pd.DataFrame:
    """
    Compute T/Z signal codes for every bar from OHLCV DataFrame.

    Parameters
    ----------
    df : DataFrame with columns open, high, low, close (case-insensitive).
         Uses Massive-fetched bars — no yfinance.

    Returns
    -------
    DataFrame with columns:
        bc (int8)     - bullish priority code 0-12
        zc (int8)     - bearish priority code 0-13
        sig_id (int8) - final signal 0-25
        sig_name (str)
        is_bull (bool)
        is_bear (bool)
    """
    df = _norm_ohlcv(df)
    o = df["open"]
    h = df["high"]
    l = df["low"]
    c = df["close"]

    rng = h - l
    bdy = (c - o).abs()
    mintick = 1e-10

    isDoji = (c == o)
    isBull = c > o
    isBear = c < o

    p1Bull = (c.shift(1) > o.shift(1))
    p1Bear = (c.shift(1) < o.shift(1)) | isDoji.shift(1).fillna(False).astype(bool)

    pBody = (c.shift(1) - o.shift(1)).abs()
    pTop  = np.maximum(o.shift(1), c.shift(1))
    pBot  = np.minimum(o.shift(1), c.shift(1))

    cBody = bdy
    cTop  = np.maximum(o, c)
    cBot  = np.minimum(o, c)

    eH  = cTop
    eL  = cBot
    eP  = pTop
    ePl = pBot

    safe  = np.maximum(pBody, mintick)
    engOk = (cBody / safe >= min_body_ratio) & (eH >= eP) & (ePl >= eL)
    insOk = (cTop <= pTop) & (cBot >= pBot)

    # ── Bullish patterns ──────────────────────────────────────────────────────
    cT1G = p1Bear & (o > c.shift(1)) & (o > o.shift(1)) & (c > o.shift(1)) & isBull
    cT1  = p1Bear & (o >= c.shift(1)) & (o.shift(1) >= o) & (c > o.shift(1)) & isBull
    cT2G = p1Bull & (o >= o.shift(1)) & (o > c.shift(1)) & (c > c.shift(1)) & isBull
    cT2  = p1Bull & (o >= o.shift(1)) & (o <= c.shift(1)) & (c > c.shift(1)) & isBull
    cT3  = (p1Bear & isBull & (o < o.shift(1)) & (o < c.shift(1))
            & (c < o.shift(1)) & (c > c.shift(1)))
    cT4  = p1Bear & isBull & engOk
    cT5  = (p1Bear & isBull & (o < o.shift(1)) & (o < c.shift(1))
            & (c < o.shift(1)) & (c.shift(1) >= c))
    cT6  = p1Bull & isBull & engOk
    cT9  = p1Bear & isBull & insOk
    cT10 = p1Bull & isBull & insOk
    cT11 = p1Bull & isBull & (o < o.shift(1)) & (c >= o.shift(1)) & (c < c.shift(1))
    cT12 = p1Bull & isBull & (o < o.shift(1)) & (c < o.shift(1))

    # ── Bearish patterns ──────────────────────────────────────────────────────
    cZ1G = p1Bull & (o < c.shift(1)) & (o < o.shift(1)) & (c < o.shift(1)) & isBear
    cZ1  = p1Bull & (o <= c.shift(1)) & (o <= o.shift(1)) & (c < o.shift(1)) & isBear
    cZ2G = p1Bear & (o <= o.shift(1)) & (o < c.shift(1)) & (c < c.shift(1)) & isBear
    cZ2  = p1Bear & (o <= o.shift(1)) & (o >= c.shift(1)) & (c < c.shift(1)) & isBear
    cZ3  = (p1Bull & isBear & (o > o.shift(1)) & (o > c.shift(1))
            & (c > o.shift(1)) & (c < c.shift(1)))
    cZ4  = p1Bull & isBear & engOk
    cZ5  = (p1Bull & isBear & (o > o.shift(1)) & (o > c.shift(1))
            & (c > o.shift(1)) & (c >= c.shift(1)))
    cZ6  = p1Bear & isBear & engOk
    cZ9  = p1Bull & isBear & insOk
    cZ10 = p1Bear & isBear & insOk
    cZ11 = p1Bear & (o > o.shift(1)) & isBear & ((c > c.shift(1)) | (c > o.shift(1)))
    cZ12 = p1Bull & (o <= o.shift(1)) & isBear

    anyZ = (cZ1G | cZ1 | cZ2G | cZ2 | cZ3 | cZ4 | cZ5 | cZ6
            | cZ9 | cZ10 | cZ11 | cZ12)
    anyB = cT1G | cT1 | cT2G | cT2 | cT3 | cT4 | cT5 | cT6 | cT9 | cT10 | cT11 | cT12
    cZ7c = isDoji & ~anyB & ~anyZ

    # ── bc priority code ──────────────────────────────────────────────────────
    bc_arr = np.zeros(len(df), dtype=np.int8)
    for code, cond in [
        (1, cT4), (2, cT6), (3, cT1G), (4, cT2G), (5, cT1),
        (6, cT2), (7, cT9), (8, cT10), (9, cT3), (10, cT11), (11, cT5), (12, cT12),
    ]:
        mask = cond.fillna(False).to_numpy() & (bc_arr == 0)
        bc_arr[mask] = code

    # ── zc priority code ──────────────────────────────────────────────────────
    zc_arr = np.zeros(len(df), dtype=np.int8)
    for code, cond in [
        (1, cZ4), (2, cZ6), (3, cZ1G), (4, cZ2G), (5, cZ1),
        (6, cZ2), (7, cZ9), (8, cZ10), (9, cZ3),
        (10, cZ11), (11, cZ5), (12, cZ12), (13, cZ7c),
    ]:
        mask = cond.fillna(False).to_numpy() & (zc_arr == 0)
        zc_arr[mask] = code

    zc_arr = np.where(bc_arr > 0, np.int8(0), zc_arr).astype(np.int8)
    sid    = np.where(bc_arr > 0, _BC_SID_MAP[bc_arr], _ZC_SID_MAP[zc_arr])

    bc       = pd.Series(bc_arr, index=df.index, name="bc")
    zc       = pd.Series(zc_arr, index=df.index, name="zc")
    sig_id   = pd.Series(sid.astype(np.int8), index=df.index, name="sig_id")
    sig_name = sig_id.map(SIG_NAMES).fillna("NONE")
    is_bull  = sig_id.isin(BULLISH_SIGS)
    is_bear  = sig_id.isin(BEARISH_SIGS)

    return pd.DataFrame(
        {"bc": bc, "zc": zc, "sig_id": sig_id, "sig_name": sig_name,
         "is_bull": is_bull, "is_bear": is_bear},
        index=df.index,
    )

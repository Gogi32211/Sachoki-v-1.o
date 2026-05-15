"""
rgti_engine.py — 260404 RGTI Lower-Low Screener + 260402 SMX signals.

Multi-timeframe EMA alignment patterns (4H / 1H / 15m).
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def _ema_last(series: pd.Series, span: int) -> float:
    if len(series) < max(10, span // 4):
        return float("nan")
    return float(series.ewm(span=span, adjust=False).mean().iloc[-1])


def resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame | None:
    """Approximate 4H bars from 1H by grouping every 4 bars (count-based)."""
    if df_1h is None or len(df_1h) < 8:
        return None
    df = df_1h[["open", "high", "low", "close", "volume"]].copy().reset_index(drop=True)
    rem = len(df) % 4
    if rem:
        df = df.iloc[rem:].reset_index(drop=True)
    blk = np.arange(len(df)) // 4
    agg = df.assign(_b=blk).groupby("_b").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
    ).reset_index(drop=True)
    return agg if len(agg) >= 20 else None


def compute_rgti_smx(
    df_4h: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame,
    price_now: float,
    open_now: float,
    df_chart: "pd.DataFrame | None" = None,
) -> dict:
    """
    Compute 7 × 260404 RGTI signals + 1 × 260402 SMX signal.

    Returns dict: rgti_ll, rgti_up, rgti_upup, rgti_upupup,
                  rgti_orange, rgti_green, rgti_greencirc, smx  (0 or 1 each).
    """
    zero = dict(rgti_ll=0, rgti_up=0, rgti_upup=0, rgti_upupup=0,
                rgti_orange=0, rgti_green=0, rgti_greencirc=0, smx=0)
    try:
        # ── 4H EMAs ───────────────────────────────────────────────────────────
        c4     = df_4h["close"]
        e9_4   = _ema_last(c4, 9)
        e20_4  = _ema_last(c4, 20)
        e34_4  = _ema_last(c4, 34)
        e50_4  = _ema_last(c4, 50)
        e89_4  = _ema_last(c4, 89)
        e200_4 = _ema_last(c4, 200)
        c4h    = float(df_4h["close"].iloc[-1])
        o4h    = float(df_4h["open"].iloc[-1])

        # ── 1H EMAs ───────────────────────────────────────────────────────────
        c1     = df_1h["close"]
        e9_1   = _ema_last(c1, 9)
        e20_1  = _ema_last(c1, 20)
        e34_1  = _ema_last(c1, 34)
        e50_1  = _ema_last(c1, 50)
        e89_1  = _ema_last(c1, 89)
        e200_1 = _ema_last(c1, 200)
        c1h    = float(df_1h["close"].iloc[-1])
        o1h    = float(df_1h["open"].iloc[-1])

        # ── 15m EMAs ──────────────────────────────────────────────────────────
        c15    = df_15m["close"]
        e9_15   = _ema_last(c15, 9)
        e20_15  = _ema_last(c15, 20)
        e50_15  = _ema_last(c15, 50)
        e89_15  = _ema_last(c15, 89)
        e200_15 = _ema_last(c15, 200)
        c15m   = float(df_15m["close"].iloc[-1])

        # NaN guard
        for v in (e9_4, e20_4, e34_4, e50_4, e89_4, e200_4,
                  e9_1, e20_1, e34_1, e50_1, e89_1, e200_1,
                  e9_15, e20_15, e50_15, e89_15, e200_15):
            if v != v:  # isnan
                return zero

        p = price_now

        # ── 260404 RGTI — 4H sub-conditions ──────────────────────────────────
        c4h_rel_LL        = e50_4 < e20_4 and e20_4 > e9_4
        c4h_price_LL      = p > e50_4 and p > e20_4
        c4h_rel_UP        = e50_4 < e20_4 and e20_4 < e9_4
        c4h_price_UP      = p > e20_4 and p > e200_4
        c4h_ema9_above200 = e9_4 > e200_4
        c4h_UPUP          = e20_4 > e9_4 and e20_4 > e50_4 and e50_4 > e89_4
        c4h_rel_UPUPUP    = e200_4 > e50_4 and e50_4 < e9_4 and e20_4 > e50_4
        c4h_price_UPUPUP  = p > e50_4 and p > e9_4
        c4h_GREEN         = e34_4 > e50_4 and e50_4 > e20_4 and e20_4 > e9_4 and e9_4 > e89_4
        c4h_GREENCIRC     = e50_4 > e20_4 and e20_4 > e9_4 and p < e50_4

        # ── 260404 RGTI — 1H sub-conditions ──────────────────────────────────
        c1h_price_LL   = p > e9_1
        c1h_rel_LL     = e9_1 < e50_1 and e50_1 > e20_1 and e200_1 < e50_1
        c1h_price_UP   = p > e9_1
        c1h_rel_UP     = e9_1 > e50_1 and e50_1 < e20_1 and e200_1 < e50_1
        c1h_UPUP       = e50_1 < e89_1 and e50_1 > e34_1
        c1h_rel_UPUPUP = e9_1 > e200_1 and e200_1 > e50_1
        c1h_GREEN      = e89_1 > e50_1 and e34_1 > e20_1
        c1h_GREENCIRC  = p > e9_1 and e9_1 > e34_1 and e9_1 < e89_1

        # ── 260404 RGTI — 15m sub-conditions ─────────────────────────────────
        c15m_rel_LL      = e200_15 > e50_15
        c15m_rel_UP      = e20_15 > e9_15
        c15m_price_UP    = p > e200_15
        c15m_price_UP2   = p < e20_15
        c15m_UPUP        = e9_15 > e50_15 and e200_15 > e89_15 and e20_15 > e50_15
        c15m_rel_UPUPUP  = e200_15 > e20_15 and e9_15 < e20_15
        c15m_GREEN       = e9_15 > e20_15 and e20_15 > e89_15
        c15m_GREENCIRC   = e200_15 > e20_15 and e20_15 > e89_15

        # ── 260404 RGTI final signals ─────────────────────────────────────────
        rgti_ll = int(
            c4h_rel_LL and c4h_price_LL and
            c1h_price_LL and c1h_rel_LL and
            c15m_rel_LL
        )
        rgti_up = int(
            c4h_rel_UP and c4h_price_UP and c4h_ema9_above200 and
            c1h_price_UP and c1h_rel_UP and
            c15m_rel_UP and c15m_price_UP and c15m_price_UP2
        )
        rgti_upup = int(
            c4h_UPUP and c1h_UPUP and c15m_UPUP and c1h > o1h
        )
        rgti_upupup = int(
            c4h_rel_UPUPUP and c4h_price_UPUPUP and
            c1h_rel_UPUPUP and
            c15m_rel_UPUPUP
        )
        rgti_orange = int(
            (e200_1 > e9_1 and e9_1 > e20_1 and e20_1 > e50_1) and
            (e200_4 > e50_4 and e50_4 > e20_4 and e9_4 > e20_4) and
            (e9_15 > e20_15 and e20_15 > e50_15 and e50_15 > e200_15)
        )
        rgti_green = int(
            c4h_GREEN and c1h_GREEN and c15m_GREEN and c1h > o1h
        )
        rgti_greencirc = int(
            c4h_GREENCIRC and c1h_GREENCIRC and c15m_GREENCIRC
        )

        # ── 260402 SMX signal ─────────────────────────────────────────────────
        smx_4h_stack = e200_4 > e50_4 and e50_4 > e20_4 and e20_4 > e9_4
        smx_4h_price = c4h > e9_4
        smx_4h_bull  = c4h > o4h
        smx_1h_price = c1h > e9_1
        smx_1h_ema   = e9_1 > e20_1
        smx_1h_bull  = c1h > o1h
        # 15m: ema20 < ema9, ema200 > ema9, ema50 < ema20, close > ema50
        smx_15m = (
            e20_15 < e9_15 and e200_15 > e9_15 and
            e50_15 < e20_15 and c15m > e50_15
        )
        # Near recent low (lookback=20 bars, tolerance=3%) on chart TF
        _nl_df = df_chart if df_chart is not None else df_1h
        low_20   = float(_nl_df["low"].rolling(20, min_periods=5).min().iloc[-1])
        near_low = float(_nl_df["low"].iloc[-1]) <= low_20 * 1.03

        smx = int(
            smx_4h_stack and smx_4h_price and smx_4h_bull and
            smx_1h_price and smx_1h_ema and smx_1h_bull and
            smx_15m and near_low
        )

        return dict(
            rgti_ll=rgti_ll, rgti_up=rgti_up,
            rgti_upup=rgti_upup, rgti_upupup=rgti_upupup,
            rgti_orange=rgti_orange, rgti_green=rgti_green,
            rgti_greencirc=rgti_greencirc, smx=smx,
        )

    except Exception:
        return zero

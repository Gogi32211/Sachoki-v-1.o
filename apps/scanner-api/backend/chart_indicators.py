"""
chart_indicators.py — Phase 8C: shared technical indicator helpers for chart engines.

Verbatim port of root backend/indicators.py, adapted for scanner-api package.
No external imports beyond numpy and pandas. No yfinance.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ── DataFrame normalisation ────────────────────────────────────────────────────

def norm_ohlcv(df: pd.DataFrame, require_volume: bool = False) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    missing_ohlc = {"open", "high", "low", "close"} - set(df.columns)
    if missing_ohlc:
        raise ValueError(f"Missing OHLC columns: {missing_ohlc}")
    if not require_volume and "volume" not in df.columns:
        df["volume"] = 1.0
    return df


# ── Moving averages ────────────────────────────────────────────────────────────

def rma(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def bollinger_bands(
    series: pd.Series, period: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (basis, upper, lower)."""
    basis = series.rolling(period, min_periods=1).mean()
    std   = series.rolling(period, min_periods=1).std()
    return basis, basis + num_std * std, basis - num_std * std


# ── Oscillators ───────────────────────────────────────────────────────────────

def rsi(series: pd.Series, period: int, fillna_val: float | None = None) -> pd.Series:
    delta = series.diff()
    gain  = rma(delta.clip(lower=0), period)
    loss  = rma((-delta).clip(lower=0), period).replace(0, np.nan)
    result = 100.0 - (100.0 / (1.0 + gain / loss))
    if fillna_val is not None:
        result = result.fillna(fillna_val)
    return result


def cci(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20
) -> pd.Series:
    tp = (high + low + close) / 3.0
    ma = tp.rolling(period, min_periods=1).mean()
    md = tp.rolling(period, min_periods=1).apply(
        lambda x: np.abs(x - x.mean()).mean(), raw=True
    )
    return ((tp - ma) / (0.015 * md.replace(0, np.nan))).fillna(0)


# ── Volatility ────────────────────────────────────────────────────────────────

def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int
) -> pd.Series:
    prev_c = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_c).abs(), (low - prev_c).abs()], axis=1
    ).max(axis=1)
    return rma(tr, period)


# ── Pattern helpers ───────────────────────────────────────────────────────────

def crossover(a: pd.Series, level: float) -> pd.Series:
    return (a > level) & (a.shift(1) <= level)


def ffill_when(series: pd.Series, condition: pd.Series) -> pd.Series:
    return series.where(condition).ffill().fillna(0)


def cooldown(condition: pd.Series, n: int) -> pd.Series:
    arr = condition.values
    out = np.zeros(len(arr), dtype=bool)
    last = -(n + 1)
    for i in range(len(arr)):
        if arr[i] and (i - last) > n:
            out[i] = True
            last = i
    return pd.Series(out, index=condition.index)


def bars_since(cond: pd.Series) -> pd.Series:
    arr = cond.values
    out = np.full(len(arr), 9999, dtype=np.int32)
    last = -9999
    for i in range(len(arr)):
        if arr[i]:
            last = i
        out[i] = i - last
    return pd.Series(out, index=cond.index)

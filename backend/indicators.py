"""
indicators.py — shared technical indicator helpers.

All engine modules import from here to avoid duplicated implementations.

Public functions
----------------
norm_ohlcv(df, require_volume)  — normalise column names, optionally validate
rma(series, period)             — Wilder's Moving Average
ema(series, span)               — Exponential Moving Average
bollinger_bands(series, …)      — (basis, upper, lower) Bollinger Bands
rsi(series, period, fillna_val) — RSI (Wilder smoothing)
atr(high, low, close, period)   — Average True Range
cci(high, low, close, period)   — Commodity Channel Index
macd(close, fast, slow, signal) — MACD line / signal / histogram
crossover(a, level)             — bar where a crosses above level
psar(high, low, af_start, …)    — Parabolic SAR
ffill_when(series, condition)   — forward-fill values at condition bars
cooldown(condition, n)          — bool series: re-fires only after n-bar gap
apply_cooldown(series, n)       — suppress signals within n bars of each fire
bars_since(cond)                — bars elapsed since last True
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


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_f     = close.ewm(span=fast,          adjust=False).mean()
    ema_s     = close.ewm(span=slow,          adjust=False).mean()
    macd_line = ema_f - ema_s
    macd_sig  = macd_line.ewm(span=signal_period, adjust=False).mean()
    return macd_line, macd_sig, macd_line - macd_sig


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


def psar(
    high: np.ndarray,
    low: np.ndarray,
    af_start: float = 0.02,
    af_step: float = 0.02,
    af_max: float = 0.2,
) -> np.ndarray:
    n    = len(high)
    out  = np.empty(n, dtype=np.float64)
    bull = True
    af   = af_start
    ep   = high[0]
    out[0] = low[0]

    for i in range(1, n):
        if bull:
            out[i] = out[i - 1] + af * (ep - out[i - 1])
            out[i] = min(out[i], low[i - 1])
            if i > 1:
                out[i] = min(out[i], low[i - 2])
            if low[i] < out[i]:
                bull   = False
                out[i] = ep
                ep     = low[i]
                af     = af_start
            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + af_step, af_max)
        else:
            out[i] = out[i - 1] + af * (ep - out[i - 1])
            out[i] = max(out[i], high[i - 1])
            if i > 1:
                out[i] = max(out[i], high[i - 2])
            if high[i] > out[i]:
                bull   = True
                out[i] = ep
                ep     = high[i]
                af     = af_start
            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + af_step, af_max)
    return out


# ── Series utilities ──────────────────────────────────────────────────────────

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


def apply_cooldown(series: pd.Series, n: int) -> pd.Series:
    arr  = series.to_numpy(dtype=bool, copy=True)
    last = -(n + 1)
    for i in range(len(arr)):
        if arr[i]:
            if i - last < n:
                arr[i] = False
            else:
                last = i
    return pd.Series(arr, index=series.index)


def bars_since(cond: pd.Series) -> pd.Series:
    arr = cond.values
    out = np.full(len(arr), 9999, dtype=np.int32)
    last = -9999
    for i in range(len(arr)):
        if arr[i]:
            last = i
        out[i] = i - last
    return pd.Series(out, index=cond.index)
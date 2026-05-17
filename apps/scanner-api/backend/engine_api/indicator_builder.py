"""
indicator_builder.py — Phase 8G: shared indicator dataframe builder.

Single source of truth for all derived columns required by ported engines.
Computes once per ticker/timeframe. Engines receive the prepared dataframe
and never recompute the same indicator on their own.

Formulas are unchanged from the old indicators.py (verbatim semantics).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .chart_indicators import (
    norm_ohlcv,
    rma,
    ema,
    bollinger_bands,
    rsi as _rsi,
    atr as _atr,
    cci as _cci,
    macd as _macd,
)


# Default lengths matching old-Ultra conventions.
_RSI_LEN     = 14
_CCI_LEN     = 20
_ATR_LEN     = 14
_BB_LEN      = 20
_BB_STD      = 2.0
_VOL_MA_LEN  = 20

_EMA_SPANS = (8, 13, 20, 21, 34, 50, 89, 200)


def build_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Receive raw OHLCV; return a dataframe with all derived columns the engine
    registry needs. Does NOT mutate the input.

    Output columns (in addition to open/high/low/close/volume):
      ema8, ema13, ema20, ema21, ema34, ema50, ema89, ema200
      rsi, cci, atr
      bb_basis, bb_upper, bb_lower
      vol_ma, vol_std, vol_z, vol_ratio
      body, upper_wick, lower_wick, range, body_pct, up_pct, lo_pct
      prev_close, change, change_pct
    """
    out = norm_ohlcv(df, require_volume=False).copy()

    close = out["close"]
    high  = out["high"]
    low   = out["low"]
    open_ = out["open"]
    vol   = out["volume"]

    # EMAs
    for span in _EMA_SPANS:
        out[f"ema{span}"] = ema(close, span)

    # Oscillators
    out["rsi"] = _rsi(close, _RSI_LEN)
    out["cci"] = _cci(high, low, close, _CCI_LEN)
    out["atr"] = _atr(high, low, close, _ATR_LEN)

    # Bollinger bands
    basis, upper, lower = bollinger_bands(close, _BB_LEN, _BB_STD)
    out["bb_basis"] = basis
    out["bb_upper"] = upper
    out["bb_lower"] = lower

    # MACD (used by combo)
    macd_line, macd_signal, macd_hist = _macd(close)
    out["macd"]      = macd_line
    out["macd_sig"]  = macd_signal
    out["macd_hist"] = macd_hist

    # Volume regime
    out["vol_ma"]  = vol.rolling(_VOL_MA_LEN, min_periods=1).mean()
    out["vol_std"] = vol.rolling(_VOL_MA_LEN, min_periods=2).std().fillna(0.0)
    out["vol_z"]   = (vol - out["vol_ma"]) / out["vol_std"].replace(0, np.nan)
    out["vol_ratio"] = vol / out["vol_ma"].replace(0, np.nan)

    # Candle anatomy
    rng = (high - low)
    body = (close - open_).abs()
    upper_body = np.maximum(open_, close)
    lower_body = np.minimum(open_, close)
    upper_wick = (high - upper_body)
    lower_wick = (lower_body - low)
    out["range"]      = rng
    out["body"]       = body
    out["upper_wick"] = upper_wick
    out["lower_wick"] = lower_wick
    safe_rng = rng.where(rng > 0, other=np.nan)
    out["body_pct"] = (body / safe_rng).fillna(0.0)
    out["up_pct"]   = (upper_wick / safe_rng).fillna(0.0)
    out["lo_pct"]   = (lower_wick / safe_rng).fillna(0.0)

    # Bar-over-bar
    out["prev_close"] = close.shift(1)
    out["change"]     = close - out["prev_close"]
    out["change_pct"] = out["change"] / out["prev_close"].replace(0, np.nan) * 100.0

    return out


# Re-export rma in case downstream modules import it from here.
__all__ = ["build_indicators", "rma"]

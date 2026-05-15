"""
data.py — OHLCV fetch from yfinance with memory/Redis cache (5-min TTL).
"""
from __future__ import annotations
import os, time
from typing import Optional

import pandas as pd
import yfinance as yf

CACHE_TTL = 300  # 5 minutes

_mem: dict[str, tuple[pd.DataFrame, float]] = {}

TF_PERIOD = {
    "1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d",
    "1h": "730d", "4h": "730d", "1d": "5y", "1wk": "10y",
}


def _redis():
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    try:
        import redis
        return redis.from_url(url, decode_responses=True)
    except Exception:
        return None


def _get(key: str) -> Optional[pd.DataFrame]:
    r = _redis()
    if r:
        raw = r.get(key)
        if raw:
            try:
                return pd.read_json(raw)
            except Exception:
                pass
    if key in _mem:
        df, ts = _mem[key]
        if time.time() - ts < CACHE_TTL:
            return df
    return None


def _set(key: str, df: pd.DataFrame):
    r = _redis()
    if r:
        try:
            r.setex(key, CACHE_TTL, df.to_json(date_format="iso"))
        except Exception:
            pass
    _mem[key] = (df, time.time())


def fetch_ohlcv(ticker: str, interval: str = "1d", bars: int = 500) -> pd.DataFrame:
    """
    Fetch OHLCV data. Returns DataFrame with lowercase columns:
    open, high, low, close, volume — indexed by datetime.
    """
    ticker = ticker.upper().strip()
    key = f"{ticker}:{interval}:{bars}"

    cached = _get(key)
    if cached is not None:
        return cached

    period = TF_PERIOD.get(interval, "5y")
    try:
        raw = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    except Exception as e:
        raise RuntimeError(f"yfinance error for {ticker}: {e}")

    if raw is None or raw.empty:
        raise RuntimeError(f"No data returned for {ticker}")

    raw.columns = [str(c).lower() for c in raw.columns]

    needed = {"open", "high", "low", "close"}
    missing = needed - set(raw.columns)
    if missing:
        raise RuntimeError(f"Missing columns {missing} for {ticker}")

    cols = ["open", "high", "low", "close"] + (["volume"] if "volume" in raw.columns else [])
    df = raw[cols].dropna().tail(bars)

    if df.empty:
        raise RuntimeError(f"Empty dataframe after dropna for {ticker}")

    _set(key, df)
    return df

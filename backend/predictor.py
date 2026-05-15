"""
predictor.py — 3-bar and 2-bar next-bar signal prediction with regime split.
Also: T/Z signal frequency statistics (counts, group%, bar%) per Pine Script 260415.
"""
from __future__ import annotations
import time
from collections import Counter

import numpy as np
import pandas as pd

from signal_engine import SIG_NAMES, BULLISH_SIGS, BEARISH_SIGS

# ── T/Z signal frequency — signal ID lists ────────────────────────────────────
_T_SIGS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]   # T1G … T12
_Z_SIGS = [13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25]  # Z1G … Z12

# Benchmark stats cache: {"SPY_1d": {"stats": {...}, "ts": float}}
_bench_cache: dict = {}


def predict_next(df: pd.DataFrame, lookback: int = 5000) -> dict:
    """
    Given df with sig_id + OHLCV columns, find the last 3-bar and 2-bar patterns
    then count what signal appeared on the next bar historically.
    Also splits outcomes by market regime (EMA20 > EMA50 = bull).

    Returns
    -------
    {
      "current_regime": "bull" | "bear",
      "tz_3bar": {
        "pattern": "ZTN",
        "signals": "Z4 → T1G → NONE",
        "total_matches": 23,
        "bull_matches": 15,  "bear_matches": 8,
        "bull_bull_pct": 73, "bear_bull_pct": 50,
        "top_outcomes": [...]
      },
      "tz_2bar": { same structure }
    }
    """
    df = df.tail(lookback).reset_index(drop=True)

    if "sig_id" not in df.columns:
        return _empty()

    sigs = df["sig_id"].to_numpy(dtype=np.int8)
    n = len(sigs)

    if n < 4:
        return _empty()

    # ── Regime: bull = EMA20 > EMA50 ────────────────────────────────────────
    if "close" in df.columns:
        ema20 = df["close"].ewm(span=20, adjust=False).mean().to_numpy()
        ema50 = df["close"].ewm(span=50, adjust=False).mean().to_numpy()
        is_bull_regime = ema20 > ema50
    else:
        is_bull_regime = np.ones(n, dtype=bool)

    current_regime = "bull" if is_bull_regime[-1] else "bear"

    last3 = tuple(sigs[-3:])
    last2 = tuple(sigs[-2:])

    sig3_label = " → ".join(SIG_NAMES.get(int(s), "NONE") for s in last3)
    sig2_label = " → ".join(SIG_NAMES.get(int(s), "NONE") for s in last2)

    pat3 = "".join(_pchar(s) for s in last3)
    pat2 = "".join(_pchar(s) for s in last2)

    out3: list[int] = []; out3_bull: list[int] = []; out3_bear: list[int] = []
    out2: list[int] = []; out2_bull: list[int] = []; out2_bear: list[int] = []

    for i in range(n - 3):
        if sigs[i] == last3[0] and sigs[i+1] == last3[1] and sigs[i+2] == last3[2]:
            outcome = int(sigs[i + 3])
            out3.append(outcome)
            if is_bull_regime[i + 2]:
                out3_bull.append(outcome)
            else:
                out3_bear.append(outcome)

    for i in range(n - 2):
        if sigs[i] == last2[0] and sigs[i+1] == last2[1]:
            outcome = int(sigs[i + 2])
            out2.append(outcome)
            if is_bull_regime[i + 1]:
                out2_bull.append(outcome)
            else:
                out2_bear.append(outcome)

    return {
        "current_regime": current_regime,
        "tz_3bar": _summarize(pat3, sig3_label, out3, out3_bull, out3_bear),
        "tz_2bar": _summarize(pat2, sig2_label, out2, out2_bull, out2_bear),
    }


def _pchar(s: int) -> str:
    if s in BULLISH_SIGS:
        return "T"
    if s in BEARISH_SIGS:
        return "Z"
    return "N"


def _bull_pct(outcomes: list[int]) -> int:
    if not outcomes:
        return 0
    return round(sum(1 for s in outcomes if s in BULLISH_SIGS) / len(outcomes) * 100)


def _summarize(pattern: str, signals: str, outcomes: list[int],
               bull_out: list[int], bear_out: list[int]) -> dict:
    total = len(outcomes)
    if total == 0:
        return {"pattern": pattern, "signals": signals,
                "total_matches": 0, "top_outcomes": [],
                "bull_matches": 0, "bear_matches": 0,
                "bull_bull_pct": 0, "bear_bull_pct": 0}

    counts = Counter(outcomes)
    top = [
        {
            "sig_id": sid,
            "sig_name": SIG_NAMES.get(sid, "NONE"),
            "count": cnt,
            "pct": round(cnt / total * 100),
            "is_bull": sid in BULLISH_SIGS,
            "is_bear": sid in BEARISH_SIGS,
        }
        for sid, cnt in counts.most_common(10)
    ]
    return {
        "pattern": pattern, "signals": signals,
        "total_matches": total, "top_outcomes": top,
        "bull_matches": len(bull_out),
        "bear_matches": len(bear_out),
        "bull_bull_pct": _bull_pct(bull_out),
        "bear_bull_pct": _bull_pct(bear_out),
    }


def _empty() -> dict:
    empty = {"pattern": "", "signals": "", "total_matches": 0, "top_outcomes": [],
             "bull_matches": 0, "bear_matches": 0, "bull_bull_pct": 0, "bear_bull_pct": 0}
    return {"current_regime": "unknown", "tz_3bar": empty, "tz_2bar": empty}


# ── T/Z signal frequency statistics ──────────────────────────────────────────

def compute_tz_stats(df: pd.DataFrame, doji_thresh: float = 0.05) -> dict:
    """
    Count each T and Z signal over all bars and compute group% and bar%.

    Pine Script 260415 compatible:
      group% = count / t_total  (or z_total)
      bar%   = count / bull_bars (T), count / bear_bars (Z), count / doji_bars (Z7)
    """
    if "sig_id" not in df.columns or "close" not in df.columns:
        return _empty_stats()

    c    = df["close"].to_numpy(dtype=float)
    o    = df["open"].to_numpy(dtype=float)
    h    = df["high"].to_numpy(dtype=float)
    lw   = df["low"].to_numpy(dtype=float)
    sigs = df["sig_id"].to_numpy(dtype=np.int8)

    rng     = h - lw
    body    = np.abs(c - o)
    is_doji = c == o
    bull    = c > o
    bear    = c < o

    bull_bars  = int(bull.sum())
    bear_bars  = int(bear.sum())
    doji_bars  = int(is_doji.sum())
    total_bars = len(sigs)

    t_counts = {sid: int((sigs == sid).sum()) for sid in _T_SIGS}
    z_counts = {sid: int((sigs == sid).sum()) for sid in _Z_SIGS}

    t_total = sum(t_counts.values())
    z_total = sum(z_counts.values())

    def _pct(n: int, d: int) -> float:
        return round(n / d * 100, 1) if d > 0 else 0.0

    t_signals = [
        {
            "sig_id":    sid,
            "name":      SIG_NAMES[sid],
            "count":     t_counts[sid],
            "group_pct": _pct(t_counts[sid], t_total),
            "bar_pct":   _pct(t_counts[sid], bull_bars),
        }
        for sid in _T_SIGS
    ]

    z_signals = [
        {
            "sig_id":    sid,
            "name":      SIG_NAMES[sid],
            "count":     z_counts[sid],
            "group_pct": _pct(z_counts[sid], z_total),
            # Z7 (doji signal) uses doji_bars as denominator
            "bar_pct":   _pct(z_counts[sid], doji_bars if sid == 21 else bear_bars),
        }
        for sid in _Z_SIGS
    ]

    return {
        "total_bars": total_bars,
        "bull_bars":  bull_bars,
        "bear_bars":  bear_bars,
        "doji_bars":  doji_bars,
        "t_total":    t_total,
        "z_total":    z_total,
        "t_signals":  t_signals,
        "z_signals":  z_signals,
    }


def compute_tz_matrix(df: pd.DataFrame) -> dict:
    """
    Compute signal transition matrix for bar+1 and bar+2.

    Returns
    -------
    {
      "bar1": {"<src_id>": {"<next_id>": count, ...}, ...},
      "bar2": { same structure, 2 bars ahead }
    }
    Keys are string integers (JSON-safe).
    """
    if "sig_id" not in df.columns:
        return {"bar1": {}, "bar2": {}}

    sigs = df["sig_id"].to_numpy(dtype=np.int16)

    c1 = Counter(zip(sigs[:-1].tolist(), sigs[1:].tolist()))
    c2 = Counter(zip(sigs[:-2].tolist(), sigs[2:].tolist()))

    bar1: dict = {}
    for (s, n), cnt in c1.items():
        k = str(s)
        if k not in bar1:
            bar1[k] = {}
        bar1[k][str(n)] = cnt

    bar2: dict = {}
    for (s, n), cnt in c2.items():
        k = str(s)
        if k not in bar2:
            bar2[k] = {}
        bar2[k][str(n)] = cnt

    return {"bar1": bar1, "bar2": bar2}


def _empty_stats() -> dict:
    t = [{"sig_id": s, "name": SIG_NAMES[s], "count": 0, "group_pct": 0.0, "bar_pct": 0.0}
         for s in _T_SIGS]
    z = [{"sig_id": s, "name": SIG_NAMES[s], "count": 0, "group_pct": 0.0, "bar_pct": 0.0}
         for s in _Z_SIGS]
    return {"total_bars": 0, "bull_bars": 0, "bear_bars": 0, "doji_bars": 0,
            "t_total": 0, "z_total": 0, "t_signals": t, "z_signals": z}


_BENCH_TICKERS = {"sp500": "SPY", "nasdaq": "QQQ", "russell2k": "IWM"}

def get_bench_tz_stats(universe: str = "sp500", interval: str = "1d") -> dict:
    """
    Return T/Z frequency stats for the benchmark universe.
    Uses pooled aggregate stats (all stocks in the universe) when available —
    falls back to the index ETF (SPY/QQQ/IWM) if not yet built.
    """
    # Primary: pooled aggregate across all universe stocks
    try:
        from pooled_stats import get_pooled_tz_freq
        pooled = get_pooled_tz_freq(universe, interval)
        if pooled:
            return pooled
    except Exception:
        pass

    # Fallback: single benchmark ETF, cached for 24 h
    bench = _BENCH_TICKERS.get(universe, "SPY")
    cache_key = f"{bench}_{interval}"
    cached = _bench_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < 86400:
        return cached["stats"]

    try:
        from data import fetch_ohlcv
        from signal_engine import compute_signals
        df   = fetch_ohlcv(bench, interval=interval, bars=5000)
        sigs = compute_signals(df)
        full = df.join(sigs)
        stats = compute_tz_stats(full)
        stats["bench_ticker"] = bench
        _bench_cache[cache_key] = {"stats": stats, "ts": time.time()}
        return stats
    except Exception:
        s = _empty_stats()
        s["bench_ticker"] = bench
        return s

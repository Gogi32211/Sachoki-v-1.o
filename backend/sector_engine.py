"""
sector_engine.py — U.S. Sector ETF analysis engine.
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np
import pandas as pd

from data import fetch_ohlcv

# ── Universe ───────────────────────────────────────────────────────────────────
SECTORS: dict[str, str] = {
    "XLC":  "Communication Services",
    "XLY":  "Consumer Discretionary",
    "XLP":  "Consumer Staples",
    "XLE":  "Energy",
    "XLF":  "Financials",
    "XLV":  "Health Care",
    "XLI":  "Industrials",
    "XLB":  "Materials",
    "XLRE": "Real Estate",
    "XLK":  "Technology",
    "XLU":  "Utilities",
}

BENCHMARKS: dict[str, str] = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "IWM": "Russell 2000",
    "DIA": "Dow Jones",
}

GROWTH    = {"XLK", "XLC", "XLY", "XLI"}
DEFENSIVE = {"XLU", "XLP", "XLV", "XLRE"}

# ── Static holdings ────────────────────────────────────────────────────────────
HOLDINGS: dict[str, list[dict]] = {
    "XLC":  [
        {"symbol": "META",  "name": "Meta Platforms",      "weight": 23.1},
        {"symbol": "GOOGL", "name": "Alphabet A",          "weight": 12.5},
        {"symbol": "GOOG",  "name": "Alphabet C",          "weight": 10.8},
        {"symbol": "NFLX",  "name": "Netflix",             "weight":  4.9},
        {"symbol": "T",     "name": "AT&T",                "weight":  4.5},
        {"symbol": "TMUS",  "name": "T-Mobile US",         "weight":  4.2},
    ],
    "XLY":  [
        {"symbol": "AMZN",  "name": "Amazon",              "weight": 24.2},
        {"symbol": "TSLA",  "name": "Tesla",               "weight": 14.3},
        {"symbol": "HD",    "name": "Home Depot",          "weight":  8.1},
        {"symbol": "MCD",   "name": "McDonald's",          "weight":  4.2},
        {"symbol": "NKE",   "name": "Nike",                "weight":  3.1},
        {"symbol": "LOW",   "name": "Lowe's",              "weight":  3.0},
    ],
    "XLP":  [
        {"symbol": "PG",    "name": "Procter & Gamble",    "weight": 16.2},
        {"symbol": "KO",    "name": "Coca-Cola",           "weight": 10.1},
        {"symbol": "PEP",   "name": "PepsiCo",             "weight":  9.8},
        {"symbol": "WMT",   "name": "Walmart",             "weight":  9.2},
        {"symbol": "COST",  "name": "Costco",              "weight":  8.7},
        {"symbol": "PM",    "name": "Philip Morris",       "weight":  5.3},
    ],
    "XLE":  [
        {"symbol": "XOM",   "name": "Exxon Mobil",         "weight": 22.3},
        {"symbol": "CVX",   "name": "Chevron",             "weight": 14.8},
        {"symbol": "COP",   "name": "ConocoPhillips",      "weight":  8.1},
        {"symbol": "EOG",   "name": "EOG Resources",       "weight":  4.7},
        {"symbol": "SLB",   "name": "Schlumberger",        "weight":  4.3},
        {"symbol": "MPC",   "name": "Marathon Petroleum",  "weight":  4.1},
    ],
    "XLF":  [
        {"symbol": "BRK-B", "name": "Berkshire Hathaway",  "weight": 13.1},
        {"symbol": "JPM",   "name": "JPMorgan Chase",      "weight": 12.7},
        {"symbol": "V",     "name": "Visa",                "weight":  8.9},
        {"symbol": "MA",    "name": "Mastercard",          "weight":  6.2},
        {"symbol": "BAC",   "name": "Bank of America",     "weight":  4.8},
        {"symbol": "GS",    "name": "Goldman Sachs",       "weight":  3.9},
    ],
    "XLV":  [
        {"symbol": "LLY",   "name": "Eli Lilly",           "weight": 12.4},
        {"symbol": "UNH",   "name": "UnitedHealth",        "weight":  9.8},
        {"symbol": "JNJ",   "name": "Johnson & Johnson",   "weight":  7.1},
        {"symbol": "ABBV",  "name": "AbbVie",              "weight":  6.5},
        {"symbol": "MRK",   "name": "Merck",               "weight":  5.9},
        {"symbol": "TMO",   "name": "Thermo Fisher",       "weight":  4.1},
    ],
    "XLI":  [
        {"symbol": "RTX",   "name": "Raytheon",            "weight":  5.1},
        {"symbol": "CAT",   "name": "Caterpillar",         "weight":  4.8},
        {"symbol": "GE",    "name": "GE Aerospace",        "weight":  4.6},
        {"symbol": "UNP",   "name": "Union Pacific",       "weight":  4.2},
        {"symbol": "HON",   "name": "Honeywell",           "weight":  3.9},
        {"symbol": "ETN",   "name": "Eaton Corp",          "weight":  3.7},
    ],
    "XLB":  [
        {"symbol": "LIN",   "name": "Linde",               "weight": 17.2},
        {"symbol": "APD",   "name": "Air Products",        "weight":  5.8},
        {"symbol": "FCX",   "name": "Freeport-McMoRan",    "weight":  5.4},
        {"symbol": "SHW",   "name": "Sherwin-Williams",    "weight":  5.1},
        {"symbol": "NEM",   "name": "Newmont",             "weight":  4.3},
        {"symbol": "ECL",   "name": "Ecolab",              "weight":  4.0},
    ],
    "XLRE": [
        {"symbol": "PLD",   "name": "Prologis",            "weight":  9.8},
        {"symbol": "AMT",   "name": "American Tower",      "weight":  8.3},
        {"symbol": "EQIX",  "name": "Equinix",             "weight":  7.1},
        {"symbol": "WELL",  "name": "Welltower",           "weight":  5.9},
        {"symbol": "SPG",   "name": "Simon Property",      "weight":  4.7},
        {"symbol": "PSA",   "name": "Public Storage",      "weight":  4.5},
    ],
    "XLK":  [
        {"symbol": "NVDA",  "name": "NVIDIA",              "weight": 22.1},
        {"symbol": "AAPL",  "name": "Apple",               "weight": 20.8},
        {"symbol": "MSFT",  "name": "Microsoft",           "weight": 18.4},
        {"symbol": "AVGO",  "name": "Broadcom",            "weight":  5.7},
        {"symbol": "ORCL",  "name": "Oracle",              "weight":  3.2},
        {"symbol": "CRM",   "name": "Salesforce",          "weight":  2.8},
    ],
    "XLU":  [
        {"symbol": "NEE",   "name": "NextEra Energy",      "weight": 14.9},
        {"symbol": "SO",    "name": "Southern Company",    "weight":  7.2},
        {"symbol": "DUK",   "name": "Duke Energy",         "weight":  7.1},
        {"symbol": "AEP",   "name": "American Electric",   "weight":  5.4},
        {"symbol": "EXC",   "name": "Exelon",              "weight":  4.8},
        {"symbol": "SRE",   "name": "Sempra",              "weight":  4.6},
    ],
}

MACRO_MATRIX: dict[str, dict[str, int]] = {
    "XLE":  {"oil":  1, "rates": -1, "dollar": -1, "inflation":  1, "vix": -1},
    "XLF":  {"oil":  0, "rates":  1, "dollar":  1, "inflation":  0, "vix": -1},
    "XLU":  {"oil":  0, "rates": -1, "dollar":  0, "inflation": -1, "vix":  1},
    "XLRE": {"oil":  0, "rates": -1, "dollar":  0, "inflation": -1, "vix":  1},
    "XLK":  {"oil":  0, "rates": -1, "dollar":  1, "inflation": -1, "vix": -1},
    "XLP":  {"oil":  0, "rates":  0, "dollar": -1, "inflation":  0, "vix":  1},
    "XLY":  {"oil": -1, "rates": -1, "dollar":  0, "inflation": -1, "vix": -1},
    "XLI":  {"oil":  0, "rates":  0, "dollar": -1, "inflation":  1, "vix": -1},
    "XLB":  {"oil":  1, "rates":  0, "dollar": -1, "inflation":  1, "vix": -1},
    "XLV":  {"oil":  0, "rates":  0, "dollar":  0, "inflation":  0, "vix":  1},
    "XLC":  {"oil":  0, "rates": -1, "dollar":  0, "inflation": -1, "vix": -1},
}

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache: dict = {}
_TTL = 300


def _cached(key: str, fn, ttl: int = _TTL):
    now = time.time()
    if key in _cache:
        val, ts = _cache[key]
        if now - ts < ttl:
            return val
    val = fn()
    _cache[key] = (val, now)
    return val


def invalidate_cache():
    _cache.clear()


# ── Computation helpers ────────────────────────────────────────────────────────
def _pct(df: pd.DataFrame, bars: int) -> Optional[float]:
    c = df["close"]
    if len(c) <= bars:
        return None
    return round((float(c.iloc[-1]) / float(c.iloc[-bars - 1]) - 1) * 100, 2)


def _ema(series: pd.Series, span: int) -> float:
    return float(series.ewm(span=span, adjust=False).mean().iloc[-1])


def _ema_stack(e20: float, e50: float, e200: float) -> str:
    if e20 > e50 > e200:
        return "BULL"
    if e50 > e200 and e20 > e200:   # e20 dipped below e50 but still above e200
        return "PARTIAL_BULL"
    if e20 < e50 < e200:
        return "BEAR"
    if e50 < e200 and e20 < e200:   # e20 above e50 but both below e200
        return "PARTIAL_BEAR"
    return "NEUTRAL"


def _trend_label(rs_ratio: float, rs_mom: float) -> str:
    # 0.3 dead-band around 100 to avoid noise-flipping on border cases
    near = 0.3
    if abs(rs_ratio - 100) < near and abs(rs_mom - 100) < near:
        return "NEUTRAL"
    if rs_ratio >= 100 and rs_mom >= 100:
        return "LEADING"
    if rs_ratio >= 100 and rs_mom < 100:
        return "WEAKENING"
    if rs_ratio < 100 and rs_mom < 100:
        return "LAGGING"
    return "IMPROVING"


def _bench_trend(close: float, e50: float, e200: float) -> str:
    if close > e50 > e200:
        return "Bullish"
    if close < e200:
        return "Below key average"
    if close < e50:
        return "Weak"
    return "Neutral"


# ── RRG ───────────────────────────────────────────────────────────────────────
def _rrg_series(
    sector_df: pd.DataFrame,
    spy_df: pd.DataFrame,
    window: int = 20,
    mom_bars: int = 1,
) -> tuple[pd.Series, pd.Series]:
    common = sector_df.index.intersection(spy_df.index)
    sc = sector_df.loc[common, "close"]
    sp = spy_df.loc[common, "close"]

    rs = sc / sp
    rs_sma = rs.rolling(window, min_periods=1).mean()
    rs_ratio = (rs / rs_sma) * 100
    rs_mom = rs_ratio.diff(mom_bars).fillna(0) + 100

    return rs_ratio.fillna(100), rs_mom.fillna(100)


# ── Build one sector row ───────────────────────────────────────────────────────
def _build_row(
    ticker: str,
    name: str,
    df: pd.DataFrame,
    spy_df: pd.DataFrame,
) -> dict:
    c     = df["close"]
    close = float(c.iloc[-1])

    r1   = _pct(df, 1)
    r5   = _pct(df, 5)
    r20  = _pct(df, 20)
    r50  = _pct(df, 50)
    r200 = _pct(df, 200)

    spy_r1   = _pct(spy_df, 1)
    spy_r5   = _pct(spy_df, 5)
    spy_r20  = _pct(spy_df, 20)
    spy_r50  = _pct(spy_df, 50)
    spy_r200 = _pct(spy_df, 200)

    def _vs(a, b):
        return round(a - b, 2) if a is not None and b is not None else None

    e20  = _ema(c, 20)
    e50  = _ema(c, 50)
    e200 = _ema(c, 200)

    rs_ratio_ser, rs_mom_ser = _rrg_series(df, spy_df)
    rs_ratio = float(rs_ratio_ser.iloc[-1])
    rs_mom   = float(rs_mom_ser.iloc[-1])

    return {
        "ticker":          ticker,
        "name":            name,
        "close":           round(close, 2),
        "return_1d":       r1,
        "return_5d":       r5,
        "return_20d":      r20,
        "return_50d":      r50,
        "return_200d":     r200,
        "vs_spy_1d":       _vs(r1,   spy_r1),
        "vs_spy_5d":       _vs(r5,   spy_r5),
        "vs_spy_20d":      _vs(r20,  spy_r20),
        "vs_spy_50d":      _vs(r50,  spy_r50),
        "vs_spy_200d":     _vs(r200, spy_r200),
        "ema20":           round(e20,  2),
        "ema50":           round(e50,  2),
        "ema200":          round(e200, 2),
        "price_vs_ema20":  round(close - e20,  2),
        "price_vs_ema50":  round(close - e50,  2),
        "price_vs_ema200": round(close - e200, 2),
        "ema_stack":       _ema_stack(e20, e50, e200),
        "rs_ratio":        round(rs_ratio, 2),
        "rs_mom":          round(rs_mom,   2),
        "trend_label":     _trend_label(rs_ratio, rs_mom),
        "rrg_quadrant":    _trend_label(rs_ratio, rs_mom),
    }


# ── Null row returned when a ticker fails ─────────────────────────────────────
_NULL_FIELDS = [
    "close",
    "return_1d", "return_5d", "return_20d", "return_50d", "return_200d",
    "vs_spy_1d", "vs_spy_5d", "vs_spy_20d", "vs_spy_50d", "vs_spy_200d",
    "ema20", "ema50", "ema200",
    "price_vs_ema20", "price_vs_ema50", "price_vs_ema200",
    "ema_stack", "rs_ratio", "rs_mom", "trend_label", "rrg_quadrant",
]


def _null_row(ticker: str, name: str) -> dict:
    row: dict = {"ticker": ticker, "name": name}
    for f in _NULL_FIELDS:
        row[f] = None
    return row


# ── Risk regime ────────────────────────────────────────────────────────────────
def _compute_regime(sectors: list[dict], benchmarks: list[dict]) -> dict:
    valid = [s for s in sectors if s.get("vs_spy_20d") is not None]

    growth_lead    = sum(1 for s in valid if s["ticker"] in GROWTH    and s["vs_spy_20d"] > 0)
    defensive_lead = sum(1 for s in valid if s["ticker"] in DEFENSIVE and s["vs_spy_20d"] > 0)

    spy_b = next((b for b in benchmarks if b["ticker"] == "SPY"), {})
    spy_bullish = spy_b.get("trend") == "Bullish"

    strong  = [s["ticker"] for s in valid if s.get("trend_label") == "LEADING"]
    weak    = [s["ticker"] for s in valid if s.get("trend_label") == "LAGGING"]

    if growth_lead >= 2 and defensive_lead <= 1 and spy_bullish:
        mode    = "RISK_ON"
        explain = (
            "Growth and cyclical sectors are leading. "
            + (f"{', '.join(strong[:3])} show relative strength " if strong else "")
            + "while defensive sectors show relative weakness."
        ).strip()
    elif defensive_lead >= 2 and growth_lead <= 1:
        mode    = "RISK_OFF"
        explain = (
            "Defensive sectors are outperforming. "
            "Utilities, Staples, and Health Care show relative strength "
            "while growth sectors underperform."
        )
    else:
        mode    = "NEUTRAL"
        explain = "Leadership is mixed with no clear sector rotation direction."

    return {
        "risk_mode":      mode,
        "strong_sectors": strong,
        "weak_sectors":   weak,
        "explanation":    explain,
    }


# ── Public API ─────────────────────────────────────────────────────────────────
def get_sector_overview() -> dict:
    def _build() -> dict:
        errors: list[str] = []
        spy_df = fetch_ohlcv("SPY", interval="1d", bars=260)

        sectors: list[dict] = []
        for tkr, nm in SECTORS.items():
            try:
                df = fetch_ohlcv(tkr, interval="1d", bars=260)
                sectors.append(_build_row(tkr, nm, df, spy_df))
            except Exception as exc:
                sectors.append(_null_row(tkr, nm))
                errors.append(f"{tkr}: {exc}")

        benchmarks: list[dict] = []
        for tkr, nm in BENCHMARKS.items():
            try:
                df    = fetch_ohlcv(tkr, interval="1d", bars=260)
                c     = df["close"]
                close = float(c.iloc[-1])
                e50   = _ema(c, 50)
                e200  = _ema(c, 200)
                benchmarks.append({
                    "ticker":   tkr,
                    "name":     nm,
                    "close":    round(close, 2),
                    "return_1d": _pct(df, 1),
                    "trend":    _bench_trend(close, e50, e200),
                })
            except Exception as exc:
                benchmarks.append({"ticker": tkr, "name": nm, "close": None,
                                   "return_1d": None, "trend": None})
                errors.append(f"{tkr}: {exc}")

        regime = _compute_regime(sectors, benchmarks)

        return {
            "ok":           not errors or bool(sectors),
            "last_updated": round(time.time()),
            "data": {
                "sectors":    sectors,
                "benchmarks": benchmarks,
                "regime":     regime,
            },
            "errors": errors,
        }

    return _cached("overview", _build)


def get_sector_detail(etf: str) -> dict:
    tkr = etf.upper()

    def _build() -> dict:
        errors: list[str] = []
        try:
            df     = fetch_ohlcv(tkr, interval="1d", bars=260)
            spy_df = fetch_ohlcv("SPY", interval="1d", bars=260)
        except Exception as exc:
            return {
                "ok": False,
                "last_updated": round(time.time()),
                "data": None,
                "errors": [str(exc)],
            }

        name = SECTORS.get(tkr, tkr)
        try:
            row = _build_row(tkr, name, df, spy_df)
        except Exception as exc:
            row = _null_row(tkr, name)
            errors.append(str(exc))

        # Historical series (last 252 bars)
        try:
            common  = df.index.intersection(spy_df.index)
            sc      = df.loc[common, "close"]
            sp      = spy_df.loc[common, "close"]
            rs_ser  = (sc / sp * 100).round(3)
            n       = 252
            history = {
                "dates":  [str(d)[:10] for d in common[-n:].tolist()],
                "prices": sc.iloc[-n:].round(2).tolist(),
                "rs":     rs_ser.iloc[-n:].tolist(),
                "ema20":  sc.ewm(span=20,  adjust=False).mean().iloc[-n:].round(2).tolist(),
                "ema50":  sc.ewm(span=50,  adjust=False).mean().iloc[-n:].round(2).tolist(),
                "ema200": sc.ewm(span=200, adjust=False).mean().iloc[-n:].round(2).tolist(),
            }
        except Exception as exc:
            history = None
            errors.append(f"history: {exc}")

        return {
            "ok":           not errors,
            "last_updated": round(time.time()),
            "data": {
                **row,
                "history":           history,
                "holdings":          HOLDINGS.get(tkr, []),
                "top_gainers":       [],
                "top_losers":        [],
                "volume_leaders":    [],
                "data_source_status": "static_fallback" if not errors else "live",
            },
            "errors": errors,
        }

    return _cached(f"detail:{tkr}", _build)


def get_sector_rrg(trail: int = 12) -> dict:
    def _build() -> dict:
        errors: list[str] = []
        spy_df = fetch_ohlcv("SPY", interval="1d", bars=260)
        items: list[dict] = []

        for tkr, nm in SECTORS.items():
            try:
                df = fetch_ohlcv(tkr, interval="1d", bars=260)
                rs_r, rs_m = _rrg_series(df, spy_df)

                trail_r = rs_r.dropna().tail(trail).round(2).tolist()
                trail_m = rs_m.dropna().tail(trail).round(2).tolist()
                cur_r   = trail_r[-1] if trail_r else 100.0
                cur_m   = trail_m[-1] if trail_m else 100.0

                items.append({
                    "ticker":      tkr,
                    "name":        nm,
                    "rs_ratio":    round(cur_r, 2),
                    "rs_mom":      round(cur_m, 2),
                    "trend_label": _trend_label(cur_r, cur_m),
                    "trail_ratio": trail_r,
                    "trail_mom":   trail_m,
                })
            except Exception as exc:
                items.append({
                    "ticker": tkr, "name": nm,
                    "rs_ratio": None, "rs_mom": None,
                    "trend_label": None,
                    "trail_ratio": [], "trail_mom": [],
                })
                errors.append(f"{tkr}: {exc}")

        return {
            "ok":           not errors,
            "last_updated": round(time.time()),
            "data":         items,
            "errors":       errors,
        }

    return _cached("rrg", _build)


def get_sector_heatmap(metric: str = "return_1d") -> dict:
    overview = get_sector_overview()
    sectors  = overview.get("data", {}).get("sectors", [])
    items = [
        {"ticker": s["ticker"], "name": s["name"], "value": s.get(metric)}
        for s in sectors
    ]
    return {
        "ok":           overview.get("ok", True),
        "last_updated": overview.get("last_updated", round(time.time())),
        "data":         items,
        "errors":       overview.get("errors", []),
    }


def get_macro_matrix() -> dict:
    return {
        "ok":           True,
        "last_updated": round(time.time()),
        "data": {
            "sectors": list(SECTORS.keys()),
            "factors": ["oil", "rates", "dollar", "inflation", "vix"],
            "matrix":  MACRO_MATRIX,
            "names":   SECTORS,
        },
        "errors": [],
    }

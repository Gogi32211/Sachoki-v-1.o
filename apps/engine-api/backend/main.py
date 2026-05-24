"""
engine-api — pure-compute layer as a standalone Railway service.

Phase B-2 extraction (per docs/ARCHITECTURE_TARGET.md): the engine_api
subpackage from scanner-api is promoted to its own service. The Python
surface stays identical to the in-process barrel; the HTTP wrapper here
is intentionally thin.

Architectural law:
    NO DATABASE_URL access.
    NO Massive HTTP fetch.
    NO scan orchestration.
    NO dashboard concerns.
    Pure compute on inputs supplied by the caller.

Single primary endpoint:
    POST /api/engines/run
        body:  { ticker, timeframe, ohlcv: [{ts, open, high, low, close, volume}, ...],
                 split_flags?: dict, profile?: "sp500"|"nasdaq"|"all_us" }
        returns: { bars: [...normalized bar dicts...],
                   engines_ran: [...], engines_failed: [...] }

Reachable engine modules for parity / single-engine compute:
    POST /api/engines/single/{engine}
        body: { ohlcv: [...] }
        returns: { columns: {...} }
"""
from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .engines import run_engines

log = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "info").upper())

app = FastAPI(title="engine-api", version="0.1.0")

_VERSION = "0.1.0"
_PHASE   = "B-2 extraction"


# ── Health / version ─────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "engine-api"}


@app.get("/version")
def version():
    return {"service": "engine-api", "version": _VERSION, "phase": _PHASE}


@app.get("/api/debug/status")
def debug_status():
    return {
        "service":           "engine-api",
        "mode":              "pure_compute",
        "database_required": False,
        "massive_required":  False,
        "engines_in_pipeline": 14,  # tz/wlnbb/vabs/wick/combo/f/fly/b/g/ult260/ult_v2/delta/gog (+split when flags supplied)
    }


# ── Request / response models ────────────────────────────────────────────────

class OHLCVBar(BaseModel):
    ts: str | int        # ISO timestamp or epoch ms — accepted both
    open:  float
    high:  float
    low:   float
    close: float
    volume: float | None = None


class RunRequest(BaseModel):
    ticker:      str
    timeframe:   str = "1d"
    ohlcv:       list[dict]              # list of bar dicts (OHLCV); ts as str
    split_flags: dict | None = None
    profile:     str = "sp500"           # passed-through for future profile-aware engines


class RunResponse(BaseModel):
    ok:              bool
    bars:            list[dict]
    bar_count:       int
    engines_ran:     list[str]
    engines_failed:  list[str]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ohlcv_to_df(ohlcv: list[dict]) -> pd.DataFrame:
    """Convert request body OHLCV list → DataFrame with DatetimeIndex."""
    if not ohlcv:
        raise HTTPException(status_code=400, detail="ohlcv list is empty")
    df = pd.DataFrame(ohlcv)
    if "ts" not in df.columns:
        raise HTTPException(status_code=400, detail="each bar must have a 'ts' field")
    try:
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"cannot parse 'ts' values: {exc}")
    df = df.set_index("ts").sort_index()
    needed = {"open", "high", "low", "close"}
    missing = needed - set(df.columns)
    if missing:
        raise HTTPException(status_code=400, detail=f"missing OHLC columns: {sorted(missing)}")
    if "volume" not in df.columns:
        df["volume"] = 0.0
    return df[["open", "high", "low", "close", "volume"]]


# ── Main entrypoint ──────────────────────────────────────────────────────────

@app.post("/api/engines/run", response_model=RunResponse)
def run_engine_pipeline(req: RunRequest):
    """
    Run the full 14-engine pipeline + scoring (turbo, RTB, profile, canonical,
    beta, ultra) on the supplied OHLCV. Returns normalized per-bar dicts.

    No DB, no Massive — caller resolves market data + split flags first
    and passes them in.
    """
    df = _ohlcv_to_df(req.ohlcv)
    try:
        bars = run_engines(
            ticker=req.ticker,
            timeframe=req.timeframe,
            df=df,
            split_flags=req.split_flags,
        )
    except Exception as exc:
        log.warning("run_engines raised for %s: %s", req.ticker, exc)
        raise HTTPException(status_code=500, detail=f"engine compute failed: {type(exc).__name__}: {exc}")

    last_debug = (bars[-1].get("engine_debug") if bars else {}) or {}
    return {
        "ok":             True,
        "bars":           bars,
        "bar_count":      len(bars),
        "engines_ran":    last_debug.get("engines_ran",    []),
        "engines_failed": last_debug.get("engines_failed", []),
    }


# ── Single-engine endpoints (parity / debug) ─────────────────────────────────

_SINGLE_ENGINES: dict[str, str] = {
    "indicators":  "engines.indicator_builder:build_indicators",
    "tz":          "engines.chart_signal_engine:compute_signals",
    "wlnbb":       "engines.chart_wlnbb_engine:compute_wlnbb",
    "vabs":        "engines.chart_vabs_engine:compute_vabs",
    "wick":        "engines.chart_wick_engine:compute_wick",
    "combo":       "engines.chart_combo_engine:compute_combo",
    "f":           "engines.chart_f_engine:compute_f_signals",
    "fly":         "engines.chart_fly_engine:compute_fly_series",
    "b":           "engines.chart_b_engine:compute_b_signals",
    "g":           "engines.chart_b_engine:compute_g_signals",
    "ult260":      "engines.chart_ultra_engine:compute_260308_l88",
    "ult_v2":      "engines.chart_ultra_engine:compute_ultra_v2",
    "delta":       "engines.chart_delta_engine:compute_delta",
    "turbo_score": "engines.chart_turbo_engine:compute_turbo_score",
    "rtb":         "engines.chart_rtb_engine:calc_rtb_v4",
}


@app.get("/api/engines/list")
def list_engines():
    """Discoverable list of single-engine endpoints + the dotted import path."""
    return {"engines": _SINGLE_ENGINES, "primary": "POST /api/engines/run"}


@app.post("/api/engines/single/{engine}")
def run_single_engine(engine: str, req: RunRequest):
    """Diagnostic: run ONE engine on the supplied OHLCV and return its DataFrame
    columns as records. Used by parity scripts and debugging."""
    if engine not in _SINGLE_ENGINES:
        raise HTTPException(status_code=404,
            detail=f"unknown engine {engine!r}; valid: {sorted(_SINGLE_ENGINES.keys())}")
    df = _ohlcv_to_df(req.ohlcv)
    import importlib
    mod_name, fn_name = _SINGLE_ENGINES[engine].split(":")
    mod = importlib.import_module("backend." + mod_name)
    fn  = getattr(mod, fn_name)
    try:
        out = fn(df)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{engine} compute failed: {exc}")

    if isinstance(out, pd.DataFrame):
        return {
            "engine":  engine,
            "rows":    len(out),
            "columns": list(out.columns),
            "records": out.reset_index().to_dict(orient="records"),
        }
    if isinstance(out, (dict, list)):
        return {"engine": engine, "result": out}
    return {"engine": engine, "result": str(out)}

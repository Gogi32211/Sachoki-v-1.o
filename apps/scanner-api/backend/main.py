"""
scanner-api — Phase 7B: real Ultra scoring is now the default.

scoring_mode defaults to "real" (production compute_ultra_score engine).
"temporary" and "compare" remain available for debug.
Candidate row_json always carries score_engine, ultra_score, band,
final_signal, why_selected, risk_flags.
Scheduler and full-market scan remain disabled.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

log = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "info").upper())

app = FastAPI(title="scanner-api", version="0.9.0")

_VERSION = "0.9.0"
_PHASE   = "7B-real-default"

# Known Ultra Scan table names (confirmed from backend/ultra_scan_migration.py)
_RUN_TABLE  = "ultra_scan_runs"
_CAND_TABLE = "ultra_scan_candidates"

# Safe columns for ORDER BY — never interpolate arbitrary user input
_SAFE_SORT_COLS = {"ultra_score", "ticker", "created_at"}

# ── Controlled scan config ────────────────────────────────────────────────────
_MAX_SYMBOLS        = 500
_ALLOWED_TIMEFRAMES = ["1d"]
_ALLOWED_UNIVERSES  = {
    "manual_test", "sp500_sample", "nasdaq_sample",
    "watchlist_sample", "custom_sample",
}
_SCHEDULER_ENABLED  = False

from .scan_engine import DEFAULT_SYMBOLS as _DEFAULT_SYMBOLS  # noqa: E402

_SP500_SAMPLE: list[str] = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","BRK-B","AVGO","JPM",
    "LLY","V","UNH","XOM","MA","JNJ","PG","HD","MRK","ABBV",
    "COST","CVX","CRM","BAC","NFLX","AMD","PEP","KO","TMO","WMT",
    "ORCL","ADBE","MCD","ACN","PM","CSCO","ABT","GE","TXN","DHR",
    "CAT","SPGI","MS","AXP","GS","BLK","RTX","HON","LMT","DE",
    "NOW","ISRG","SYK","PLD","AMT","REGN","VRTX","GILD","BSX","ELV",
    "MDT","CI","HUM","CVS","MCK","AIG","AFL","PRU","MET","TRV",
    "SCHW","CME","ICE","NDAQ","CB","AON","MMC","WTW","AJG","BRO",
    "NEE","SO","DUK","AEP","D","EXC","SRE","XEL","PCG","WEC",
    "PGR","ANET","FICO","CDNS","ANSS","CTSH","EPAM","LDOS","SAIC","BAH",
]
_NASDAQ_SAMPLE: list[str] = [
    "AAPL","MSFT","NVDA","AMZN","META","TSLA","GOOGL","AVGO","CSCO","ADBE",
    "AMD","TXN","QCOM","INTC","INTU","AMAT","MU","LRCX","KLAC","SNPS",
    "CDNS","MRVL","NXPI","ON","MCHP","PANW","CRWD","DDOG","ZS","NET",
    "PLTR","SNOW","OKTA","TEAM","HUBS","VEEV","MELI","BKNG","ABNB","DASH",
    "UBER","SOFI","COIN","HOOD","AFRM","UPST","OPEN","RDFN","REDFIN","TTD",
    "ROKU","SPOT","NFLX","WBD","PARA","RBLX","U","SNAP","PINS","APP",
    "CELH","DKNG","PENN","MGM","WYNN","LVS","CZR","NCLH","CCL","RCL",
    "MRNA","BNTX","NVAX","SGEN","ALNY","BMRN","RARE","FOLD","ACAD","IONS",
    "ZM","DOCU","BILL","GTLB","PATH","MDB","ESTC","NCNO","ALRM","ASAN",
    "FROG","CFLT","BRZE","SMAR","BOX","DRCT","BLZE","GDDY","WIX","SHOP",
]

# ── Concurrency control ───────────────────────────────────────────────────────
_scan_lock      = threading.Lock()   # guards _scan_running + _current_run_id
_scan_running   = False
_current_run_id: int | None = None
_cancel_event   = threading.Event()  # set to request cancel between symbols


_ALLOWED_SCORING_MODES = {"temporary", "real", "compare"}


class ScanRequest(BaseModel):
    symbols:        list[str] = _DEFAULT_SYMBOLS
    timeframe:      str       = "1d"
    universe:       str       = "manual_test"
    scan_mode:      str       = "controlled_test"
    scoring_mode:   str       = "real"         # real (default) | temporary | compare
    replace_latest: bool      = True
    dry_run:        bool      = False

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, v: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(s.upper().strip() for s in v if s.strip()))
        if not cleaned:
            raise ValueError("symbols list must not be empty.")
        if len(cleaned) > _MAX_SYMBOLS:
            raise ValueError(f"Controlled async scan allows max {_MAX_SYMBOLS} symbols.")
        return cleaned

    @field_validator("timeframe")
    @classmethod
    def check_timeframe(cls, v: str) -> str:
        if v not in _ALLOWED_TIMEFRAMES:
            raise ValueError(f"Allowed timeframes: {_ALLOWED_TIMEFRAMES}")
        return v

    @field_validator("universe")
    @classmethod
    def check_universe(cls, v: str) -> str:
        if v not in _ALLOWED_UNIVERSES:
            raise ValueError(
                f"universe must be one of: {sorted(_ALLOWED_UNIVERSES)}. "
                "Full-market universes (full_sp500, full_nasdaq, all_us) are not allowed."
            )
        return v

    @field_validator("scoring_mode")
    @classmethod
    def check_scoring_mode(cls, v: str) -> str:
        if v not in _ALLOWED_SCORING_MODES:
            raise ValueError(f"scoring_mode must be one of: {sorted(_ALLOWED_SCORING_MODES)}")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_row_json(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _normalize_candidate(row_json_str: str | None, db_score: float | None) -> dict:
    """
    Map a raw row_json string to the standard candidate shape.
    Returns null/empty for missing fields — never raises.
    """
    r = _parse_row_json(row_json_str)

    def _get(*keys, default=None):
        for k in keys:
            v = r.get(k)
            if v is not None:
                return v
        return default

    def _as_list(val):
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                return parsed if isinstance(parsed, list) else [parsed]
            except Exception:
                return [val] if val else []
        return []

    return {
        "symbol":               _get("ticker", default=""),
        "company":              _get("name", "company", default=""),
        "sector":               _get("sector", default=""),
        "industry":             _get("industry", default=""),
        "price":                _get("price", "close", "last_price", default=None),
        "change_pct":           _get("change_pct", "chg_pct", default=None),
        "volume":               _get("volume", default=None),
        "ultra_score":          _get("ultra_score", default=db_score),
        "setup_quality_score":  _get("turbo_score", "score", default=None),
        "band":                 _get("ultra_score_band_v2", "ultra_score_band", default=""),
        "priority":             _get("ultra_score_priority", default=""),
        "role":                 _get("tz_intel_role", "abr_role", default=""),
        "action_bucket":        _get("action_bucket", "bucket", default=""),
        "final_signal":         _get("t_signal", "final_signal", "signal", default=""),
        "sequence_4bar":        _get("sequence_4bar", "sequence", default=""),
        "abr_category":         _get("abr_category", "category", default=""),
        "wlnbb_bucket":         _get("wlnbb_bucket", "l_bucket", default=""),
        "ema_state":            _get("ema_state", "ema_cross", default=""),
        "risk_flags":           _as_list(_get("ultra_score_flags", default=[])),
        "events":               [],
        "why_selected":         _as_list(_get("ultra_score_reasons", default=[])),
        # Phase 8G commit 3: normalized scanner payload from engine_registry.
        # When absent (legacy rows), these surface as empty dicts so the UI
        # can read them without null-guards.
        "signals":              _get("signals", default={}) or {},
        "indicators":           _get("indicators", default={}) or {},
        "ohlcv":                _get("ohlcv", default={}) or {},
        "scores":               _get("scores_obj", "scores", default={}) or {},
        "roles":                _get("roles", default={}) or {},
        "split":                _get("split", default={}) or {},
        "engine_debug":         _get("engine_debug", default={}) or {},
        "bar_date":             _get("bar_date", default=None),
        "ultra_active_signals": _as_list(_get("ultra_active_signals", default=[])),
    }


def _get_latest_run(cur, universe: str | None, tf: str | None) -> dict | None:
    """
    Find the latest completed scan run.
    Tries universe+tf first; falls back to any is_latest completed run.
    """
    if universe and tf:
        cur.execute(
            f"""
            SELECT id, universe, tf, nasdaq_batch, status,
                   started_at, finished_at, total_candidates
            FROM {_RUN_TABLE}
            WHERE universe=%s AND tf=%s AND is_latest=TRUE AND status='completed'
            ORDER BY finished_at DESC LIMIT 1
            """,
            (universe, tf),
        )
        row = cur.fetchone()
        if row:
            return dict(row)

    cur.execute(
        f"""
        SELECT id, universe, tf, nasdaq_batch, status,
               started_at, finished_at, total_candidates
        FROM {_RUN_TABLE}
        WHERE is_latest=TRUE AND status='completed'
        ORDER BY finished_at DESC LIMIT 1
        """
    )
    row = cur.fetchone()
    return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Health / version
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "scanner-api"}


@app.get("/version")
def version():
    return {"service": "scanner-api", "version": _VERSION, "phase": _PHASE}


# ─────────────────────────────────────────────────────────────────────────────
# Debug endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/debug/status")
def debug_status():
    from . import db as _db

    db_configured = bool(_db.DATABASE_URL)
    db_connected, db_error = (False, None)
    latest_scan_found = False
    latest_scan_id = None
    latest_candidate_count = 0

    if db_configured:
        db_connected, db_error = _db.ping()

    if db_connected:
        try:
            with _db.get_conn() as cur:
                if _db.table_exists(_RUN_TABLE):
                    run = _get_latest_run(cur, None, None)
                    if run:
                        latest_scan_found = True
                        latest_scan_id = run["id"]
                        latest_candidate_count = run.get("total_candidates") or 0
        except Exception as exc:
            log.warning("debug/status DB probe: %s", exc)

    from . import progress as _prog

    with _scan_lock:
        is_running  = _scan_running
        running_rid = _current_run_id

    return {
        "service":                        "scanner-api",
        "mode":                           "async_controlled_scan_phase",
        "database_configured":            db_configured,
        "database_connected":             db_connected,
        "database_error":                 db_error or None,
        "redis_configured":               bool(os.getenv("REDIS_URL")),
        "redis_progress_available":       _prog.redis_available(),
        "massive_configured":             bool(os.getenv("MASSIVE_API_KEY")),
        "async_scan_available":           True,
        "scan_execution_available":       True,
        "controlled_scan_max_symbols":    _MAX_SYMBOLS,
        "allowed_universes":              sorted(_ALLOWED_UNIVERSES),
        "default_scoring_mode":           "real",
        "available_scoring_modes":        sorted(_ALLOWED_SCORING_MODES),
        "score_engine":                   "real_ultra_score",
        "scanning_enabled":               False,
        "scheduler_enabled":              _SCHEDULER_ENABLED,
        "full_market_scan_enabled":       False,
        "running_scan":                   is_running,
        "running_run_id":                 running_rid,
        "latest_ultra_scan_found":        latest_scan_found,
        "latest_ultra_scan_id":           latest_scan_id,
        "latest_ultra_candidate_count":   latest_candidate_count,
    }


@app.get("/api/debug/db")
def debug_db():
    from . import db as _db

    if not _db.DATABASE_URL:
        return {
            "database_configured":  False,
            "database_connected":   False,
            "tables":               [],
            "ultra_related_tables": [],
            "candidate_tables":     [],
            "scan_run_tables":      [],
            "notes":                ["DATABASE_URL not set"],
        }

    connected, err = _db.ping()
    if not connected:
        return {
            "database_configured":  True,
            "database_connected":   False,
            "error_type":           err,
            "tables":               [],
            "ultra_related_tables": [],
            "candidate_tables":     [],
            "scan_run_tables":      [],
            "notes":                ["Connection failed — check DATABASE_URL"],
        }

    try:
        tables = _db.list_tables()
    except Exception as exc:
        return {
            "database_configured":  True,
            "database_connected":   True,
            "error_type":           type(exc).__name__,
            "tables":               [],
            "ultra_related_tables": [],
            "candidate_tables":     [],
            "scan_run_tables":      [],
            "notes":                ["Failed to list tables"],
        }

    keywords = {"ultra", "scan", "candidate", "turbo", "dashboard"}
    all_names = [t["table"] for t in tables]
    ultra_related   = [n for n in all_names if any(k in n for k in keywords)]
    candidate_tables = [n for n in all_names if "candidate" in n]
    scan_run_tables  = [n for n in all_names if "scan" in n and "run" in n]

    notes = []
    if _RUN_TABLE not in all_names:
        notes.append(f"'{_RUN_TABLE}' not found — DB may be empty staging instance")
    if _CAND_TABLE not in all_names:
        notes.append(f"'{_CAND_TABLE}' not found")
    if not notes:
        notes.append("Ultra Scan tables present and ready")

    return {
        "database_configured":  True,
        "database_connected":   True,
        "tables":               tables,
        "ultra_related_tables": ultra_related,
        "candidate_tables":     candidate_tables,
        "scan_run_tables":      scan_run_tables,
        "notes":                notes,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Score compare debug endpoint (Phase 7A)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/debug/score-compare")
def debug_score_compare(
    symbol: str = Query(default="AAPL"),
    tf: str = Query(default="1d"),
):
    """
    Fetch live bars for one symbol and compute both temporary and real scores.
    Returns a side-by-side comparison without writing to the database.
    """
    from .scan_engine import fetch_bars, compute_signals, score_candidate
    from .scoring_adapter import compute_scanner_ultra_candidate

    sym = symbol.upper().strip()

    df = fetch_bars(sym, interval=tf)
    if df is None:
        return JSONResponse(
            status_code=422,
            content={"error": f"Could not fetch bars for {sym} ({tf}). Check MASSIVE_API_KEY."},
        )

    signals = compute_signals(df)
    if not signals:
        return JSONResponse(
            status_code=422,
            content={"error": f"Signal computation failed for {sym}."},
        )

    temp = score_candidate(sym, signals)
    real = compute_scanner_ultra_candidate(sym, signals, timeframe=tf, df=df)

    return {
        "symbol":    sym,
        "timeframe": tf,
        "signals": {
            "price":             signals.get("price"),
            "rsi":               signals.get("rsi"),
            "ema20":             signals.get("ema20"),
            "ema50":             signals.get("ema50"),
            "vol_ratio":         signals.get("vol_ratio"),
            "mom5d_pct":         signals.get("mom5d_pct"),
            "price_above_ema20": signals.get("price_above_ema20"),
            "price_above_ema50": signals.get("price_above_ema50"),
            "ema20_above_ema50": signals.get("ema20_above_ema50"),
            "ema_cross_up_5d":   signals.get("ema_cross_up_5d"),
        },
        "temporary": {
            "score":        temp["ultra_score"],
            "band":         temp["band"],
            "why_selected": temp.get("why_selected", []),
            "risk_flags":   temp.get("risk_flags", []),
            "engine":       temp.get("score_engine"),
        },
        "real": {
            "score":                    real["ultra_score"],
            "band":                     real["band"],
            "why_selected":             real.get("why_selected", []),
            "risk_flags":               real.get("risk_flags", []),
            "raw_before_penalty":       real.get("ultra_score_raw_before_penalty"),
            "penalty_total":            real.get("ultra_score_penalty_total"),
            "regime_bonus":             real.get("ultra_score_regime_bonus"),
            "caps_applied":             real.get("ultra_score_caps_applied", []),
            "engine":                   real.get("score_engine"),
        },
        "delta": {
            "score_diff": real["ultra_score"] - temp["ultra_score"],
            "band_diff":  real["band"] != temp["band"],
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Ultra Scan read endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/scans/ultra/latest")
def get_latest_ultra_scan(
    universe: str | None = Query(default=None),
    tf: str | None = Query(default=None),
):
    from . import db as _db

    if not _db.DATABASE_URL:
        return {"has_data": False, "message": "DATABASE_URL not configured", "source": "db"}

    connected, err = _db.ping()
    if not connected:
        return {"has_data": False, "message": f"DB connection failed ({err})", "source": "db"}

    try:
        with _db.get_conn() as cur:
            if not _db.table_exists(_RUN_TABLE):
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' ORDER BY table_name"
                )
                found = [r["table_name"] for r in cur.fetchall()]
                return {
                    "has_data":     False,
                    "message":      "Ultra Scan tables not found in this database.",
                    "source":       "db",
                    "tables_found": found,
                }

            run = _get_latest_run(cur, universe, tf)
            if not run:
                return {"has_data": False, "message": "No completed Ultra Scan found.", "source": "db"}

            return {
                "has_data": True,
                "source":   "db",
                "run": {
                    "id":               run["id"],
                    "status":           run["status"],
                    "universe":         run["universe"],
                    "timeframe":        run["tf"],
                    "nasdaq_batch":     run.get("nasdaq_batch") or "",
                    "started_at":       str(run["started_at"]) if run.get("started_at") else None,
                    "finished_at":      str(run["finished_at"]) if run.get("finished_at") else None,
                    "total_candidates": run.get("total_candidates") or 0,
                },
            }
    except Exception as exc:
        log.exception("get_latest_ultra_scan error")
        return JSONResponse(
            status_code=500,
            content={"has_data": False, "message": "Internal error", "error_type": type(exc).__name__},
        )


@app.get("/api/scans/ultra/latest/candidates")
def get_latest_ultra_candidates(
    universe: str | None = Query(default=None),
    tf: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(default="ultra_score"),
    sort_dir: str = Query(default="desc"),
):
    from . import db as _db

    sort_col   = sort_by if sort_by in _SAFE_SORT_COLS else "ultra_score"
    sort_order = "DESC" if sort_dir.lower() != "asc" else "ASC"

    _no_data = {
        "has_data": False, "scan_run_id": None,
        "total_available": 0, "count": 0, "candidates": [],
    }

    if not _db.DATABASE_URL:
        return {**_no_data, "message": "DATABASE_URL not configured"}

    connected, err = _db.ping()
    if not connected:
        return {**_no_data, "message": f"DB connection failed ({err})"}

    try:
        with _db.get_conn() as cur:
            if not _db.table_exists(_RUN_TABLE) or not _db.table_exists(_CAND_TABLE):
                return {**_no_data, "message": "Ultra Scan tables not found in this database."}

            run = _get_latest_run(cur, universe, tf)
            if not run:
                return {**_no_data, "message": "No completed Ultra Scan found."}

            run_id = run["id"]

            cur.execute(
                f"SELECT COUNT(*) AS n FROM {_CAND_TABLE} WHERE scan_run_id=%s",
                (run_id,),
            )
            total = (cur.fetchone() or {}).get("n") or 0

            cur.execute(
                f"""
                SELECT ticker, ultra_score, row_json
                FROM {_CAND_TABLE}
                WHERE scan_run_id=%s
                ORDER BY {sort_col} {sort_order}
                LIMIT %s OFFSET %s
                """,
                (run_id, limit, offset),
            )
            rows = cur.fetchall()

            candidates = [
                _normalize_candidate(r.get("row_json"), r.get("ultra_score"))
                for r in rows
            ]

            return {
                "has_data":        True,
                "scan_run_id":     run_id,
                "universe":        run["universe"],
                "timeframe":       run["tf"],
                "total_available": int(total),
                "count":           len(candidates),
                "limit":           limit,
                "offset":          offset,
                "candidates":      candidates,
            }

    except Exception as exc:
        log.exception("get_latest_ultra_candidates error")
        return JSONResponse(
            status_code=500,
            content={**_no_data, "error_type": type(exc).__name__},
        )


# ─────────────────────────────────────────────────────────────────────────────
# Chart endpoints  (Phase 8C)
# ─────────────────────────────────────────────────────────────────────────────

_CHART_ALLOWED_TF   = {"1d", "1wk", "4h", "1h"}
_CHART_MAX_BARS     = 500
_CHART_DEFAULT_BARS = 150


def _chart_sym(symbol: str) -> str | None:
    import re
    s = symbol.upper().strip()
    if not re.match(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$", s):
        return None
    return s


@app.get("/api/chart/candles")
def chart_candles(
    symbol: str = Query(...),
    tf:     str = Query(default="1d"),
    bars:   int = Query(default=_CHART_DEFAULT_BARS, ge=10, le=_CHART_MAX_BARS),
):
    """
    Return OHLCV candles with T/Z + WLNBB signal overlays.
    Completed candles only — no partial current bar, no yfinance.
    """
    sym = _chart_sym(symbol)
    if sym is None:
        return JSONResponse(status_code=422, content={"error": "Invalid ticker symbol"})
    if tf not in _CHART_ALLOWED_TF:
        return JSONResponse(status_code=422,
                            content={"error": f"tf must be one of {sorted(_CHART_ALLOWED_TF)}"})

    from .chart_engine import get_chart_candles
    result = get_chart_candles(sym, tf=tf, bars=bars)
    if not result.get("ok"):
        return JSONResponse(status_code=422, content=result)
    return result


@app.get("/api/chart/score")
def chart_score(
    symbol: str = Query(...),
    tf:     str = Query(default="1d"),
):
    """
    Return Ultra score panel for a single symbol (no candles).
    """
    sym = _chart_sym(symbol)
    if sym is None:
        return JSONResponse(status_code=422, content={"error": "Invalid ticker symbol"})
    if tf not in _CHART_ALLOWED_TF:
        return JSONResponse(status_code=422,
                            content={"error": f"tf must be one of {sorted(_CHART_ALLOWED_TF)}"})

    from .chart_engine import get_chart_score
    result = get_chart_score(sym, tf=tf)
    if not result.get("ok"):
        return JSONResponse(status_code=422, content=result)
    return result


@app.get("/api/chart/snapshot")
def chart_snapshot(
    symbol: str = Query(...),
    tf:     str = Query(default="1d"),
    bars:   int = Query(default=_CHART_DEFAULT_BARS, ge=10, le=_CHART_MAX_BARS),
):
    """
    Full chart snapshot: candles + markers + score panel + WLNBB summary.
    Single Massive fetch shared across all computations.
    """
    sym = _chart_sym(symbol)
    if sym is None:
        return JSONResponse(status_code=422, content={"error": "Invalid ticker symbol"})
    if tf not in _CHART_ALLOWED_TF:
        return JSONResponse(status_code=422,
                            content={"error": f"tf must be one of {sorted(_CHART_ALLOWED_TF)}"})

    from .chart_engine import get_chart_snapshot
    result = get_chart_snapshot(sym, tf=tf, bars=bars)
    if not result.get("ok"):
        return JSONResponse(status_code=422, content=result)
    return result


_CHART_MAX_LOOKBACK     = 120
_CHART_DEFAULT_LOOKBACK = 60


@app.get("/api/chart/history")
def chart_history(
    symbol:   str = Query(...),
    tf:       str = Query(default="1d"),
    lookback: int = Query(default=_CHART_DEFAULT_LOOKBACK, ge=10, le=_CHART_MAX_LOOKBACK),
):
    """
    Historical per-bar signal timeline for Super Chart History view.
    Returns last `lookback` bars with T/Z + WLNBB signals grouped into rows.
    No scoring per bar. No yfinance.
    """
    sym = _chart_sym(symbol)
    if sym is None:
        return JSONResponse(status_code=422, content={"error": "Invalid ticker symbol"})
    if tf not in _CHART_ALLOWED_TF:
        return JSONResponse(status_code=422,
                            content={"error": f"tf must be one of {sorted(_CHART_ALLOWED_TF)}"})

    from .chart_engine import get_chart_history
    result = get_chart_history(sym, tf=tf, lookback=lookback)
    if not result.get("ok"):
        return JSONResponse(status_code=422, content=result)
    return result


@app.get("/api/chart/signals")
def chart_signals():
    """
    Stub — P1/P2 extended signal families not yet migrated.
    Returns a list of what is and isn't implemented.
    """
    from .chart_engine import MISSING_GROUPS
    return {
        "implemented": [
            "T/Z candlestick state machine (T1G-T12, Z1G-Z12, Z7)",
            "WLNBB volume Bollinger bands (L1-L6, L34, L43, L64, L22)",
            "FRI34 / FRI43 / FRI64 (BLUE + L-combo)",
            "BLUE / UI indicators",
            "CCI_READY / CCI_0_RETEST_OK / CCI_BLUE_TURN",
            "BO_UP / BO_DN / BX_UP / BX_DN / BE_UP / BE_DN breakouts",
            "PRE_PUMP (VSA pattern clustering)",
            "FUCHSIA_RH / FUCHSIA_RL (RSI extremes)",
            "Ultra score panel (real_ultra_score engine)",
        ],
        "missing_groups": MISSING_GROUPS,
        "note": "Missing groups are scheduled for Phase 8C-P1 / Phase 8C-P2.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# One-time staging seeder  (Phase 3.5 — remove in Phase 4)
# Protected by SEED_TOKEN env var. Never runs scans. Synthetic data only.
# ─────────────────────────────────────────────────────────────────────────────

_DDL_SCHEMA = """
CREATE TABLE IF NOT EXISTS ultra_scan_runs (
    id               SERIAL PRIMARY KEY,
    universe         VARCHAR(20) NOT NULL DEFAULT 'sp500',
    tf               VARCHAR(10) NOT NULL DEFAULT '1d',
    nasdaq_batch     VARCHAR(20) NOT NULL DEFAULT '',
    status           VARCHAR(20) NOT NULL DEFAULT 'running',
    is_latest        BOOLEAN NOT NULL DEFAULT FALSE,
    total_candidates INTEGER DEFAULT 0,
    last_turbo_scan  TEXT,
    sources_json     TEXT,
    warnings_json    TEXT,
    meta_json        TEXT,
    started_at       TIMESTAMPTZ DEFAULT NOW(),
    finished_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_usr_univ_tf    ON ultra_scan_runs(universe, tf, nasdaq_batch);
CREATE INDEX IF NOT EXISTS idx_usr_is_latest  ON ultra_scan_runs(is_latest);
CREATE INDEX IF NOT EXISTS idx_usr_status     ON ultra_scan_runs(status);
CREATE INDEX IF NOT EXISTS idx_usr_created_at ON ultra_scan_runs(created_at);
CREATE TABLE IF NOT EXISTS ultra_scan_candidates (
    id          BIGSERIAL PRIMARY KEY,
    scan_run_id INTEGER NOT NULL REFERENCES ultra_scan_runs(id) ON DELETE CASCADE,
    ticker      TEXT NOT NULL,
    ultra_score REAL DEFAULT 0,
    row_json    TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_usc_run_id ON ultra_scan_candidates(scan_run_id);
CREATE INDEX IF NOT EXISTS idx_usc_ticker ON ultra_scan_candidates(ticker);
CREATE INDEX IF NOT EXISTS idx_usc_score  ON ultra_scan_candidates(scan_run_id, ultra_score DESC)
"""

_SEED_MARKER  = "SEED_SAMPLE_3.5"
_SEED_TICKERS = [
    "NVDA","AAPL","MSFT","GOOGL","META","AMZN","TSLA","AVGO","AMD","QCOM",
    "CRM","ORCL","ADBE","SNOW","PLTR","DDOG","ZS","CRWD","NET","PANW",
    "LLY","UNH","ABBV","MRK","BMY","AMGN","GILD","REGN","VRTX","ISRG",
    "JPM","GS","MS","BAC","WFC","BLK","SCHW","AXP","COF","SPGI",
    "HD","LOW","TGT","COST","NKE","SBUX","MCD","CMG","BKNG","MAR",
    "GE","CAT","HON","RTX","LMT","DE","EMR","ETN","PH","ITW",
    "XOM","CVX","COP","SLB","MPC","VLO","OXY","EOG","FANG","HAL",
    "UBER","ABNB","DASH","RBLX","SPOT","TTD","SNAP","PINS","APP","COUR",
    "FCX","NEM","AA","NUE","CF","MOS","LIN","APD","ECL","SHW",
    "T","VZ","TMUS","NFLX","DIS","WBD","PARA","FOXA","OMC","IPG",
]
_SECTORS = ["Technology","Healthcare","Financials","Consumer Discretionary",
            "Industrials","Communication Services","Energy","Materials"]
_T_SIGS  = ["T4","T1G","T2G","T1","T2","T3","T6"]
_REGIMES = ["ACTIONABLE_SETUP","CLEAN_ENTRY","SHAKEOUT_ABSORB","REBOUND_SQUEEZE","NONE"]
_ABR     = ["ACTIVATION","BREAKING","RETEST","NONE"]
_RTB     = ["TREND","BREAKOUT","RANGE","WATCH"]


def _synthetic_row(i: int, ticker: str) -> dict:
    import random
    rng = random.Random(hash(ticker) ^ i ^ 0xFACE)
    score = max(35, min(99, 58 + rng.randint(-23, 41)))
    turbo = max(20, min(95, score - rng.randint(0, 12)))
    bi    = 0 if score >= 90 else 1 if score >= 80 else 2 if score >= 65 else 3 if score >= 50 else 4
    bv2   = ["A+","A","B","C","D"][bi]
    pri   = ["HIGH_PRIORITY","WATCH_A","STRONG_WATCH","CONTEXT_WATCH","LOW"][bi]
    rgm   = rng.choice(_REGIMES)
    reasons = []
    if score >= 80: reasons.append("BUY_2809")
    if score >= 75: reasons.append("MOMO+CAT")
    if rgm != "NONE": reasons.append(f"REGIME:{rgm}")
    abr_val = rng.choice(_ABR)
    if abr_val != "NONE": reasons.append(f"ABR:{abr_val}")
    price = round(rng.uniform(8, 820), 2)
    return {
        "ticker": ticker, "name": f"{ticker} Inc", "sector": _SECTORS[i % len(_SECTORS)],
        "industry": f"{_SECTORS[i%len(_SECTORS)]} Group", "profile": rng.choice(["nasdaq","sp500"]),
        "price": price, "close": price, "change_pct": round(rng.uniform(-3.5, 9.0), 2),
        "volume": rng.randint(400_000, 30_000_000), "avg_vol": rng.randint(500_000, 20_000_000),
        "t_signal": rng.choice(_T_SIGS), "z_signal": rng.choice(["Z1","Z4","",""]),
        "l_signal": rng.choice(["L1","L3","L34","FRI34","BLUE",""]),
        "turbo_score": turbo, "ultra_score": score,
        "ultra_score_band": ["A","A","B","C","D"][bi], "ultra_score_band_v2": bv2,
        "ultra_score_priority": pri, "ultra_score_reasons": reasons,
        "ultra_score_flags": ["MOMENTUM_A"] if rng.random() < 0.3 else [],
        "ultra_score_raw_before_penalty": score + rng.randint(0, 8),
        "ultra_score_penalty_total": rng.randint(0, 5),
        "ultra_score_regime_bonus": 12 if rgm=="ACTIONABLE_SETUP" else 8 if rgm=="CLEAN_ENTRY" else 0,
        "ultra_score_caps_applied": [], "ultra_score_cap_reason": "",
        "buy_2809": score >= 75, "rocket": score >= 88,
        "sig3g": rng.random() < 0.4, "rtv": rng.random() < 0.3,
        "rtb_phase": rng.choice(_RTB), "sweet_spot": score >= 70 and rng.random() < 0.6,
        "abr_category": abr_val, "tz_intel_role": rng.choice(["ACTIVATION","BREAKING","",""]),
        "wlnbb_bucket": rng.choice(["L1","L3","L34","",""]),
        "ema_state": rng.choice(["ABOVE","CROSS_UP","","BELOW"]),
        "action_bucket": rng.choice(["BUY","WATCH","REVIEW",""]),
        "sequence": rng.choice(["T4→T2","T1G→T2G","",""]),
        "sequence_4bar": rng.choice(["T4→T2→T2→T1","",""]),
        "ultra_enriched": True,
        "ultra_sources": {"has_turbo": True, "has_tz_wlnbb": rng.random()<0.7,
                          "has_tz_intel": rng.random()<0.5},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Controlled scan — DB helpers (Phase 5C async refactor)
# ─────────────────────────────────────────────────────────────────────────────

def _build_summary_json(scan_result: dict) -> dict:
    candidates = scan_result["results"]
    band_counts:   dict[str, int] = {}
    sector_counts: dict[str, int] = {}
    signal_counts: dict[str, int] = {}
    top_score = 0
    top_gainer: dict | None = None
    top_loser:  dict | None = None
    for c in candidates:
        b = c.get("band") or "?"
        band_counts[b] = band_counts.get(b, 0) + 1
        s = c.get("sector") or ""
        if s and s != "Unknown":
            sector_counts[s] = sector_counts.get(s, 0) + 1
        if (c.get("ultra_score") or 0) > top_score:
            top_score = c.get("ultra_score") or 0
        for sig in (c.get("signals") or c.get("why_selected") or []):
            if sig:
                signal_counts[sig] = signal_counts.get(sig, 0) + 1
        chg = c.get("change_pct")
        if chg is not None:
            if top_gainer is None or chg > (top_gainer.get("change_pct") or 0):
                top_gainer = {"symbol": c.get("symbol"), "change_pct": chg}
            if top_loser is None or chg < (top_loser.get("change_pct") or 0):
                top_loser = {"symbol": c.get("symbol"), "change_pct": chg}
    return {
        "scan_mode":         scan_result["scan_mode"],
        "score_engine":      scan_result["score_engine"],
        "data_provider":     "massive",
        "symbols_requested": scan_result["symbols_requested"],
        "symbols_scanned":   scan_result["symbols_scanned"],
        "symbols_failed":    scan_result["symbols_failed"],
        "candidates_saved":  scan_result["candidates_saved"],
        "top_score":         top_score,
        "top_gainer":        top_gainer,
        "top_loser":         top_loser,
        "band_counts":       band_counts,
        "sector_counts":     sector_counts,
        "signal_counts":     dict(sorted(signal_counts.items(), key=lambda x: -x[1])),
        "symbol_errors":     scan_result["errors"],
    }


def _create_run_record(
    universe: str, timeframe: str, scan_mode: str,
    symbols_count: int, started_at: datetime,
) -> int:
    """Insert a scan run with status='running'. Returns run_id."""
    from . import db as _db
    import psycopg2.extras

    with _db.get_write_conn() as conn:
        with conn.cursor(cursor_factory=__import__("psycopg2").extras.RealDictCursor) as cur:
            cur.execute(
                f"""INSERT INTO {_RUN_TABLE}
                    (universe, tf, nasdaq_batch, status, is_latest,
                     total_candidates, sources_json, warnings_json, meta_json,
                     started_at, finished_at)
                   VALUES (%s,%s,%s,'running',FALSE,0,%s,%s,%s,%s,NULL)
                   RETURNING id""",
                (
                    universe, timeframe, scan_mode,
                    json.dumps({"source": "scanner-api-async-scan"}),
                    json.dumps([]),
                    json.dumps({"phase": _PHASE, "symbols_count": symbols_count}),
                    started_at.isoformat(),
                ),
            )
            run_id = cur.fetchone()["id"]
        conn.commit()
    return run_id


def _finalize_run(run_id: int, scan_result: dict, replace_latest: bool) -> str:
    """
    Update run status, insert candidates, flip is_latest if success.
    Returns final status string.
    """
    from . import db as _db
    import psycopg2.extras

    candidates   = scan_result["results"]
    cancelled    = scan_result.get("cancelled", False)
    all_failed   = len(candidates) == 0
    final_status = ("cancelled" if cancelled else
                    "failed"    if all_failed else
                    "completed")

    summary  = _build_summary_json(scan_result)
    warnings = [f"{e['symbol']}: {e['error']}" for e in scan_result["errors"]]
    meta     = {
        "score_engine":      scan_result.get("score_engine", ""),
        "elapsed_ms":        scan_result.get("elapsed_ms", 0),
        "symbols_requested": scan_result.get("symbols_requested", 0),
        "symbols_failed":    scan_result.get("symbols_failed", 0),
        "phase":             _PHASE,
        "summary":           summary,
    }

    with _db.get_write_conn() as conn:
        with conn.cursor(cursor_factory=__import__("psycopg2").extras.RealDictCursor) as cur:

            cur.execute(
                f"""UPDATE {_RUN_TABLE} SET
                    status=%s, total_candidates=%s,
                    warnings_json=%s, meta_json=%s, finished_at=%s
                    WHERE id=%s""",
                (
                    final_status, len(candidates),
                    json.dumps(warnings),
                    json.dumps(meta),
                    datetime.now(timezone.utc).isoformat(),
                    run_id,
                ),
            )

            if candidates:
                rows = [
                    (run_id, c["symbol"], float(c["ultra_score"]), json.dumps(c))
                    for c in candidates
                ]
                psycopg2.extras.execute_batch(
                    cur,
                    f"INSERT INTO {_CAND_TABLE} (scan_run_id, ticker, ultra_score, row_json) "
                    "VALUES (%s,%s,%s,%s)",
                    rows, page_size=50,
                )

            # Only flip latest on clean completion with candidates
            if replace_latest and final_status == "completed":
                cur.execute(
                    f"UPDATE {_RUN_TABLE} SET is_latest=FALSE WHERE is_latest=TRUE AND id<>%s",
                    (run_id,),
                )
                cur.execute(
                    f"UPDATE {_RUN_TABLE} SET is_latest=TRUE WHERE id=%s",
                    (run_id,),
                )

        conn.commit()

    return final_status


def _run_scan_background(
    run_id:     int,
    symbols:    list[str],
    req:        ScanRequest,
    started_at: datetime,
) -> None:
    """
    Background scan worker — runs in FastAPI's thread pool.
    Updates Redis/memory progress after each symbol.
    Finalizes DB run on completion, failure, or cancellation.
    """
    global _scan_running, _current_run_id

    from . import progress as _prog
    from .scan_engine import run_controlled_scan

    total = len(symbols)

    def _on_progress(i: int, sym: str, results: list, errors: list) -> None:
        _prog.set_progress(run_id, {
            "run_id":            run_id,
            "status":            "running",
            "universe":          req.universe,
            "timeframe":         req.timeframe,
            "symbols_requested": total,
            "symbols_scanned":   len(results),
            "symbols_failed":    len(errors),
            "candidates_saved":  len(results),
            "current_symbol":    sym,
            "started_at":        started_at.isoformat(),
            "progress_pct":      round(i / total * 100, 1) if total else 0,
            "error":             None,
        })

    try:
        scan_result = run_controlled_scan(
            symbols=symbols,
            timeframe=req.timeframe,
            universe=req.universe,
            scan_mode=req.scan_mode,
            scoring_mode=req.scoring_mode,
            progress_callback=_on_progress,
            cancel_event=_cancel_event,
        )

        if _cancel_event.is_set():
            _cancel_event.clear()

        final_status = _finalize_run(run_id, scan_result, req.replace_latest)
        elapsed = round((datetime.now(timezone.utc) - started_at).total_seconds(), 1)

        _prog.set_progress(run_id, {
            "run_id":            run_id,
            "status":            final_status,
            "universe":          req.universe,
            "timeframe":         req.timeframe,
            "symbols_requested": scan_result["symbols_requested"],
            "symbols_scanned":   scan_result["symbols_scanned"],
            "symbols_failed":    scan_result["symbols_failed"],
            "candidates_saved":  scan_result["candidates_saved"],
            "current_symbol":    None,
            "started_at":        started_at.isoformat(),
            "finished_at":       datetime.now(timezone.utc).isoformat(),
            "duration_seconds":  elapsed,
            "progress_pct":      100.0 if final_status == "completed" else None,
            "error":             ("All symbols failed — previous latest preserved."
                                  if final_status == "failed" else None),
        })
        log.info("Background scan done: run_id=%d status=%s candidates=%d failed=%d elapsed=%.1fs",
                 run_id, final_status, scan_result["symbols_scanned"],
                 scan_result["symbols_failed"], elapsed)

    except Exception as exc:
        log.exception("Background scan error: run_id=%d", run_id)
        try:
            from . import db as _db
            with _db.get_write_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE {_RUN_TABLE} SET status='failed', finished_at=%s WHERE id=%s",
                        (datetime.now(timezone.utc).isoformat(), run_id),
                    )
                conn.commit()
        except Exception:
            pass
        from . import progress as _prog
        _prog.set_progress(run_id, {
            "run_id": run_id, "status": "failed",
            "error": type(exc).__name__,
        })
    finally:
        with _scan_lock:
            _scan_running   = False
            _current_run_id = None


@app.post("/api/scans/ultra/run")
def run_ultra_scan(req: ScanRequest, background_tasks: BackgroundTasks):
    """
    Start a bounded async controlled Ultra scan. Max 500 symbols.
    dry_run=true validates without fetching or writing.
    Returns run_id immediately; scan runs in background.
    """
    global _scan_running, _current_run_id

    symbols = req.symbols or _DEFAULT_SYMBOLS

    # ── Dry run ───────────────────────────────────────────────────────────────
    if req.dry_run:
        return {
            "dry_run":      True,
            "valid":        True,
            "symbols_count":len(symbols),
            "symbols":      symbols,
            "timeframe":    req.timeframe,
            "universe":     req.universe,
            "scan_mode":    req.scan_mode,
            "would_scan":   True,
            "max_symbols":  _MAX_SYMBOLS,
        }

    # ── Concurrency check ─────────────────────────────────────────────────────
    with _scan_lock:
        if _scan_running:
            return JSONResponse(
                status_code=409,
                content={
                    "accepted":       False,
                    "error":          "Another Ultra scan is already running.",
                    "running_run_id": _current_run_id,
                },
            )
        _scan_running = True

    try:
        from . import db as _db
        from . import progress as _prog

        if not _db.DATABASE_URL:
            with _scan_lock:
                _scan_running = False
            return JSONResponse(
                status_code=503,
                content={"accepted": False, "error": "DATABASE_URL not configured"},
            )

        started_at = datetime.now(timezone.utc)
        run_id = _create_run_record(
            universe=req.universe,
            timeframe=req.timeframe,
            scan_mode=req.scan_mode,
            symbols_count=len(symbols),
            started_at=started_at,
        )

        with _scan_lock:
            _current_run_id = run_id

        # Seed initial progress
        _prog.set_progress(run_id, {
            "run_id":            run_id,
            "status":            "running",
            "universe":          req.universe,
            "timeframe":         req.timeframe,
            "symbols_requested": len(symbols),
            "symbols_scanned":   0,
            "symbols_failed":    0,
            "candidates_saved":  0,
            "current_symbol":    symbols[0] if symbols else None,
            "started_at":        started_at.isoformat(),
            "progress_pct":      0.0,
            "error":             None,
        })

        background_tasks.add_task(_run_scan_background, run_id, symbols, req, started_at)

        log.info("Phase 5C async scan started: run_id=%d symbols=%d universe=%s",
                 run_id, len(symbols), req.universe)

        return {
            "accepted":          True,
            "run_id":            run_id,
            "status":            "running",
            "symbols_requested": len(symbols),
            "universe":          req.universe,
            "timeframe":         req.timeframe,
            "message":           "Controlled Ultra scan started.",
        }

    except Exception as exc:
        with _scan_lock:
            _scan_running   = False
            _current_run_id = None
        log.exception("run_ultra_scan setup error")
        return JSONResponse(status_code=500,
                            content={"accepted": False, "error": type(exc).__name__})


@app.get("/api/scans/ultra/status")
def ultra_scan_status(run_id: int | None = Query(default=None)):
    """
    Return scan progress/status.
    run_id: specific run (defaults to latest running or most recent).
    """
    from . import db as _db
    from . import progress as _prog

    # ── Determine which run_id to inspect ────────────────────────────────────
    target_id = run_id
    if target_id is None:
        with _scan_lock:
            target_id = _current_run_id  # running scan takes priority

    # ── Try progress store first (live data) ─────────────────────────────────
    if target_id is not None:
        prog = _prog.get_progress(target_id)
        if prog:
            running = prog.get("status") == "running"
            out = {**prog, "running": running}
            if running:
                total = prog.get("symbols_requested", 0)
                scanned = prog.get("symbols_scanned", 0)
                out["progress_pct"] = round(scanned / total * 100, 1) if total else 0
            return out

    # ── Fallback to DB ────────────────────────────────────────────────────────
    db_row: dict = {}
    if _db.DATABASE_URL:
        try:
            with _db.get_conn() as cur:
                if target_id is not None:
                    cur.execute(
                        f"SELECT id, universe, tf, status, total_candidates, started_at, finished_at "
                        f"FROM {_RUN_TABLE} WHERE id=%s",
                        (target_id,),
                    )
                else:
                    cur.execute(
                        f"SELECT id, universe, tf, status, total_candidates, started_at, finished_at "
                        f"FROM {_RUN_TABLE} ORDER BY id DESC LIMIT 1"
                    )
                row = cur.fetchone()
                if row:
                    db_row = dict(row)
        except Exception:
            pass

    if not db_row:
        return {"running": False, "run_id": None, "status": None, "error": "No scan found"}

    st = db_row.get("status")
    fa = db_row.get("finished_at")
    sa = db_row.get("started_at")
    duration = None
    if sa and fa:
        try:
            duration = round((fa - sa).total_seconds(), 1)
        except Exception:
            pass

    return {
        "running":           st == "running",
        "run_id":            db_row.get("id"),
        "status":            st,
        "universe":          db_row.get("universe"),
        "timeframe":         db_row.get("tf"),
        "symbols_requested": None,
        "symbols_scanned":   None,
        "symbols_failed":    None,
        "candidates_saved":  db_row.get("total_candidates") or 0,
        "current_symbol":    None,
        "started_at":        str(sa) if sa else None,
        "finished_at":       str(fa) if fa else None,
        "duration_seconds":  duration,
        "error":             None,
    }


@app.post("/api/scans/ultra/cancel")
def cancel_ultra_scan():
    """Request cancellation of the currently running scan."""
    with _scan_lock:
        if not _scan_running:
            return {"cancelled": False, "message": "No scan is currently running."}
        run_id = _current_run_id

    _cancel_event.set()
    return {
        "cancelled":        True,
        "run_id":           run_id,
        "message":          "Cancel requested. Scan will stop after current symbol completes.",
    }


@app.get("/api/debug/scan-config")
def debug_scan_config():
    from . import progress as _prog
    return {
        "phase":                    _PHASE,
        "max_symbols":              _MAX_SYMBOLS,
        "allowed_timeframes":       _ALLOWED_TIMEFRAMES,
        "allowed_universes":        sorted(_ALLOWED_UNIVERSES),
        "default_symbols":          _DEFAULT_SYMBOLS,
        "scheduler_enabled":        _SCHEDULER_ENABLED,
        "full_market_scan_enabled": False,
        "async_scan":               True,
        "redis_progress":           _prog.redis_available(),
        "scoring_modes":            sorted(_ALLOWED_SCORING_MODES),
        "default_scoring_mode":     "real",
        "score_engine_real":        "real_ultra_score",
        "score_engine_temporary":   "temporary_phase_5A",
        "notes": [
            "Phase 7B: real Ultra scoring is now the default (scoring_mode=real).",
            "scoring_mode=real: production compute_ultra_score() — default.",
            "scoring_mode=temporary: legacy rule-based EMA/RSI/volume scorer.",
            "scoring_mode=compare: runs both, stores delta in candidate.compare field.",
            "GET /api/debug/score-compare?symbol=AAPL&tf=1d for single-symbol test.",
            "Scheduler remains disabled.",
        ],
    }


@app.get("/api/scans/ultra/sample-lists")
def ultra_sample_lists():
    return {
        "manual_default":  _DEFAULT_SYMBOLS,
        "sp500_sample":    _SP500_SAMPLE,
        "nasdaq_sample":   _NASDAQ_SAMPLE,
        "watchlist_sample":[],
        "custom_sample":   [],
        "max_symbols":     _MAX_SYMBOLS,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Staging seeder (Phase 3.5, remove in Phase 6)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/admin/seed")
def admin_seed(x_seed_token: str = Header(default="")):
    """
    One-time staging DB seeder. Protected by SEED_TOKEN env var.
    Creates schema + inserts 100 synthetic candidates. Idempotent.
    REMOVE in Phase 4.
    """
    expected = os.environ.get("SEED_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="SEED_TOKEN not configured on this service")
    if not secrets.compare_digest(x_seed_token, expected):
        raise HTTPException(status_code=401, detail="Invalid seed token")

    from . import db as _db
    import psycopg2
    import psycopg2.extras

    if not _db.DATABASE_URL:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")

    results: dict = {"schema_created": False, "run_id": None, "candidates_inserted": 0,
                     "skipped": False, "error": None}
    try:
        conn = psycopg2.connect(_db.DATABASE_URL, connect_timeout=10)
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Create schema
        for stmt in [s.strip() for s in _DDL_SCHEMA.split(";") if s.strip()]:
            cur.execute(stmt)
        conn.commit()
        results["schema_created"] = True

        # Idempotency check
        cur.execute("SELECT id FROM ultra_scan_runs WHERE nasdaq_batch=%s LIMIT 1", (_SEED_MARKER,))
        existing = cur.fetchone()
        if existing:
            results["skipped"] = True
            results["run_id"] = existing["id"]
            cur.close(); conn.close()
            return results

        # Flip existing is_latest
        cur.execute("UPDATE ultra_scan_runs SET is_latest=FALSE WHERE universe='sp500' AND tf='1d'")

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        cur.execute(
            """INSERT INTO ultra_scan_runs
               (universe, tf, nasdaq_batch, status, is_latest, total_candidates,
                sources_json, warnings_json, started_at, finished_at)
               VALUES ('sp500','1d',%s,'completed',TRUE,100,%s,%s,%s,%s)
               RETURNING id""",
            (_SEED_MARKER, json.dumps({"source":"seed_endpoint"}), json.dumps([]), now, now),
        )
        run_id = cur.fetchone()["id"]

        rows = [
            (run_id, t, float(c["ultra_score"]), json.dumps(c))
            for i, t in enumerate(_SEED_TICKERS)
            for c in [_synthetic_row(i, t)]
        ]
        psycopg2.extras.execute_batch(
            cur,
            "INSERT INTO ultra_scan_candidates (scan_run_id, ticker, ultra_score, row_json) "
            "VALUES (%s,%s,%s,%s)",
            rows, page_size=100,
        )
        conn.commit()
        cur.close(); conn.close()

        results["run_id"] = run_id
        results["candidates_inserted"] = len(rows)
        log.info("Seed complete: run_id=%s candidates=%s", run_id, len(rows))
        return results

    except Exception as exc:
        log.exception("admin_seed error")
        results["error"] = type(exc).__name__
        return JSONResponse(status_code=500, content=results)

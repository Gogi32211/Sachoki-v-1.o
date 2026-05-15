"""
dashboard BFF — Phase 8E-1: chart proxy endpoints forwarding to scanner-api.

Serves a minimal static HTML/JS/CSS frontend at / and keeps all BFF API
routes intact. No scans, no scoring, no DB writes, no AI/live prices.
Dashboard BFF does not call Massive directly — only scanner-api does.
"""
from __future__ import annotations

import logging
import os
import pathlib
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import scanner_client as scanner
# Re-export so any external import of `SCANNER_API_URL` from this module
# still resolves (used by tests + ops scripts).
SCANNER_API_URL  = scanner.SCANNER_API_URL
RESEARCH_API_URL = scanner.RESEARCH_API_URL

log = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "info").upper())

app = FastAPI(title="dashboard", version="0.6.0")

_VERSION = "0.8.0"
_PHASE   = "8E-history-timeline"

_FRONTEND_DIR = pathlib.Path(__file__).parent.parent / "frontend"

# Mount static assets (JS, CSS) — must come before catch-all route
if _FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIR)), name="static")

_VALID_SYM_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")
_CHART_ALLOWED_TF = {"1d"}   # Phase 8E-1: daily only
_CHART_MIN_BARS   = 20
_CHART_MAX_BARS   = 250


# ─────────────────────────────────────────────────────────────────────────────
# Upstream call wrappers — declarative.
#
# Single source of truth for "path → registered endpoint name" lives here.
# All actual HTTP policy (timeout, retry, error mapping) is owned by
# scanner_client.ENDPOINTS.
#
# Wrappers preserve the legacy (data, error_string) tuple shape so existing
# call sites stay one-line refactors. New call sites should prefer
# `_scanner_call(name, ...)` directly to receive a structured UpstreamError.
# ─────────────────────────────────────────────────────────────────────────────

_PATH_TO_ENDPOINT: dict[str, str] = {
    "/health":                                 "scanner_health",
    "/version":                                "scanner_version",
    "/api/chart/signals":                      "scanner_signals",
    "/api/chart/candles":                      "chart_candles",
    "/api/chart/score":                        "chart_score",
    "/api/chart/snapshot":                     "chart_snapshot",
    "/api/chart/history":                      "chart_history",
    "/api/scans/ultra/sample-lists":           "sample_lists",
    "/api/scans/ultra/split-universe":         "split_universe",
    "/api/scans/ultra/run":                    "scan_run",
    "/api/scans/ultra/status":                 "scan_status",
    "/api/scans/ultra/cancel":                 "scan_cancel",
    "/api/scans/ultra/latest":                 "scan_latest",
    "/api/scans/ultra/latest/candidates":      "scan_latest_candidates",
}


def _scanner_call(
    name: str,
    *,
    params: dict | None = None,
    body:   dict | None = None,
) -> tuple[dict | None, scanner.UpstreamError | None]:
    """Direct path: registered endpoint name → structured (data, error)."""
    return scanner.call(name, params=params, body=body)


def _resolve(path: str) -> str:
    ep = _PATH_TO_ENDPOINT.get(path)
    if not ep:
        raise KeyError(f"upstream path {path!r} is not registered in _PATH_TO_ENDPOINT")
    return ep


def _scanner_get(path: str, params: dict | None = None) -> tuple[dict | None, str | None]:
    """Legacy wrapper — returns (data, error_string) to keep existing
    call sites untouched. New code should use _scanner_call() for the
    structured UpstreamError shape."""
    data, err = scanner.call(_resolve(path), params=params)
    return data, (err.message if err else None)


def _chart_get(path: str, params: dict | None = None) -> tuple[dict | None, str | None]:
    """Legacy chart-specific wrapper. Timeout/retry policy lives in
    scanner_client.ENDPOINTS, so this is now identical to _scanner_get."""
    return _scanner_get(path, params=params)


def _scanner_post(path: str, body: dict | None = None) -> tuple[dict | None, str | None]:
    """Legacy POST wrapper — see _scanner_get."""
    data, err = scanner.call(_resolve(path), body=body)
    return data, (err.message if err else None)


def _validate_chart_sym(symbol: str) -> str | None:
    """Return uppercased symbol or None if invalid."""
    s = symbol.upper().strip()
    return s if _VALID_SYM_RE.match(s) else None


def _err_response(err: scanner.UpstreamError) -> JSONResponse:
    """Map a structured UpstreamError to an HTTP response with `error_code`."""
    return JSONResponse(
        status_code=scanner.err_to_http_status(err),
        content=scanner.err_response_body(err),
    )


# ─────────────────────────────────────────────────────────────────────────────
# BFF helpers
# ─────────────────────────────────────────────────────────────────────────────

_BAND_ORDER: dict[str, int] = {"A+": 0, "A": 1, "B": 2, "C": 3, "D": 4}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _band_summary(candidates: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for c in candidates:
        b = c.get("band") or "?"
        counts[b] = counts.get(b, 0) + 1
    return counts


def _sector_summary(candidates: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for c in candidates:
        s = c.get("sector") or "Unknown"
        counts[s] = counts.get(s, 0) + 1
    return counts


def _setup_reason(c: dict) -> list[str]:
    reasons: list[str] = []
    score  = c.get("ultra_score") or 0
    band   = c.get("band") or ""
    chg    = c.get("change_pct")
    engine = c.get("score_engine") or ""
    reasons.append(f"Ultra score {score}")
    if band:
        reasons.append(f"Band {band}")
    if chg is not None:
        reasons.append("Positive change_pct" if chg >= 0 else f"Change {chg:+.2f}%")
    if "real_ultra" in engine:
        reasons.append("Real Ultra score")
    return reasons


def _build_setups(
    candidates: list[dict],
    n:          int        = 5,
    min_score:  int        = 70,
    bands:      list[str] | None = None,
    sector:     str | None = None,
) -> dict:
    """Rule-based best-setups selection. Returns {setups, fallback}."""
    eligible = [
        c for c in candidates
        if (c.get("ultra_score") or 0) >= min_score
        and (c.get("band") or "D") != "D"
        and (not bands  or c.get("band") in bands)
        and (not sector or c.get("sector") == sector)
    ]
    fallback = False
    if not eligible:
        fallback = True
        pool = sorted(candidates, key=lambda x: x.get("ultra_score") or 0, reverse=True)
        eligible = [c for c in pool if (not sector or c.get("sector") == sector)][:3]

    eligible.sort(key=lambda c: (
        -(c.get("ultra_score") or 0),
        _BAND_ORDER.get(c.get("band") or "D", 4),
        -(c.get("change_pct") or 0),
    ))

    setups = [
        {
            "symbol":       c.get("symbol", ""),
            "sector":       c.get("sector", ""),
            "industry":     c.get("industry", ""),
            "ultra_score":  c.get("ultra_score") or 0,
            "band":         c.get("band", ""),
            "final_signal": c.get("final_signal", ""),
            "change_pct":   c.get("change_pct"),
            "why_selected": list(c.get("why_selected") or []),
            "risk_flags":   list(c.get("risk_flags") or []),
            "setup_reason": _setup_reason(c),
            "score_engine": c.get("score_engine", ""),
        }
        for c in eligible[:n]
    ]
    return {"setups": setups, "fallback": fallback}


def _mover_shape(c: dict) -> dict:
    return {
        "symbol":       c.get("symbol", ""),
        "sector":       c.get("sector", ""),
        "industry":     c.get("industry", ""),
        "price":        c.get("price"),
        "prev_close":   c.get("prev_close"),
        "change_pct":   c.get("change_pct"),
        "ultra_score":  c.get("ultra_score") or 0,
        "band":         c.get("band", ""),
        "final_signal": c.get("final_signal", ""),
        "why_selected": list(c.get("why_selected") or []),
        "risk_flags":   list(c.get("risk_flags") or []),
        "score_engine": c.get("score_engine", ""),
    }


def _build_top_movers(
    candidates: list[dict],
    n:          int        = 5,
    min_score:  int | None = None,
    sector:     str | None = None,
) -> dict:
    """Sort by change_pct for gainers/losers. Returns {gainers, losers, stats}."""
    pool = [
        c for c in candidates
        if c.get("change_pct") is not None
        and (min_score is None or (c.get("ultra_score") or 0) >= min_score)
        and (not sector or c.get("sector") == sector)
    ]
    gainers = sorted(pool, key=lambda x: x["change_pct"], reverse=True)[:n]
    losers  = sorted(pool, key=lambda x: x["change_pct"])[:n]
    with_chg    = sum(1 for c in candidates if c.get("change_pct") is not None)
    without_chg = len(candidates) - with_chg
    return {
        "gainers": [_mover_shape(c) for c in gainers],
        "losers":  [_mover_shape(c) for c in losers],
        "stats": {
            "total_candidates":  len(candidates),
            "with_change_pct":   with_chg,
            "without_change_pct": without_chg,
        },
    }


def _fetch_all_candidates(total: int) -> list[dict]:
    """Fetch up to 500 candidates from scanner-api latest run."""
    limit = min(max(total, 1), 500)
    data, _ = _scanner_get("/api/scans/ultra/latest/candidates", params={"limit": limit})
    return data.get("candidates", []) if data else []


# ─────────────────────────────────────────────────────────────────────────────
# Core endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "dashboard"}


@app.get("/version")
def version():
    return {"service": "dashboard", "version": _VERSION, "phase": _PHASE}


@app.get("/", include_in_schema=False)
def index():
    index_file = _FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), media_type="text/html")
    return {"service": "dashboard", "version": _VERSION, "phase": _PHASE}


# ─────────────────────────────────────────────────────────────────────────────
# Debug
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/debug/status")
def debug_status():
    scanner_url_ok = bool(SCANNER_API_URL)
    scanner_reachable = False
    scanner_health: dict | None = None
    chart_snapshot_reachable = False

    if scanner_url_ok:
        data, err = _scanner_get("/health")
        if data and data.get("status") == "ok":
            scanner_reachable = True
            scanner_health = data
        else:
            scanner_health = {"error": err}

        # Probe chart proxy with a lightweight signals endpoint (no Massive fetch)
        sig_data, _ = _chart_get("/api/chart/signals")
        chart_snapshot_reachable = sig_data is not None and "implemented" in sig_data

    return {
        "service":                          "dashboard",
        "mode":                             "scanner_api_bridge_phase",
        "database_configured":              bool(os.getenv("DATABASE_URL")),
        "redis_configured":                 bool(os.getenv("REDIS_URL")),
        "scanner_api_url_configured":       scanner_url_ok,
        "scanner_api_reachable":            scanner_reachable,
        "scanner_api_health":               scanner_health,
        "research_api_url_configured":      bool(RESEARCH_API_URL),
        "massive_configured":               bool(os.getenv("MASSIVE_API_KEY")),
        "anthropic_configured":             bool(os.getenv("ANTHROPIC_API_KEY")),
        "chart_proxy_available":            scanner_url_ok,
        "scanner_chart_snapshot_reachable": chart_snapshot_reachable,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/dashboard/bootstrap")
def dashboard_bootstrap():
    # 1 — get latest scan metadata
    scan_data, scan_err = _scanner_get("/api/scans/ultra/latest")

    if scan_data is None:
        return {
            "dashboard_state": "ERROR",
            "top_candidates":  [],
            "best_setups":     [],
            "error":           "Scanner API unreachable",
        }

    if not scan_data.get("has_data"):
        return {
            "dashboard_state": "NO_SCAN",
            "latest_scan":     {"has_data": False},
            "top_candidates":  [],
            "best_setups":     [],
            "message":         scan_data.get("message", "No completed Ultra Scan found in scanner-api."),
        }

    run = scan_data.get("run", {})

    # 2 — get all candidates (up to 500)
    total = run.get("total_candidates") or 0
    candidates = _fetch_all_candidates(total)

    top_score  = max((c.get("ultra_score") or 0 for c in candidates), default=0)
    setups_res = _build_setups(candidates, n=5)
    movers_res = _build_top_movers(candidates, n=5)

    return {
        "dashboard_state": "SCAN_READY",
        "latest_scan": {
            "has_data":         True,
            "scan_run_id":      run.get("id"),
            "status":           run.get("status"),
            "universe":         run.get("universe"),
            "timeframe":        run.get("timeframe"),
            "finished_at":      run.get("finished_at"),
            "total_candidates": total,
            "source":           "scanner-api",
        },
        "summary": {
            "total_candidates": total,
            "top_score":        top_score,
            "bands":            _band_summary(candidates),
            "sectors":          _sector_summary(candidates),
        },
        "top_candidates": candidates,
        "best_setups":    setups_res["setups"],
        "top_movers": {
            "regular": {
                "gainers": movers_res["gainers"],
                "losers":  movers_res["losers"],
            },
        },
        "data_health": {
            "scanner_api": {
                "reachable": True,
                "source":    "scanner-api",
            },
            "ultra": {
                "status":           run.get("status"),
                "last_run_at":      run.get("finished_at"),
                "total_candidates": total,
                "source":           "scanner-api",
            },
        },
    }


@app.get("/api/dashboard/top-movers")
def dashboard_top_movers(
    limit:     int       = Query(default=5,  ge=1, le=50),
    source:    str       = Query(default="latest_ultra"),
    min_score: int | None = Query(default=None),
    sector:    str | None = Query(default=None),
):
    scan_data, _ = _scanner_get("/api/scans/ultra/latest")
    if not scan_data or not scan_data.get("has_data"):
        return {
            "source":       "scanner-api",
            "scan_run_id":  None,
            "generated_at": _now_iso(),
            "regular":      {"gainers": [], "losers": []},
            "stats":        {"total_candidates": 0, "with_change_pct": 0, "without_change_pct": 0},
            "message":      "No change_pct data available.",
        }

    run        = scan_data.get("run", {})
    total      = run.get("total_candidates") or 0
    candidates = _fetch_all_candidates(total)
    movers     = _build_top_movers(candidates, n=limit, min_score=min_score, sector=sector)

    return {
        "source":       "scanner-api",
        "scan_run_id":  run.get("id"),
        "generated_at": _now_iso(),
        "regular": {
            "gainers": movers["gainers"],
            "losers":  movers["losers"],
        },
        "stats": movers["stats"],
    }


@app.get("/api/dashboard/best-setups")
def dashboard_best_setups(
    limit:     int       = Query(default=5,  ge=1, le=50),
    min_score: int       = Query(default=70),
    bands:     str | None = Query(default=None),
    sector:    str | None = Query(default=None),
):
    scan_data, _ = _scanner_get("/api/scans/ultra/latest")
    if not scan_data or not scan_data.get("has_data"):
        return {
            "source":       "scanner-api",
            "scan_run_id":  None,
            "generated_at": _now_iso(),
            "setups":       [],
            "fallback":     False,
            "message":      "No best setups found for current scan.",
        }

    run        = scan_data.get("run", {})
    total      = run.get("total_candidates") or 0
    candidates = _fetch_all_candidates(total)
    bands_list = [b.strip() for b in bands.split(",")] if bands else None
    result     = _build_setups(candidates, n=limit, min_score=min_score, bands=bands_list, sector=sector)

    return {
        "source":       "scanner-api",
        "scan_run_id":  run.get("id"),
        "generated_at": _now_iso(),
        "setups":       result["setups"],
        "fallback":     result["fallback"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Chart proxy endpoints  (Phase 8E-1)
# ─────────────────────────────────────────────────────────────────────────────

def _chart_proxy_error(sym: str | None, tf: str, bars: int | None = None) -> JSONResponse | None:
    """Return a 422 response for invalid chart params, or None if all ok."""
    if sym is None:
        return JSONResponse(status_code=422, content={"error": "Invalid ticker symbol"})
    if tf not in _CHART_ALLOWED_TF:
        return JSONResponse(status_code=422,
                            content={"error": f"tf must be one of {sorted(_CHART_ALLOWED_TF)}"})
    if bars is not None and not (_CHART_MIN_BARS <= bars <= _CHART_MAX_BARS):
        return JSONResponse(status_code=422,
                            content={"error": f"bars must be {_CHART_MIN_BARS}–{_CHART_MAX_BARS}"})
    return None


@app.get("/api/dashboard/chart/candles")
def dashboard_chart_candles(
    symbol: str = Query(...),
    tf:     str = Query(default="1d"),
    bars:   int = Query(default=150),
):
    """Proxy /api/chart/candles from scanner-api. No Massive calls here."""
    sym = _validate_chart_sym(symbol)
    err = _chart_proxy_error(sym, tf, bars)
    if err:
        return err

    data, api_err = _chart_get("/api/chart/candles",
                                params={"symbol": sym, "tf": tf, "bars": bars})
    if data is None:
        return JSONResponse(status_code=503,
                            content={"ok": False, "error": api_err or "scanner-api unavailable"})

    data["source"]       = "dashboard-bff"
    data["proxied_from"] = "scanner-api"
    return data


@app.get("/api/dashboard/chart/score")
def dashboard_chart_score(
    symbol: str = Query(...),
    tf:     str = Query(default="1d"),
):
    """Proxy /api/chart/score from scanner-api. No Massive calls here."""
    sym = _validate_chart_sym(symbol)
    err = _chart_proxy_error(sym, tf)
    if err:
        return err

    data, api_err = _chart_get("/api/chart/score",
                                params={"symbol": sym, "tf": tf})
    if data is None:
        return JSONResponse(status_code=503,
                            content={"ok": False, "error": api_err or "scanner-api unavailable"})

    data["source"]       = "dashboard-bff"
    data["proxied_from"] = "scanner-api"
    return data


@app.get("/api/dashboard/chart/snapshot")
def dashboard_chart_snapshot(
    symbol: str = Query(...),
    tf:     str = Query(default="1d"),
    bars:   int = Query(default=150),
):
    """
    Proxy /api/chart/snapshot from scanner-api.
    Main endpoint for Phase 8E-2 Superchart Preview UI.
    """
    sym = _validate_chart_sym(symbol)
    err = _chart_proxy_error(sym, tf, bars)
    if err:
        return err

    data, api_err = _chart_get("/api/chart/snapshot",
                                params={"symbol": sym, "tf": tf, "bars": bars})
    if data is None:
        return JSONResponse(status_code=503,
                            content={"ok": False, "error": api_err or "scanner-api unavailable"})

    data["source"]       = "dashboard-bff"
    data["proxied_from"] = "scanner-api"
    return data


@app.get("/api/dashboard/chart/signals")
def dashboard_chart_signals(
    symbol: str | None = Query(default=None),
    tf:     str        = Query(default="1d"),
    bars:   int        = Query(default=150),
):
    """
    Proxy /api/chart/signals from scanner-api (implemented vs. missing signal groups).
    symbol/tf/bars accepted for forward-compat but not forwarded — signals is static.
    """
    data, api_err = _chart_get("/api/chart/signals")
    if data is None:
        return {
            "ok":             False,
            "not_implemented": True,
            "error":          api_err or "scanner-api unavailable",
            "source":         "dashboard-bff",
        }

    data["source"]       = "dashboard-bff"
    data["proxied_from"] = "scanner-api"
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Super Chart History proxy — Phase 8E
# ─────────────────────────────────────────────────────────────────────────────

_HISTORY_MAX_LOOKBACK     = 120
_HISTORY_DEFAULT_LOOKBACK = 60
_HISTORY_ALLOWED_TF       = {"1d"}


@app.get("/api/dashboard/super-chart/history")
def super_chart_history(
    ticker:   str = Query(...),
    timeframe: str = Query(default="1d"),
    lookback:  int = Query(default=_HISTORY_DEFAULT_LOOKBACK, ge=10, le=_HISTORY_MAX_LOOKBACK),
):
    """
    Proxy /api/chart/history from scanner-api.
    Returns per-bar T/Z + WLNBB signals grouped into timeline rows.
    """
    sym = _validate_chart_sym(ticker)
    if sym is None:
        return JSONResponse(status_code=422,
                            content={"ok": False, "error": "invalid ticker symbol"})
    if timeframe not in _HISTORY_ALLOWED_TF:
        return JSONResponse(status_code=422,
                            content={"ok": False, "error": f"timeframe must be one of {sorted(_HISTORY_ALLOWED_TF)}"})

    data, err = _chart_get(
        "/api/chart/history",
        params={"symbol": sym, "tf": timeframe, "lookback": lookback},
    )
    if data is None:
        return JSONResponse(
            status_code=503,
            content={
                "ok":      False,
                "ticker":  sym,
                "bars":    [],
                "error":   err or "scanner-api unavailable",
                "source":  "dashboard-bff",
            },
        )

    data["source"]      = "dashboard-bff"
    data["proxied_from"] = "scanner-api"
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Scan proxy — Phase 8D
# Dashboard BFF does not call Massive. Scanner-api owns the scan.
# ─────────────────────────────────────────────────────────────────────────────

# Maps frontend universe keys to scanner-api list keys
_UNIVERSE_MAP: dict[str, str] = {
    "sp500_sample":   "sp500_sample",
    "nasdaq_sample":  "nasdaq_sample",
    "manual_default": "manual_default",
}

_VALID_UNIVERSES = set(_UNIVERSE_MAP.keys())
_VALID_SCORING   = {"real", "compare"}
_VALID_TF_SCAN   = {"1d"}


@app.get("/api/dashboard/scans/ultra/sample-lists")
def scan_sample_lists():
    """Proxy scanner-api sample-lists for the frontend universe selector."""
    data, err = _scanner_get("/api/scans/ultra/sample-lists")
    if data is None:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": err or "scanner-api unavailable", "source": "dashboard-bff"},
        )
    data["source"] = "dashboard-bff"
    return data


@app.get("/api/dashboard/scans/ultra/split-universe")
def scan_split_universe():
    """Proxy scanner-api split-universe (warms the NASDAQ splits cache).
    Frontend calls this lazily in the background after sample-lists loads
    if `split_cache_warm=false`; sample-lists itself never blocks on NASDAQ."""
    data, err = _scanner_call("split_universe")
    if err is not None:
        return _err_response(err)
    data["source"] = "dashboard-bff"
    return data


@app.post("/api/dashboard/scans/ultra/run")
async def scan_ultra_run(request: Request):
    """
    Kick off a new Ultra scan via scanner-api.
    Accepts JSON body: {symbol_count, universe, scoring_mode, timeframe, replace_latest}.
    Fetches sample-lists from scanner-api to resolve actual symbols, slices to count,
    then forwards symbols[] array to scanner-api POST /api/scans/ultra/run.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON body"})

    universe     = str(body.get("universe", "sp500_sample"))
    symbol_count = int(body.get("symbol_count", 25))
    scoring_mode = str(body.get("scoring_mode", "real"))
    timeframe    = str(body.get("timeframe", "1d"))
    replace      = bool(body.get("replace_latest", True))

    if universe not in _VALID_UNIVERSES:
        return JSONResponse(status_code=422, content={"ok": False, "error": f"unknown universe: {universe}"})
    if scoring_mode not in _VALID_SCORING:
        return JSONResponse(status_code=422, content={"ok": False, "error": f"unknown scoring_mode: {scoring_mode}"})
    if timeframe not in _VALID_TF_SCAN:
        return JSONResponse(status_code=422, content={"ok": False, "error": f"unsupported timeframe: {timeframe}"})
    if not (1 <= symbol_count <= 500):
        return JSONResponse(status_code=422, content={"ok": False, "error": "symbol_count must be 1–500"})

    # Resolve list key and fetch symbols from scanner-api
    list_key = _UNIVERSE_MAP[universe]
    lists_data, lists_err = _scanner_get("/api/scans/ultra/sample-lists")
    if lists_data is None:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": lists_err or "cannot fetch sample-lists", "source": "dashboard-bff"},
        )

    # scanner-api returns lists at the top level (no "lists" wrapper)
    symbols_pool: list[str] = lists_data.get(list_key, [])
    if not symbols_pool:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "error": f"no symbols found for list '{list_key}'"},
        )

    symbols = symbols_pool[:symbol_count]

    scan_body = {
        "symbols":        symbols,
        "scoring_mode":   scoring_mode,
        "timeframe":      timeframe,
        "replace_latest": replace,
    }
    # Direct registry call returns structured UpstreamError so the frontend
    # can match on `error_code` (UPSTREAM_TIMEOUT, …) instead of parsing the
    # message string. This is the route where it matters most — scan_run is
    # the only "ack" endpoint we have, and the frontend has a special
    # fallback (poll status without run_id) for UPSTREAM_TIMEOUT.
    data, err = _scanner_call("scan_run", body=scan_body)
    if err is not None:
        return _err_response(err)

    data["source"]    = "dashboard-bff"
    data["universe"]  = universe
    data["list_key"]  = list_key
    data["requested"] = len(symbols)
    return data


@app.get("/api/dashboard/scans/ultra/status")
def scan_ultra_status(run_id: str | None = Query(default=None)):
    """Proxy scanner-api scan status. Optionally filter by run_id."""
    params = {}
    if run_id:
        params["run_id"] = run_id
    data, err = _scanner_call("scan_status", params=params or None)
    if err is not None:
        return _err_response(err)
    data["source"] = "dashboard-bff"
    return data


@app.post("/api/dashboard/scans/ultra/cancel")
async def scan_ultra_cancel(request: Request):
    """Proxy a cancel request to scanner-api."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    data, err = _scanner_post("/api/scans/ultra/cancel", body=body)
    if data is None:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": err or "scanner-api unavailable", "source": "dashboard-bff"},
        )
    data["source"] = "dashboard-bff"
    return data


@app.get("/api/dashboard/top-candidates")
def dashboard_top_candidates(
    limit:    int = Query(default=50, ge=1, le=500),
    offset:   int = Query(default=0, ge=0),
    sort_by:  str = Query(default="ultra_score"),
    sort_dir: str = Query(default="desc"),
):
    data, err = _scanner_get(
        "/api/scans/ultra/latest/candidates",
        params={"limit": limit, "offset": offset,
                "sort_by": sort_by, "sort_dir": sort_dir},
    )

    if data is None:
        return JSONResponse(
            status_code=503,
            content={
                "has_data":        False,
                "source":          "scanner-api",
                "scan_run_id":     None,
                "count":           0,
                "total_available": 0,
                "candidates":      [],
                "error":           "Scanner API unreachable",
            },
        )

    return {
        "has_data":        data.get("has_data", False),
        "source":          "scanner-api",
        "scan_run_id":     data.get("scan_run_id"),
        "universe":        data.get("universe"),
        "timeframe":       data.get("timeframe"),
        "count":           data.get("count", 0),
        "total_available": data.get("total_available", 0),
        "limit":           limit,
        "offset":          offset,
        "candidates":      data.get("candidates", []),
    }

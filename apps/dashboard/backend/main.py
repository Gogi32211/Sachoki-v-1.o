"""
dashboard BFF — Phase 4B: scanner-api bridge + static frontend preview.

Serves a minimal static HTML/JS/CSS frontend at / and keeps all BFF API
routes intact. No scans, no scoring, no DB writes, no AI/live prices.
"""
from __future__ import annotations

import logging
import os
import pathlib
from typing import Any

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "info").upper())

app = FastAPI(title="dashboard", version="0.4.0")

_VERSION = "0.4.0"
_PHASE   = "4B-static-frontend-preview"

_FRONTEND_DIR = pathlib.Path(__file__).parent.parent / "frontend"

# Mount static assets (JS, CSS) — must come before catch-all route
if _FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIR)), name="static")

_TIMEOUT = 5.0  # seconds for all scanner-api calls

SCANNER_API_URL  = os.getenv("SCANNER_API_URL", "").rstrip("/")
RESEARCH_API_URL = os.getenv("RESEARCH_API_URL", "").rstrip("/")


# ─────────────────────────────────────────────────────────────────────────────
# HTTP client helpers
# ─────────────────────────────────────────────────────────────────────────────

def _scanner_get(path: str, params: dict | None = None) -> tuple[dict | None, str | None]:
    """
    GET {SCANNER_API_URL}{path}. Returns (data, error_message).
    Never raises — all failures return (None, error_str).
    """
    if not SCANNER_API_URL:
        return None, "SCANNER_API_URL not configured"
    url = f"{SCANNER_API_URL}{path}"
    try:
        resp = httpx.get(url, params=params or {}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json(), None
    except httpx.TimeoutException:
        return None, "scanner-api timeout"
    except httpx.HTTPStatusError as exc:
        return None, f"scanner-api HTTP {exc.response.status_code}"
    except Exception as exc:
        return None, type(exc).__name__


# ─────────────────────────────────────────────────────────────────────────────
# BFF helpers
# ─────────────────────────────────────────────────────────────────────────────

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


def _best_setups(candidates: list[dict], n: int = 5) -> list[dict]:
    """Deterministic top-N by ultra_score — no AI, no scoring."""
    sorted_c = sorted(candidates, key=lambda x: x.get("ultra_score") or 0, reverse=True)
    result = []
    for c in sorted_c[:n]:
        score = c.get("ultra_score") or 0
        why = list(c.get("why_selected") or [])
        if not any("Ultra score" in w for w in why):
            why.insert(0, f"Ultra score {score}")
        if "Top Ultra candidate" not in why:
            why.insert(0, "Top Ultra candidate")
        result.append({
            "symbol":       c.get("symbol", ""),
            "sector":       c.get("sector", ""),
            "ultra_score":  score,
            "band":         c.get("band", ""),
            "priority":     c.get("priority", ""),
            "action_bucket": c.get("action_bucket", ""),
            "final_signal": c.get("final_signal", ""),
            "why_selected": why[:5],
            "source":       "scanner-api",
        })
    return result


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

    if scanner_url_ok:
        data, err = _scanner_get("/health")
        if data and data.get("status") == "ok":
            scanner_reachable = True
            scanner_health = data
        else:
            scanner_health = {"error": err}

    return {
        "service":                    "dashboard",
        "mode":                       "scanner_api_bridge_phase",
        "database_configured":        bool(os.getenv("DATABASE_URL")),
        "redis_configured":           bool(os.getenv("REDIS_URL")),
        "scanner_api_url_configured": scanner_url_ok,
        "scanner_api_reachable":      scanner_reachable,
        "scanner_api_health":         scanner_health,
        "research_api_url_configured": bool(RESEARCH_API_URL),
        "massive_configured":         bool(os.getenv("MASSIVE_API_KEY")),
        "anthropic_configured":       bool(os.getenv("ANTHROPIC_API_KEY")),
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

    # 2 — get top candidates
    cand_data, cand_err = _scanner_get(
        "/api/scans/ultra/latest/candidates",
        params={"limit": 50, "offset": 0, "sort_by": "ultra_score", "sort_dir": "desc"},
    )

    candidates: list[dict] = []
    if cand_data and cand_data.get("has_data"):
        candidates = cand_data.get("candidates", [])

    total = run.get("total_candidates") or 0
    top_score = max((c.get("ultra_score") or 0 for c in candidates), default=0)

    return {
        "dashboard_state": "SCAN_READY",
        "latest_scan": {
            "has_data":        True,
            "scan_run_id":     run.get("id"),
            "status":          run.get("status"),
            "universe":        run.get("universe"),
            "timeframe":       run.get("timeframe"),
            "finished_at":     run.get("finished_at"),
            "total_candidates": total,
            "source":          "scanner-api",
        },
        "summary": {
            "total_candidates": total,
            "top_score":        top_score,
            "bands":            _band_summary(candidates),
            "sectors":          _sector_summary(candidates),
        },
        "top_candidates": candidates,
        "best_setups":    _best_setups(candidates, n=5),
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

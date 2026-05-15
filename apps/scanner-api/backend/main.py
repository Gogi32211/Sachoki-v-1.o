"""
scanner-api — Phase 3: read-only Ultra Scan DB integration.

All endpoints are read-only. No scans, no writes, no scheduler.
"""
from __future__ import annotations

import json
import logging
import os

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "info").upper())

app = FastAPI(title="scanner-api", version="0.2.0")

_VERSION = "0.2.0"
_PHASE = "3-readonly-db"

# Known Ultra Scan table names (confirmed from backend/ultra_scan_migration.py)
_RUN_TABLE = "ultra_scan_runs"
_CAND_TABLE = "ultra_scan_candidates"

# Safe columns for ORDER BY — never interpolate arbitrary user input
_SAFE_SORT_COLS = {"ultra_score", "ticker", "created_at"}


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

    return {
        "service":                      "scanner-api",
        "mode":                         "read_only_db_phase",
        "database_configured":          db_configured,
        "database_connected":           db_connected,
        "database_error":               db_error or None,
        "redis_configured":             bool(os.getenv("REDIS_URL")),
        "massive_configured":           bool(os.getenv("MASSIVE_API_KEY")),
        "scanning_enabled":             False,
        "scheduler_enabled":            False,
        "latest_ultra_scan_found":      latest_scan_found,
        "latest_ultra_scan_id":         latest_scan_id,
        "latest_ultra_candidate_count": latest_candidate_count,
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

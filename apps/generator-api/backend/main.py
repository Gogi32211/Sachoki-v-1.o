"""
generator-api — dashboard-ready view generator as a standalone Railway service.

Phase E extraction (per docs/ARCHITECTURE_TARGET.md): the generator module
from scanner-api is promoted to its own service. After E, all six target
services exist as independent Railway deployments.

Architectural law:
    READS  ultra_scan_candidates  (owned by scanner-api, shared Postgres)
    READS+WRITES scan_generated_views  (owned by this service)
    NO Massive HTTP. NO engine compute. NO scan orchestration.
    NO frontend concerns.

Endpoints:
    GET  /health
    GET  /version
    GET  /api/debug/status
    POST /api/generator/run                      — (x-admin-token)
    GET  /api/generator/views                    — list all 4 views for a run
    GET  /api/generator/views/{view_type}        — one view

Auth: POST /api/generator/run requires x-admin-token, validated against
this service's ADMIN_TOKEN env (same model as engine-api / market-data-api).
"""
from __future__ import annotations

import json
import logging
import os
import secrets

from fastapi import Body, FastAPI, Header, HTTPException, Query

from . import generator as _gen
from . import db as _db

log = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "info").upper())

app = FastAPI(title="generator-api", version="0.1.0")

_VERSION = "0.1.0"
_PHASE   = "E extraction"

_RUN_TABLE  = "ultra_scan_runs"        # owned by scanner-api; we only READ
_CAND_TABLE = "ultra_scan_candidates"  # owned by scanner-api; we only READ


# ── DDL: own only scan_generated_views ───────────────────────────────────────
# scan_generated_views is shared with scanner-api (it also runs the same
# CREATE TABLE IF NOT EXISTS on startup). Idempotent on either side.

_DDL_SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_generated_views (
    scan_run_id        INTEGER     NOT NULL,
    view_type          VARCHAR(32) NOT NULL,
    payload_json       JSONB       NOT NULL,
    generator_version  VARCHAR(16) NOT NULL DEFAULT 'd1.0',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scan_run_id, view_type, generator_version)
);
CREATE INDEX IF NOT EXISTS idx_sgv_run_type ON scan_generated_views(scan_run_id, view_type);
CREATE INDEX IF NOT EXISTS idx_sgv_updated  ON scan_generated_views(updated_at);
"""


def _ensure_schema() -> None:
    if not _db.DATABASE_URL:
        return
    with _db.get_write_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_DDL_SCHEMA)
        conn.commit()


@app.on_event("startup")
def _startup_ensure_schema() -> None:
    try:
        _ensure_schema()
    except Exception as exc:
        log.warning("generator-api startup schema init failed: %s", exc)


# ── Auth + DB helpers ────────────────────────────────────────────────────────

def _require_admin(x_admin_token: str) -> None:
    expected = os.environ.get("ADMIN_TOKEN") or os.environ.get("SEED_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN not configured on this service")
    if not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=401, detail="Invalid admin token")


def _resolve_latest_run_id() -> int | None:
    """Find the most recent completed scan_run_id by querying scanner-api's
    table directly (shared Postgres). No HTTP roundtrip needed."""
    if not _db.DATABASE_URL:
        return None
    try:
        with _db.get_conn() as cur:
            cur.execute(
                f"SELECT id FROM {_RUN_TABLE} "
                f"WHERE is_latest=TRUE AND status='completed' "
                f"ORDER BY finished_at DESC LIMIT 1"
            )
            r = cur.fetchone()
            return r["id"] if r else None
    except Exception as exc:
        log.warning("resolve_latest_run_id failed: %s", exc)
        return None


def _fetch_candidates(scan_run_id: int) -> list[dict]:
    """Pull all candidates for a scan_run_id, parse row_json, normalize."""
    if not _db.DATABASE_URL:
        return []
    candidates: list[dict] = []
    with _db.get_conn() as cur:
        cur.execute(
            f"SELECT ticker, ultra_score, row_json FROM {_CAND_TABLE} "
            f"WHERE scan_run_id=%s",
            (scan_run_id,),
        )
        for r in cur.fetchall():
            raw = r.get("row_json")
            try:
                row = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except Exception:
                row = {}
            row["symbol"]      = row.get("symbol") or row.get("ticker") or r.get("ticker")
            row["ultra_score"] = row.get("ultra_score", r.get("ultra_score"))
            candidates.append(row)
    return candidates


# ── Health / version / debug ─────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "generator-api"}


@app.get("/version")
def version():
    return {"service": "generator-api", "version": _VERSION, "phase": _PHASE}


@app.get("/api/debug/status")
def debug_status():
    db_configured = bool(_db.DATABASE_URL)
    db_connected, db_error = (False, None)
    if db_configured:
        db_connected, db_error = _db.ping()
    return {
        "service":             "generator-api",
        "mode":                "pure_aggregator",
        "database_configured": db_configured,
        "database_connected":  db_connected,
        "database_error":      db_error,
        "owns_table":          "scan_generated_views",
        "reads":               ["ultra_scan_runs", "ultra_scan_candidates"],
        "writes":              ["scan_generated_views"],
        "generator_version":   _gen.GENERATOR_VERSION,
        "view_types":          list(_gen.VIEW_TYPES),
    }


# ── Generate + read endpoints ────────────────────────────────────────────────

@app.post("/api/generator/run")
def generator_run(
    body: dict = Body(default={}),
    x_admin_token: str = Header(default=""),
):
    """Run all 4 generators on the latest (or specified) scan candidates
    and persist payloads to scan_generated_views. Idempotent."""
    _require_admin(x_admin_token)
    _db.require_db()
    _ensure_schema()

    run_id = body.get("run_id")
    if run_id is None:
        run_id = _resolve_latest_run_id()
    if run_id is None:
        return {
            "ok": False, "error": "no_completed_scan",
            "message": "No latest completed scan to generate views from.",
        }

    candidates = _fetch_candidates(int(run_id))
    summary = _gen.generate_and_save(int(run_id), candidates)
    summary["ok"]     = True
    summary["source"] = "generator-api"
    return summary


@app.get("/api/generator/views/{view_type}")
def get_one_view(view_type: str, run_id: int | None = Query(default=None)):
    if view_type not in _gen.VIEW_TYPES:
        raise HTTPException(status_code=400,
            detail=f"unknown view_type {view_type!r}; must be one of {list(_gen.VIEW_TYPES)}")
    if not _db.DATABASE_URL:
        return {"ok": False, "error": "DATABASE_URL not configured",
                "view_type": view_type, "payload": None}

    if run_id is None:
        run_id = _resolve_latest_run_id()
        if run_id is None:
            return {"ok": False, "view_type": view_type, "payload": None,
                    "error": "no_completed_scan"}

    payload = _gen.get_view(int(run_id), view_type)
    return {
        "ok":                payload is not None,
        "view_type":         view_type,
        "scan_run_id":       int(run_id),
        "generator_version": _gen.GENERATOR_VERSION,
        "payload":           payload,
    }


@app.get("/api/generator/views")
def get_all_views(run_id: int | None = Query(default=None)):
    """Return all 4 generated views for a given run (or latest)."""
    out: dict = {"ok": True, "scan_run_id": run_id, "views": {}}
    for vt in _gen.VIEW_TYPES:
        resp = get_one_view(vt, run_id=out["scan_run_id"])
        if out["scan_run_id"] is None and resp.get("scan_run_id"):
            out["scan_run_id"] = resp["scan_run_id"]
        out["views"][vt] = resp.get("payload")
    return out

"""
ultra_research_routes.py — Persistent research archive endpoints.

All data is served from the DB tables written by persist_research_results().
These endpoints work even after a server restart (no parquet/disk dependency).

Endpoints (all under /api/ultra-research):
  GET  /runs                            list runs with filters
  GET  /runs/{run_id}                   run detail + available sections
  GET  /runs/{run_id}/episodes          paginated episodes from DB
  GET  /runs/{run_id}/patterns          paginated patterns from DB
  GET  /runs/{run_id}/research-bundle   bundle JSON from DB
  GET  /runs/{run_id}/integrity         orphan/count audit
  DELETE /runs/{run_id}                 hard delete all rows
  GET  /runs/{run_id}/export            export with ?type= and ?format=
  GET  /runs/{run_id}/export-audit      counts vs DB
"""
from __future__ import annotations
import csv
import datetime
import io
import json
import logging
import zipfile
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from db import get_db, USE_PG

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ultra-research", tags=["ultra_research"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ph() -> str:
    return "%s" if USE_PG else "?"


def _q(sql: str, params=()) -> list[dict]:
    with get_db() as db:
        db.execute(sql, list(params))
        return db.fetchall() or []


def _q1(sql: str, params=()) -> dict | None:
    with get_db() as db:
        db.execute(sql, list(params))
        return db.fetchone()


def _csv_safe(v: Any) -> Any:
    """Neutralize CSV formula injection: prefix =+-@ with single quote."""
    if isinstance(v, str) and v and v[0] in ("=", "+", "-", "@"):
        return "'" + v
    return v


def _require_run(run_id: int) -> dict:
    ph = _ph()
    row = _q1(f"SELECT * FROM ultra_pump_runs WHERE id={ph}", [run_id])
    if not row:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return row


def _duration_str(row: dict) -> str | None:
    try:
        s = row.get("started_at")
        f = row.get("finished_at")
        if s and f:
            from datetime import datetime as _dt
            fmt = "%Y-%m-%d %H:%M:%S"
            ts = _dt.strptime(str(s)[:19], fmt) if "T" not in str(s) else _dt.fromisoformat(str(s)[:19])
            tf = _dt.strptime(str(f)[:19], fmt) if "T" not in str(f) else _dt.fromisoformat(str(f)[:19])
            sec = int((tf - ts).total_seconds())
            m, s2 = divmod(sec, 60)
            return f"{m}m {s2}s"
    except Exception:
        pass
    return None


# ── List runs ─────────────────────────────────────────────────────────────────

@router.get("/runs")
def list_runs(
    status: str | None = None,
    universe: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    ph = _ph()

    where_parts = []
    params: list = []
    if status:
        where_parts.append(f"status={ph}")
        params.append(status)
    if universe:
        where_parts.append(f"universe={ph}")
        params.append(universe)

    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    count_row = _q1(f"SELECT COUNT(*) AS n FROM ultra_pump_runs {where}", params)
    total = (count_row or {}).get("n", 0) or 0

    params_page = params + [limit, offset]
    rows = _q(
        f"SELECT id, status, universe, mode, pump_target, start_date, end_date, "
        f"pump_horizon, pre_pump_window_bars, scanner_detection_window_bars, "
        f"total_episodes, total_caught, total_missed, symbols_total, symbols_completed, "
        f"summary_json, error_message, started_at, finished_at, created_at "
        f"FROM ultra_pump_runs {where} ORDER BY id DESC LIMIT {ph} OFFSET {ph}",
        params_page,
    )

    # Annotate each row with DB archive status and duration
    for r in rows:
        rid = r.get("id")
        r["duration"] = _duration_str(r)
        # Check if DB archive rows exist
        ep_cnt = _q1(f"SELECT COUNT(*) AS n FROM ultra_research_episodes WHERE run_id={ph}", [rid])
        r["db_episodes_count"] = (ep_cnt or {}).get("n", 0) or 0
        r["has_db_archive"] = r["db_episodes_count"] > 0 or bool(
            _q1(f"SELECT run_id FROM ultra_research_bundles WHERE run_id={ph}", [rid])
        )

    return {"total": total, "limit": limit, "offset": offset, "runs": rows}


# ── Single run detail ─────────────────────────────────────────────────────────

@router.get("/runs/{run_id}")
def get_run(run_id: int) -> dict:
    row = dict(_require_run(run_id))
    ph = _ph()

    # Count DB archive rows
    ep_row = _q1(f"SELECT COUNT(*) AS n FROM ultra_research_episodes WHERE run_id={ph}", [run_id])
    pt_row = _q1(f"SELECT COUNT(*) AS n FROM ultra_research_patterns WHERE run_id={ph}", [run_id])
    bun_row = _q1(f"SELECT run_id FROM ultra_research_bundles WHERE run_id={ph}", [run_id])

    row["archive"] = {
        "episodes_count": (ep_row or {}).get("n", 0) or 0,
        "patterns_count": (pt_row or {}).get("n", 0) or 0,
        "has_bundle": bun_row is not None,
    }
    row["duration"] = _duration_str(row)
    row["available_sections"] = ["episodes", "patterns", "research-bundle", "export", "integrity"]
    return row


# ── Episodes ──────────────────────────────────────────────────────────────────

@router.get("/runs/{run_id}/episodes")
def list_episodes(
    run_id: int,
    category: str | None = None,
    symbol: str | None = None,
    caught_status: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict:
    _require_run(run_id)
    ph = _ph()
    limit = max(1, min(limit, 5000))
    offset = max(0, offset)

    where_parts = [f"run_id={ph}"]
    params: list = [run_id]
    if category:
        where_parts.append(f"category={ph}")
        params.append(category)
    if symbol:
        where_parts.append(f"symbol={ph}")
        params.append(symbol.upper())
    if caught_status:
        where_parts.append(f"caught_status={ph}")
        params.append(caught_status)

    where = "WHERE " + " AND ".join(where_parts)

    count_row = _q1(f"SELECT COUNT(*) AS n FROM ultra_research_episodes {where}", params)
    total = (count_row or {}).get("n", 0) or 0

    params_page = params + [limit, offset]
    rows = _q(
        f"SELECT * FROM ultra_research_episodes {where} "
        f"ORDER BY anchor_date DESC LIMIT {ph} OFFSET {ph}",
        params_page,
    )
    return {"total": total, "limit": limit, "offset": offset, "rows": rows}


# ── Patterns ──────────────────────────────────────────────────────────────────

@router.get("/runs/{run_id}/patterns")
def list_patterns(
    run_id: int,
    pattern_type: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict:
    _require_run(run_id)
    ph = _ph()
    limit = max(1, min(limit, 5000))
    offset = max(0, offset)

    where_parts = [f"run_id={ph}"]
    params: list = [run_id]
    if pattern_type:
        where_parts.append(f"pattern_type={ph}")
        params.append(pattern_type)

    where = "WHERE " + " AND ".join(where_parts)

    count_row = _q1(f"SELECT COUNT(*) AS n FROM ultra_research_patterns {where}", params)
    total = (count_row or {}).get("n", 0) or 0

    params_page = params + [limit, offset]
    # SQLite: use CASE to sort NULLs last; Postgres: NULLS LAST
    if USE_PG:
        order_expr = "lift_vs_baseline DESC NULLS LAST"
    else:
        order_expr = "CASE WHEN lift_vs_baseline IS NULL THEN 1 ELSE 0 END, lift_vs_baseline DESC"
    rows = _q(
        f"SELECT * FROM ultra_research_patterns {where} "
        f"ORDER BY {order_expr} LIMIT {ph} OFFSET {ph}",
        params_page,
    )
    return {"total": total, "limit": limit, "offset": offset, "rows": rows}


# ── Research bundle ───────────────────────────────────────────────────────────

@router.get("/runs/{run_id}/research-bundle")
def get_research_bundle(run_id: int) -> dict:
    _require_run(run_id)
    ph = _ph()
    bun = _q1(f"SELECT bundle_json FROM ultra_research_bundles WHERE run_id={ph}", [run_id])
    if not bun:
        raise HTTPException(status_code=404, detail="No research bundle in DB for this run. "
                            "Run may predate persistence or failed before completion.")
    try:
        return json.loads(bun["bundle_json"])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"bundle JSON parse error: {exc}")


# ── Integrity audit ───────────────────────────────────────────────────────────

@router.get("/runs/{run_id}/integrity")
def integrity_audit(run_id: int) -> dict:
    row = dict(_require_run(run_id))
    ph = _ph()

    ep_row = _q1(f"SELECT COUNT(*) AS n FROM ultra_research_episodes WHERE run_id={ph}", [run_id])
    pt_row = _q1(f"SELECT COUNT(*) AS n FROM ultra_research_patterns WHERE run_id={ph}", [run_id])
    bun_row = _q1(f"SELECT run_id FROM ultra_research_bundles WHERE run_id={ph}", [run_id])

    db_episodes = (ep_row or {}).get("n", 0) or 0
    db_patterns = (pt_row or {}).get("n", 0) or 0
    meta_episodes = row.get("total_episodes", 0) or 0

    issues = []
    if db_episodes == 0 and meta_episodes > 0:
        issues.append("DB has 0 episodes but run metadata claims >0 — persistence may have failed")
    if db_episodes > 0 and abs(db_episodes - meta_episodes) > 5:
        issues.append(
            f"Episode count mismatch: DB={db_episodes}, metadata={meta_episodes} "
            f"(delta={abs(db_episodes - meta_episodes)})"
        )
    if not bun_row:
        issues.append("No research bundle in DB — run may have failed before persistence step")

    return {
        "run_id": run_id,
        "status": row.get("status"),
        "audit": {
            "db_episodes": db_episodes,
            "db_patterns": db_patterns,
            "has_bundle": bun_row is not None,
            "metadata_episodes": meta_episodes,
            "metadata_caught": row.get("total_caught", 0),
            "metadata_missed": row.get("total_missed", 0),
        },
        "issues": issues,
        "healthy": len(issues) == 0,
    }


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete("/runs/{run_id}")
def delete_run(run_id: int) -> dict:
    row = dict(_require_run(run_id))
    if row.get("status") == "running":
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a run that is currently running. Stop it first.",
        )

    ph = _ph()
    try:
        with get_db() as db:
            # Count what will be deleted
            db.execute(f"SELECT COUNT(*) AS n FROM ultra_research_episodes WHERE run_id={ph}", [run_id])
            ep_del = (db.fetchone() or {}).get("n", 0) or 0

            db.execute(f"SELECT COUNT(*) AS n FROM ultra_research_patterns WHERE run_id={ph}", [run_id])
            pt_del = (db.fetchone() or {}).get("n", 0) or 0

            # Delete cascade-able rows explicitly (in case FK cascade is not enforced)
            db.execute(f"DELETE FROM ultra_research_bundles WHERE run_id={ph}", [run_id])
            db.execute(f"DELETE FROM ultra_research_patterns WHERE run_id={ph}", [run_id])
            db.execute(f"DELETE FROM ultra_research_episodes WHERE run_id={ph}", [run_id])
            db.execute(f"DELETE FROM replay_artifacts WHERE run_id={ph}", [run_id])
            db.execute(f"DELETE FROM ultra_pump_runs WHERE id={ph}", [run_id])
            db.commit()

        # Also remove disk artifacts if present
        disk_removed = False
        try:
            from replay_storage import delete_run_directory
            disk_removed = delete_run_directory(run_id)
        except Exception:
            pass

        return {
            "deleted": True,
            "run_id": run_id,
            "rows_deleted": {
                "episodes": ep_del,
                "patterns": pt_del,
                "bundle": 1,
                "run": 1,
            },
            "disk_directory_removed": disk_removed,
        }
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("ultra_research delete failed for run %s", run_id)
        raise HTTPException(status_code=500, detail=f"delete failed: {exc}")


# ── Export ────────────────────────────────────────────────────────────────────

_EXPORT_TYPES = {"summary", "episodes", "patterns", "research_bundle", "all_zip"}


def _episodes_to_csv(rows: list[dict]) -> str:
    if not rows:
        return "# no episode rows\n"
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for r in rows:
        writer.writerow({k: _csv_safe(v) for k, v in r.items()})
    return buf.getvalue()


def _patterns_to_csv(rows: list[dict]) -> str:
    if not rows:
        return "# no pattern rows\n"
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for r in rows:
        writer.writerow({k: _csv_safe(v) for k, v in r.items()})
    return buf.getvalue()


def _fetch_all_episodes(run_id: int) -> list[dict]:
    ph = _ph()
    return _q(
        f"SELECT * FROM ultra_research_episodes WHERE run_id={ph} ORDER BY anchor_date DESC",
        [run_id],
    )


def _fetch_all_patterns(run_id: int) -> list[dict]:
    ph = _ph()
    if USE_PG:
        order_expr = "lift_vs_baseline DESC NULLS LAST"
    else:
        order_expr = "CASE WHEN lift_vs_baseline IS NULL THEN 1 ELSE 0 END, lift_vs_baseline DESC"
    return _q(
        f"SELECT * FROM ultra_research_patterns WHERE run_id={ph} ORDER BY {order_expr}",
        [run_id],
    )


@router.get("/runs/{run_id}/export")
def export_run(
    run_id: int,
    type: str = Query("summary", description="summary|episodes|patterns|research_bundle|all_zip"),
    format: str = Query("json", description="json|csv (csv only for episodes/patterns)"),
) -> Response:
    row = dict(_require_run(run_id))
    ph = _ph()

    if type not in _EXPORT_TYPES:
        raise HTTPException(status_code=400,
                            detail=f"type must be one of {sorted(_EXPORT_TYPES)}")

    use_csv = str(format).lower() == "csv"
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    # ── summary ──
    if type == "summary":
        summary = {
            "run_id": run_id,
            "exported_at": datetime.datetime.utcnow().isoformat() + "Z",
            "status": row.get("status"),
            "universe": row.get("universe"),
            "pump_target": row.get("pump_target"),
            "start_date": row.get("start_date"),
            "end_date": row.get("end_date"),
            "started_at": str(row.get("started_at") or ""),
            "finished_at": str(row.get("finished_at") or ""),
            "total_episodes": row.get("total_episodes"),
            "total_caught": row.get("total_caught"),
            "total_missed": row.get("total_missed"),
            "symbols_total": row.get("symbols_total"),
            "symbols_completed": row.get("symbols_completed"),
            "summary_json": json.loads(row["summary_json"]) if row.get("summary_json") else None,
        }
        content = json.dumps(summary, default=str, indent=2)
        fname = f"ultra_research_{run_id}_summary_{ts}.json"
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    # ── episodes ──
    if type == "episodes":
        rows = _fetch_all_episodes(run_id)
        fname_base = f"ultra_research_{run_id}_episodes_{ts}"
        if use_csv:
            content = _episodes_to_csv(rows)
            return Response(
                content=content,
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{fname_base}.csv"'},
            )
        body = {
            "run_id": run_id,
            "exported_at": datetime.datetime.utcnow().isoformat() + "Z",
            "row_count": len(rows),
            "rows": rows,
        }
        return Response(
            content=json.dumps(body, default=str, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{fname_base}.json"'},
        )

    # ── patterns ──
    if type == "patterns":
        rows = _fetch_all_patterns(run_id)
        fname_base = f"ultra_research_{run_id}_patterns_{ts}"
        if use_csv:
            content = _patterns_to_csv(rows)
            return Response(
                content=content,
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{fname_base}.csv"'},
            )
        body = {
            "run_id": run_id,
            "exported_at": datetime.datetime.utcnow().isoformat() + "Z",
            "row_count": len(rows),
            "rows": rows,
        }
        return Response(
            content=json.dumps(body, default=str, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{fname_base}.json"'},
        )

    # ── research_bundle ──
    if type == "research_bundle":
        bun = _q1(f"SELECT bundle_json FROM ultra_research_bundles WHERE run_id={ph}", [run_id])
        if not bun:
            raise HTTPException(status_code=404, detail="No research bundle for this run")
        fname = f"ultra_research_{run_id}_bundle_{ts}.json"
        return Response(
            content=bun["bundle_json"],
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    # ── all_zip ──
    if type == "all_zip":
        ep_rows = _fetch_all_episodes(run_id)
        pt_rows = _fetch_all_patterns(run_id)
        bun = _q1(f"SELECT bundle_json FROM ultra_research_bundles WHERE run_id={ph}", [run_id])

        manifest = {
            "run_id": run_id,
            "exported_at": datetime.datetime.utcnow().isoformat() + "Z",
            "sections": {
                "episodes": {
                    "db_count": len(ep_rows),
                    "export_count": len(ep_rows),
                    "files": [f"episodes.csv", f"episodes.json"],
                },
                "patterns": {
                    "db_count": len(pt_rows),
                    "export_count": len(pt_rows),
                    "files": [f"patterns.csv", f"patterns.json"],
                },
                "research_bundle": {
                    "db_count": 1 if bun else 0,
                    "export_count": 1 if bun else 0,
                    "files": ["research_bundle.json"] if bun else [],
                },
                "summary": {
                    "db_count": 1,
                    "export_count": 1,
                    "files": ["summary.json"],
                },
            },
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("export_manifest.json", json.dumps(manifest, default=str, indent=2))

            # Summary
            summary_data = {
                "run_id": run_id,
                "status": row.get("status"),
                "universe": row.get("universe"),
                "pump_target": row.get("pump_target"),
                "start_date": row.get("start_date"),
                "end_date": row.get("end_date"),
                "started_at": str(row.get("started_at") or ""),
                "finished_at": str(row.get("finished_at") or ""),
                "total_episodes": row.get("total_episodes"),
                "total_caught": row.get("total_caught"),
                "total_missed": row.get("total_missed"),
            }
            zf.writestr("summary.json", json.dumps(summary_data, default=str, indent=2))

            # Episodes
            zf.writestr("episodes.csv", _episodes_to_csv(ep_rows))
            zf.writestr("episodes.json", json.dumps({
                "run_id": run_id, "row_count": len(ep_rows), "rows": ep_rows,
            }, default=str, indent=2))

            # Patterns
            zf.writestr("patterns.csv", _patterns_to_csv(pt_rows))
            zf.writestr("patterns.json", json.dumps({
                "run_id": run_id, "row_count": len(pt_rows), "rows": pt_rows,
            }, default=str, indent=2))

            # Bundle
            if bun:
                zf.writestr("research_bundle.json", bun["bundle_json"])

        fname = f"ultra_research_{run_id}_all_{ts}.zip"
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    raise HTTPException(status_code=400, detail="Unknown export type")


# ── Export audit ──────────────────────────────────────────────────────────────

@router.get("/runs/{run_id}/export-audit")
def export_audit(run_id: int) -> dict:
    row = dict(_require_run(run_id))
    ph = _ph()

    ep_row = _q1(f"SELECT COUNT(*) AS n FROM ultra_research_episodes WHERE run_id={ph}", [run_id])
    pt_row = _q1(f"SELECT COUNT(*) AS n FROM ultra_research_patterns WHERE run_id={ph}", [run_id])
    bun_row = _q1(f"SELECT run_id FROM ultra_research_bundles WHERE run_id={ph}", [run_id])

    db_episodes = (ep_row or {}).get("n", 0) or 0
    db_patterns = (pt_row or {}).get("n", 0) or 0
    meta_episodes = row.get("total_episodes", 0) or 0

    return {
        "run_id": run_id,
        "status": row.get("status"),
        "sections": {
            "episodes": {
                "db_count": db_episodes,
                "metadata_count": meta_episodes,
                "match": db_episodes == meta_episodes or meta_episodes == 0,
            },
            "patterns": {
                "db_count": db_patterns,
                "metadata_count": None,
            },
            "research_bundle": {
                "db_count": 1 if bun_row else 0,
            },
        },
        "all_sections_populated": (db_episodes > 0 or meta_episodes == 0) and bun_row is not None,
    }

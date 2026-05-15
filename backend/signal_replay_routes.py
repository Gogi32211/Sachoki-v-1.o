"""
signal_replay_routes.py — FastAPI endpoints for the Signal Replay engine.

All heavy data (events, outcomes, statistics) is read from Parquet files via DuckDB.
Only run metadata and artifact registry are in Postgres.

Endpoints:
  POST   /api/signal-replay/run
  POST   /api/signal-replay/stop
  POST   /api/signal-replay/pause
  POST   /api/signal-replay/resume
  GET    /api/signal-replay/status
  GET    /api/signal-replay/history
  GET    /api/signal-replay/{run_id}
  GET    /api/signal-replay/{run_id}/events
  GET    /api/signal-replay/{run_id}/outcomes
  GET    /api/signal-replay/{run_id}/signal-statistics
  GET    /api/signal-replay/{run_id}/pattern-statistics
  GET    /api/signal-replay/{run_id}/filter-impact
  GET    /api/signal-replay/{run_id}/export
  DELETE /api/signal-replay/{run_id}
  POST   /api/signal-replay/purge-all
"""
from __future__ import annotations
import csv
import datetime
import io
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from db import get_db, USE_PG
from signal_replay_engine import (
    get_state as _get_state,
    run_signal_replay as _run_signal_replay,
    _insert_run as _insert_run_row,
    request_stop as _request_stop,
    request_pause as _request_pause,
    request_resume as _request_resume,
)
from replay_storage import (
    run_dir, artifact_path, query_parquet, count_parquet,
    delete_run_directory, delete_all_run_directories,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/signal-replay", tags=["signal_replay"])


# ─── Request models ──────────────────────────────────────────────────────────

_ALLOWED_LOOKBACK = {30, 100, 250, 500, 1000}


class RunRequest(BaseModel):
    universe: str = Field(..., pattern="^(sp500|nasdaq|nasdaq_gt5|split|all_us)$")
    mode:     str = Field(..., pattern="^(single_day|date_range|last_n_days|ytd)$")
    as_of_date:    str | None = None
    start_date:    str | None = None
    end_date:      str | None = None
    lookback_days: int | None = None
    benchmark_symbol: str = "QQQ"
    event_scope: str = "all_signals"
    min_price:         float | None = None
    min_volume:        int   | None = None
    min_dollar_volume: float | None = None
    lookback_bars:     int   | None = None

    @field_validator("lookback_bars", mode="before")
    @classmethod
    def validate_lookback_bars(cls, v):
        if v is None:
            return None
        v = int(v)
        if v not in _ALLOWED_LOOKBACK:
            raise ValueError(
                f"lookback_bars must be one of {sorted(_ALLOWED_LOOKBACK)}"
            )
        return v


# ─── DB helpers ──────────────────────────────────────────────────────────────

def _ph() -> str:
    return "%s" if USE_PG else "?"


def _query(sql: str, params: list | tuple = ()) -> list[dict]:
    with get_db() as db:
        db.execute(sql, params)
        rows = db.fetchall()
    return rows if rows else []


def _query_one(sql: str, params: list | tuple = ()) -> dict | None:
    with get_db() as db:
        db.execute(sql, params)
        row = db.fetchone()
    return row


# ─── Parquet helpers ──────────────────────────────────────────────────────────

def _parquet_path(run_id: int, artifact_type: str) -> Path:
    return artifact_path(run_id, artifact_type, "parquet")


def _artifact_path_if_ready(run_id: int, artifact_type: str) -> Path | None:
    """Return artifact path if it exists and is non-empty, else None.

    Never raises — callers return empty results gracefully. This allows
    completed runs with zero events (e.g. 30-bar mode on a quiet day) to
    return empty data instead of a 404 error.
    """
    p = _parquet_path(run_id, artifact_type)
    if not p.exists() or p.stat().st_size == 0:
        return None
    return p


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/run")
def start_run(req: RunRequest, background_tasks: BackgroundTasks) -> dict:
    state = _get_state()
    if state.get("running"):
        raise HTTPException(
            status_code=409,
            detail=(f"Signal Replay already running (run_id={state.get('run_id')}). "
                    "Wait or DELETE the active run."),
        )
    payload = req.model_dump()
    payload["timeframe"] = "1d"

    mode = payload["mode"]
    if mode == "single_day" and not payload.get("as_of_date"):
        raise HTTPException(status_code=400, detail="as_of_date required for single_day")
    if mode == "date_range" and not (payload.get("start_date") and payload.get("end_date")):
        raise HTTPException(status_code=400, detail="start_date and end_date required for date_range")
    if mode == "last_n_days" and not payload.get("lookback_days"):
        raise HTTPException(status_code=400, detail="lookback_days required for last_n_days")

    run_id = _insert_run_row(payload)
    background_tasks.add_task(_run_signal_replay, run_id, payload)
    return {"run_id": run_id, "status": "running", "message": "Signal replay started"}


@router.get("/status")
def status() -> dict:
    return _get_state()


@router.post("/stop")
def stop_run() -> dict:
    state = _get_state()
    if not state.get("running"):
        raise HTTPException(status_code=409, detail="No active run to stop")
    _request_stop()
    return {"message": "Stop requested", "run_id": state.get("run_id")}


@router.post("/pause")
def pause_run() -> dict:
    state = _get_state()
    if not state.get("running"):
        raise HTTPException(status_code=409, detail="No active run to pause")
    if state.get("pause_requested"):
        raise HTTPException(status_code=409, detail="Already paused")
    _request_pause()
    return {"message": "Paused", "run_id": state.get("run_id")}


@router.post("/resume")
def resume_run() -> dict:
    state = _get_state()
    if not state.get("running"):
        raise HTTPException(status_code=409, detail="No active run to resume")
    _request_resume()
    return {"message": "Resumed", "run_id": state.get("run_id")}


@router.get("/history")
def history(limit: int = 50) -> list[dict]:
    limit = max(1, min(limit, 500))
    ph = _ph()
    sql = (f"SELECT id, status, mode, universe, as_of_date, start_date, end_date, "
           f"total_events, total_outcomes, total_statistics_rows, storage_mode, "
           f"fetch_bars, outcome_forward_bars, warmup_bars, "
           f"artifact_status_json, context_limitations_json, "
           f"settings_json, started_at, finished_at, error_message "
           f"FROM signal_replay_runs ORDER BY id DESC LIMIT {limit}")
    runs = _query(sql)
    # Enrich with artifact sizes from disk and parse lookback_bars from settings_json
    for run in runs:
        rid = run.get("id")
        # Parse lookback_bars and context_quality from settings_json
        sj = run.get("settings_json")
        if sj:
            try:
                s = json.loads(sj)
                lb = int(s.get("lookback_bars") or 500)
                run["lookback_bars"] = lb
                run["context_quality"] = _context_quality_label(lb)
            except Exception:
                run["lookback_bars"] = 500
                run["context_quality"] = "FULL"
        if rid and run.get("storage_mode") == "parquet":
            rdir = run_dir(rid)
            run["artifacts_on_disk"] = rdir.exists()
            if rdir.exists():
                run["disk_bytes"] = sum(
                    f.stat().st_size for f in rdir.rglob("*") if f.is_file()
                )
    return runs


def _context_quality_label(lookback_bars: int) -> str:
    if lookback_bars >= 250:
        return "FULL"
    if lookback_bars >= 30:
        return "PARTIAL"
    return "LIMITED"


@router.get("/{run_id}")
def get_run(run_id: int) -> dict:
    ph = _ph()
    row = _query_one(f"SELECT * FROM signal_replay_runs WHERE id={ph}", [run_id])
    if not row:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    # Attach artifact info
    artifacts = _query(
        f"SELECT artifact_type, file_path, format, row_count, size_bytes, created_at "
        f"FROM replay_artifacts WHERE run_id={ph} ORDER BY artifact_type",
        [run_id],
    )
    row = dict(row)
    row["artifacts"] = artifacts
    return row


@router.get("/{run_id}/events")
def list_events(
    run_id: int,
    symbol: str | None = None,
    event_signal: str | None = None,
    event_signal_family: str | None = None,
    event_signal_type: str | None = None,
    sequence_4bar: str | None = None,
    abr_category: str | None = None,
    wlnbb_bucket: str | None = None,
    ema50_state: str | None = None,
    role: str | None = None,
    limit: int = 200,
    offset: int = 0,
    sort_by: str = "id",
    sort_dir: str = "desc",
) -> dict:
    limit  = max(1, min(limit, 2000))
    offset = max(0, offset)

    p = _artifact_path_if_ready(run_id, "events")
    if p is None:
        return {"total": 0, "limit": limit, "offset": offset, "rows": []}

    conditions: list[tuple[str, str, Any]] = []
    for col, val in (
        ("symbol", symbol), ("event_signal", event_signal),
        ("event_signal_family", event_signal_family),
        ("event_signal_type", event_signal_type),
        ("sequence_4bar", sequence_4bar),
        ("abr_category", abr_category), ("wlnbb_bucket", wlnbb_bucket),
        ("ema50_state", ema50_state), ("role", role),
    ):
        if val is not None:
            conditions.append((col, "=", val))

    _ALLOWED_SORT = {"id", "scan_date", "symbol", "event_signal", "score", "close", "volume"}
    sort_col = sort_by if sort_by in _ALLOWED_SORT else "id"
    sort_d   = "DESC" if str(sort_dir).lower() == "desc" else "ASC"

    rows  = query_parquet(p, conditions, sort_col=sort_col, sort_dir=sort_d,
                          limit=limit, offset=offset)
    total = count_parquet(p, conditions)
    return {"total": total, "limit": limit, "offset": offset, "rows": rows}


@router.get("/{run_id}/outcomes")
def list_outcomes(
    run_id: int,
    symbol: str | None = None,
    horizon: str | None = None,
    outcome_label: str | None = None,
    min_return: float | None = None,
    min_max_gain: float | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    limit  = max(1, min(limit, 2000))
    offset = max(0, offset)

    p = _artifact_path_if_ready(run_id, "outcomes")
    if p is None:
        return {"total": 0, "limit": limit, "offset": offset, "rows": []}

    conditions: list[tuple[str, str, Any]] = []
    for col, val in (("symbol", symbol), ("horizon", horizon), ("outcome_label", outcome_label)):
        if val is not None:
            conditions.append((col, "=", val))
    if min_return is not None:
        conditions.append(("return_pct", ">=", min_return))
    if min_max_gain is not None:
        conditions.append(("max_gain_pct", ">=", min_max_gain))

    rows  = query_parquet(p, conditions, sort_col="id", sort_dir="DESC",
                          limit=limit, offset=offset)
    total = count_parquet(p, conditions)
    return {"total": total, "limit": limit, "offset": offset, "rows": rows}


@router.get("/{run_id}/signal-statistics")
def list_signal_statistics(
    run_id: int,
    horizon: str | None = "10d",
    event_signal_family: str | None = None,
    verdict: str | None = None,
    stat_type: str | None = None,
    min_sample_size: int = 0,
    sort_by: str = "median_return",
    sort_dir: str = "desc",
    limit: int = 500,
) -> list[dict]:
    limit = max(1, min(limit, 5000))

    p = _artifact_path_if_ready(run_id, "signal_stats")
    if p is None:
        return []

    conditions: list[tuple[str, str, Any]] = []
    if horizon:
        conditions.append(("horizon", "=", horizon))
    if event_signal_family:
        conditions.append(("event_signal_family", "=", event_signal_family))
    if verdict:
        conditions.append(("verdict", "=", verdict))
    if stat_type:
        conditions.append(("stat_type", "=", stat_type))
    if min_sample_size:
        conditions.append(("sample_size", ">=", min_sample_size))

    _ALLOWED_SORT = {
        "sample_size", "avg_return", "median_return", "win_rate",
        "hit_10pct_rate", "fail_10pct_rate", "expectancy",
        "confidence_score", "stat_key",
    }
    sort_col = sort_by if sort_by in _ALLOWED_SORT else "median_return"
    sort_d   = "DESC" if str(sort_dir).lower() == "desc" else "ASC"

    return query_parquet(p, conditions, sort_col=sort_col, sort_dir=sort_d,
                         limit=limit, offset=0)


@router.get("/{run_id}/pattern-statistics")
def list_pattern_statistics(
    run_id: int,
    horizon: str | None = "10d",
    pattern_type: str | None = None,
    terminal_signal: str | None = None,
    min_sample_size: int = 3,
    sort_by: str = "median_return",
    sort_dir: str = "desc",
    limit: int = 500,
) -> list[dict]:
    limit = max(1, min(limit, 5000))

    p = _artifact_path_if_ready(run_id, "pattern_stats")
    if p is None:
        return []

    conditions: list[tuple[str, str, Any]] = []
    if horizon:
        conditions.append(("horizon", "=", horizon))
    if pattern_type:
        conditions.append(("pattern_type", "=", pattern_type))
    if terminal_signal:
        conditions.append(("terminal_signal", "=", terminal_signal))
    if min_sample_size:
        conditions.append(("sample_size", ">=", min_sample_size))

    _ALLOWED_SORT = {
        "sample_size", "median_return", "avg_return", "win_rate",
        "hit_10pct_rate", "fail_10pct_rate", "expectancy",
        "confidence_score", "stat_key", "pattern_value",
    }
    sort_col = sort_by if sort_by in _ALLOWED_SORT else "median_return"
    sort_d   = "DESC" if str(sort_dir).lower() == "desc" else "ASC"

    return query_parquet(p, conditions, sort_col=sort_col, sort_dir=sort_d,
                         limit=limit, offset=0)


@router.get("/{run_id}/filter-impact")
def list_filter_impact(
    run_id: int,
    horizon: str | None = "10d",
    base_signal: str | None = None,
    filter_name: str | None = None,
    filter_value: str | None = None,
    min_sample_size: int = 5,
    sort_by: str = "lift_median_return",
    sort_dir: str = "desc",
    limit: int = 500,
) -> list[dict]:
    limit = max(1, min(limit, 5000))

    p = _artifact_path_if_ready(run_id, "filter_impact")
    if p is None:
        return []

    conditions: list[tuple[str, str, Any]] = []
    if horizon:
        conditions.append(("horizon", "=", horizon))
    if base_signal:
        conditions.append(("base_signal", "=", base_signal))
    if filter_name:
        conditions.append(("filter_name", "=", filter_name))
    if filter_value:
        conditions.append(("filter_value", "=", filter_value))
    if min_sample_size:
        conditions.append(("sample_size", ">=", min_sample_size))

    _ALLOWED_SORT = {
        "sample_size", "median_return", "avg_return", "win_rate",
        "hit_10pct_rate", "fail_10pct_rate",
        "lift_median_return", "lift_hit_10pct", "confidence_score",
    }
    sort_col = sort_by if sort_by in _ALLOWED_SORT else "lift_median_return"
    sort_d   = "DESC" if str(sort_dir).lower() == "desc" else "ASC"

    return query_parquet(p, conditions, sort_col=sort_col, sort_dir=sort_d,
                         limit=limit, offset=0)


@router.get("/{run_id}/export")
def export_run(
    run_id: int,
    part: str = "all",
    offset: int = 0,
    limit: int = 50000,
    fmt: str = "json",
) -> Response:
    """
    Export run data. `part` selects the slice to export:
      run           — run metadata only
      signal_stats  — signal_stats.parquet → JSON/CSV
      pattern_stats — pattern_stats.parquet → JSON/CSV
      filter_impact — filter_impact.parquet → JSON/CSV
      events        — events.parquet (paginated) → JSON/CSV
      outcomes      — outcomes.parquet (paginated) → JSON/CSV
      research      — research_bundle.json (full analytics bundle)
      all           — all stats + run metadata in one JSON

    `fmt` param: 'json' (default) or 'csv' (events/outcomes/stats parts only).
    """
    ph = _ph()
    run = _query_one(f"SELECT * FROM signal_replay_runs WHERE id={ph}", [run_id])
    if not run:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    valid_parts = {"run", "signal_stats", "pattern_stats", "filter_impact",
                   "events", "outcomes", "research", "all"}
    if part not in valid_parts:
        raise HTTPException(status_code=400,
                            detail=f"part must be one of {sorted(valid_parts)}")

    run_status = run["status"] if isinstance(run, dict) else getattr(run, "status", None)
    if run_status in ("running", "paused") and part != "run":
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} is still {run_status}. Artifacts are written after the run completes — only 'run' metadata is available now.",
        )

    offset = max(0, int(offset))
    limit  = max(1, min(int(limit), 500_000))
    use_csv = str(fmt).lower() == "csv"

    meta = {
        "run_id":      run_id,
        "exported_at": datetime.datetime.utcnow().isoformat() + "Z",
        "version":     "parquet-v1",
        "part":        part,
    }

    # ── research bundle shortcut ──
    if part == "research":
        rb_path = artifact_path(run_id, "research_bundle", "json")
        if not rb_path.exists():
            content = json.dumps({"run_id": run_id, "status": "no_data",
                                  "message": "research_bundle not yet written"}, indent=2)
        else:
            content = rb_path.read_text(encoding="utf-8")
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="replay_{run_id}_research.json"'},
        )

    body: dict[str, Any] = {"meta": meta, "run": dict(run)}

    def _load_stats(artifact_type: str) -> list[dict]:
        p = artifact_path(run_id, artifact_type, "parquet")
        if not p.exists() or p.stat().st_size == 0:
            return []
        return query_parquet(p, limit=100_000, offset=0)

    def _load_paged(artifact_type: str) -> list[dict]:
        p = artifact_path(run_id, artifact_type, "parquet")
        if not p.exists() or p.stat().st_size == 0:
            return []
        return query_parquet(p, limit=limit, offset=offset)

    if part in ("signal_stats", "all"):
        rows = _load_stats("signal_stats")
        body["signal_statistics"] = rows
        meta["signal_stats_count"] = len(rows)

    if part in ("pattern_stats", "all"):
        rows = _load_stats("pattern_stats")
        body["pattern_statistics"] = rows
        meta["pattern_stats_count"] = len(rows)

    if part in ("filter_impact", "all"):
        rows = _load_stats("filter_impact")
        body["filter_impact_statistics"] = rows
        meta["filter_impact_count"] = len(rows)

    if part in ("events", "all"):
        rows = _load_paged("events")
        body["events"] = rows
        meta["events_count"]  = len(rows)
        meta["events_offset"] = offset
        meta["events_limit"]  = limit

    if part in ("outcomes", "all"):
        rows = _load_paged("outcomes")
        body["outcomes"] = rows
        meta["outcomes_count"]  = len(rows)
        meta["outcomes_offset"] = offset
        meta["outcomes_limit"]  = limit

    # ── CSV export for single-part tabular exports ──
    if use_csv and part in ("signal_stats", "pattern_stats", "filter_impact", "events", "outcomes"):
        key_map = {
            "signal_stats": "signal_statistics",
            "pattern_stats": "pattern_statistics",
            "filter_impact": "filter_impact_statistics",
            "events": "events",
            "outcomes": "outcomes",
        }
        data_key = key_map.get(part, part)
        rows = body.get(data_key, [])
        filename = f"replay_{run_id}_{part}.csv"
        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        else:
            buf.write(f"# No data for {part} in run {run_id}\n")
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    _part_names = {
        "all":           "all_in_one",
        "run":           "run_metadata",
        "signal_stats":  "signal_statistics",
        "pattern_stats": "pattern_statistics",
        "filter_impact": "filter_impact",
        "events":        "events",
        "outcomes":      "outcomes",
    }
    _name = _part_names.get(part, part)
    filename = (f"replay_{run_id}_{_name}_{offset}-{offset + limit}.json"
                if part in ("events", "outcomes")
                else f"replay_{run_id}_{_name}.json")

    content = json.dumps(body, default=str, indent=2)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{run_id}")
def delete_run(run_id: int) -> dict:
    state = _get_state()
    if state.get("running") and state.get("run_id") == run_id:
        raise HTTPException(status_code=409, detail="Cannot delete the active run")

    ph = _ph()
    run = _query_one(f"SELECT id FROM signal_replay_runs WHERE id={ph}", [run_id])
    if not run:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    try:
        # 1. Delete disk artifacts
        disk_deleted = delete_run_directory(run_id)

        # 2. Delete DB rows (artifacts + run)
        with get_db() as db:
            db.execute(f"DELETE FROM replay_artifacts WHERE run_id={ph}", [run_id])
            db.execute(f"DELETE FROM signal_replay_runs WHERE id={ph}", [run_id])
            db.commit()

        return {"deleted": True, "run_id": run_id, "disk_directory_removed": disk_deleted}
    except Exception as exc:
        log.exception("delete_run failed for run %s", run_id)
        raise HTTPException(status_code=500, detail=f"delete failed: {exc}")


@router.post("/purge-all")
def purge_all_runs(confirm: str | None = None) -> dict:
    """Nuke ALL replay data: disk directories + Postgres rows. Requires confirm=YES."""
    state = _get_state()
    if state.get("running"):
        raise HTTPException(status_code=409,
                            detail="Active run in progress — stop it before purging")
    if confirm != "YES":
        raise HTTPException(status_code=400,
                            detail="Pass ?confirm=YES to confirm full purge of all replay data")
    try:
        dirs_deleted = delete_all_run_directories()
        with get_db() as db:
            if USE_PG:
                db.execute("TRUNCATE TABLE replay_artifacts RESTART IDENTITY")
                db.execute("TRUNCATE TABLE signal_replay_runs RESTART IDENTITY")
            else:
                db.execute("DELETE FROM replay_artifacts")
                db.execute("DELETE FROM signal_replay_runs")
            db.commit()
        return {
            "purged": True,
            "run_directories_deleted": dirs_deleted,
            "tables_cleared": ["signal_replay_runs", "replay_artifacts"],
        }
    except Exception as exc:
        log.exception("purge_all_runs failed")
        raise HTTPException(status_code=500, detail=f"purge failed: {exc}")

"""
ultra_pump_routes.py — FastAPI endpoints for the ULTRA Pump Research Engine.

Endpoints (all under /api/ultra-pump):
  POST   /run
  POST   /stop
  POST   /pause
  POST   /resume
  GET    /status
  GET    /history
  GET    /{run_id}
  GET    /{run_id}/episodes
  GET    /{run_id}/caught
  GET    /{run_id}/missed
  GET    /{run_id}/ultra-patterns
  GET    /{run_id}/pattern-lift
  GET    /{run_id}/split-impact
  GET    /{run_id}/diagnostics
  GET    /{run_id}/recommendations
  GET    /{run_id}/research-bundle
  GET    /{run_id}/export-manifest
  GET    /{run_id}/export?part=...
  POST   /{run_id}/rebuild
  POST   /{run_id}/recalculate
  POST   /{run_id}/full-rescan
  DELETE /{run_id}
"""
from __future__ import annotations
import csv
import datetime
import io
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from db import get_db, USE_PG
from ultra_pump_research_engine import (
    get_state as _get_state,
    run_ultra_pump_research as _run_engine,
    _insert_run as _insert_run_row,
    request_stop as _request_stop,
    request_pause as _request_pause,
    request_resume as _request_resume,
    ALLOWED_HORIZONS, ALLOWED_PRE_PUMP_WINDOW, ALLOWED_SCANNER_WINDOW,
    ALLOWED_LOOKBACK_BARS, ALLOWED_SPLIT_IMPACT_WINDOW,
    DEFAULT_PUMP_HORIZON, DEFAULT_PRE_PUMP_WINDOW_BARS,
    DEFAULT_SCANNER_WINDOW_BARS, DEFAULT_DETECTION_REFERENCE,
    DEFAULT_LOOKBACK_BARS, DEFAULT_SPLIT_IMPACT_WINDOW,
)
from replay_storage import (
    run_dir, artifact_path, query_parquet, count_parquet,
    delete_run_directory,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ultra-pump", tags=["ultra_pump"])


# ── Pydantic models ──────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    universe: str = Field("all_us", pattern="^(sp500|nasdaq|nasdaq_gt5|split|all_us)$")
    mode:     str = Field("date_range", pattern="^(single_day|date_range|last_n_days|ytd)$")
    pump_target: str = Field("X2_TO_X4", pattern="^(X2_TO_X4|X4_PLUS|BOTH)$")
    start_date: str | None = None
    end_date:   str | None = None
    lookback_days: int | None = None

    pump_horizon:                  int | None = None
    pre_pump_window_bars:          int | None = None
    scanner_detection_window_bars: int | None = None
    detection_reference:           str | None = None
    lookback_bars:                 int | None = None
    split_impact_window_days:      int | None = None

    research_mode:    str = "standard"
    event_scope:      str = "pumps_only"
    min_price:         float | None = None
    min_volume:        int   | None = None
    min_dollar_volume: float | None = None
    benchmark_symbol:  str = "QQQ"

    @field_validator("pump_horizon", mode="before")
    @classmethod
    def _v_horizon(cls, v):
        if v is None:
            return None
        v = int(v)
        if v not in ALLOWED_HORIZONS:
            raise ValueError(f"pump_horizon must be one of {sorted(ALLOWED_HORIZONS)}")
        return v

    @field_validator("pre_pump_window_bars", mode="before")
    @classmethod
    def _v_pp(cls, v):
        if v is None:
            return None
        v = int(v)
        if v not in ALLOWED_PRE_PUMP_WINDOW:
            raise ValueError(f"pre_pump_window_bars must be one of {sorted(ALLOWED_PRE_PUMP_WINDOW)}")
        return v

    @field_validator("scanner_detection_window_bars", mode="before")
    @classmethod
    def _v_sw(cls, v):
        if v is None:
            return None
        v = int(v)
        if v not in ALLOWED_SCANNER_WINDOW:
            raise ValueError(f"scanner_detection_window_bars must be one of {sorted(ALLOWED_SCANNER_WINDOW)}")
        return v

    @field_validator("lookback_bars", mode="before")
    @classmethod
    def _v_lb(cls, v):
        if v is None:
            return None
        v = int(v)
        if v not in ALLOWED_LOOKBACK_BARS:
            raise ValueError(f"lookback_bars must be one of {sorted(ALLOWED_LOOKBACK_BARS)}")
        return v

    @field_validator("split_impact_window_days", mode="before")
    @classmethod
    def _v_si(cls, v):
        if v is None:
            return None
        v = int(v)
        if v not in ALLOWED_SPLIT_IMPACT_WINDOW:
            raise ValueError(f"split_impact_window_days must be one of {sorted(ALLOWED_SPLIT_IMPACT_WINDOW)}")
        return v


# ── Helpers ──────────────────────────────────────────────────────────────────

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


def _artifact_path_if_ready(run_id: int, slot: str) -> Path | None:
    p = artifact_path(run_id, slot, "parquet")
    if not p.exists() or p.stat().st_size == 0:
        return None
    return p


def _csv_neutralize_value(v: Any) -> Any:
    """Prefix any cell starting with = + - @ with a single quote (CSV-injection guard)."""
    if isinstance(v, str) and v and v[0] in ("=", "+", "-", "@"):
        return "'" + v
    return v


# ── Endpoints: lifecycle ─────────────────────────────────────────────────────

@router.post("/run")
def start_run(req: RunRequest, background_tasks: BackgroundTasks) -> dict:
    state = _get_state()
    if state.get("running"):
        raise HTTPException(
            status_code=409,
            detail=(f"ULTRA Pump Research already running (run_id={state.get('run_id')}). "
                    "Stop or delete the active run first."),
        )
    payload = req.model_dump()

    mode = payload["mode"]
    if mode == "single_day" and not payload.get("as_of_date" if False else "start_date"):
        # single_day uses start_date as the anchor
        if not payload.get("start_date"):
            raise HTTPException(status_code=400, detail="start_date required for single_day")
    if mode == "date_range" and not (payload.get("start_date") and payload.get("end_date")):
        raise HTTPException(status_code=400, detail="start_date and end_date required for date_range")
    if mode == "last_n_days" and not payload.get("lookback_days"):
        raise HTTPException(status_code=400, detail="lookback_days required for last_n_days")

    run_id = _insert_run_row(payload)
    background_tasks.add_task(_run_engine, run_id, payload)
    return {"run_id": run_id, "status": "running", "message": "ULTRA Pump Research started"}


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
    sql = (f"SELECT id, status, universe, mode, pump_target, start_date, end_date, "
           f"pump_horizon, pre_pump_window_bars, scanner_detection_window_bars, "
           f"detection_reference, lookback_bars, split_impact_window_days, "
           f"total_episodes, total_caught, total_missed, symbols_total, symbols_completed, "
           f"summary_json, artifact_manifest_json, error_message, "
           f"started_at, finished_at "
           f"FROM ultra_pump_runs ORDER BY id DESC LIMIT {limit}")
    runs = _query(sql)
    for run in runs:
        rid = run.get("id")
        if rid:
            rdir = run_dir(rid)
            run["artifacts_on_disk"] = rdir.exists()
            if rdir.exists():
                run["disk_bytes"] = sum(
                    f.stat().st_size for f in rdir.rglob("*") if f.is_file()
                )
    return runs


@router.get("/{run_id}")
def get_run(run_id: int) -> dict:
    ph = _ph()
    row = _query_one(f"SELECT * FROM ultra_pump_runs WHERE id={ph}", [run_id])
    if not row:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    artifacts = _query(
        f"SELECT artifact_type, file_path, format, row_count, size_bytes, created_at "
        f"FROM replay_artifacts WHERE run_id={ph} ORDER BY artifact_type",
        [run_id],
    )
    row = dict(row)
    row["artifacts"] = artifacts
    # Attach manifest JSON if present
    em_path = run_dir(run_id) / "export_manifest.json"
    if em_path.exists():
        try:
            row["export_manifest"] = json.loads(em_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return row


@router.delete("/{run_id}")
def delete_run(run_id: int) -> dict:
    state = _get_state()
    if state.get("running") and state.get("run_id") == run_id:
        raise HTTPException(status_code=409, detail="Cannot delete the active run")
    ph = _ph()
    run = _query_one(f"SELECT id FROM ultra_pump_runs WHERE id={ph}", [run_id])
    if not run:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    try:
        disk_deleted = delete_run_directory(run_id)
        with get_db() as db:
            db.execute(f"DELETE FROM replay_artifacts WHERE run_id={ph}", [run_id])
            db.execute(f"DELETE FROM ultra_pump_runs WHERE id={ph}", [run_id])
            db.commit()
        return {"deleted": True, "run_id": run_id, "disk_directory_removed": disk_deleted}
    except Exception as exc:
        log.exception("ultra_pump delete failed for run %s", run_id)
        raise HTTPException(status_code=500, detail=f"delete failed: {exc}")


# ── Endpoints: artifact listing (read parquet via DuckDB) ───────────────────

def _list_artifact_rows(
    run_id: int,
    slot: str,
    *,
    conditions: list[tuple[str, str, Any]] | None = None,
    sort_col: str = "anchor_date",
    sort_dir: str = "DESC",
    limit: int = 500,
    offset: int = 0,
) -> dict:
    p = _artifact_path_if_ready(run_id, slot)
    if p is None:
        return {"total": 0, "limit": limit, "offset": offset, "rows": []}
    try:
        rows = query_parquet(p, conditions or [], sort_col=sort_col,
                             sort_dir=sort_dir, limit=limit, offset=offset)
        total = count_parquet(p, conditions or [])
    except Exception as exc:
        log.warning("ultra_pump list %s/%s failed: %s", run_id, slot, exc)
        return {"total": 0, "limit": limit, "offset": offset, "rows": []}
    return {"total": total, "limit": limit, "offset": offset, "rows": rows}


@router.get("/{run_id}/episodes")
def list_episodes(
    run_id: int,
    category: str | None = None,
    symbol: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict:
    conds: list[tuple[str, str, Any]] = []
    if category:
        conds.append(("category", "=", category))
    if symbol:
        conds.append(("symbol", "=", symbol))
    return _list_artifact_rows(run_id, "pump_episodes", conditions=conds,
                               sort_col="anchor_date", sort_dir="DESC",
                               limit=max(1, min(limit, 5000)), offset=max(0, offset))


@router.get("/{run_id}/caught")
def list_caught(run_id: int, limit: int = 500, offset: int = 0) -> dict:
    return _list_artifact_rows(run_id, "scanner_caught_pumps",
                               sort_col="anchor_date", sort_dir="DESC",
                               limit=max(1, min(limit, 5000)), offset=max(0, offset))


@router.get("/{run_id}/missed")
def list_missed(run_id: int, limit: int = 500, offset: int = 0) -> dict:
    return _list_artifact_rows(run_id, "missed_pumps",
                               sort_col="anchor_date", sort_dir="DESC",
                               limit=max(1, min(limit, 5000)), offset=max(0, offset))


@router.get("/{run_id}/ultra-patterns")
def list_ultra_patterns(run_id: int, limit: int = 500, offset: int = 0) -> dict:
    return _list_artifact_rows(run_id, "ultra_pattern_stats",
                               sort_col="lift_vs_baseline", sort_dir="DESC",
                               limit=max(1, min(limit, 5000)), offset=max(0, offset))


@router.get("/{run_id}/pattern-lift")
def list_pattern_lift(run_id: int, limit: int = 500, offset: int = 0) -> dict:
    return _list_artifact_rows(run_id, "ultra_pattern_lift_stats",
                               sort_col="lift_all", sort_dir="DESC",
                               limit=max(1, min(limit, 5000)), offset=max(0, offset))


@router.get("/{run_id}/split-impact")
def list_split_impact(run_id: int, limit: int = 500, offset: int = 0) -> dict:
    return _list_artifact_rows(run_id, "split_impact_stats",
                               sort_col="lift", sort_dir="DESC",
                               limit=max(1, min(limit, 5000)), offset=max(0, offset))


@router.get("/{run_id}/diagnostics")
def list_diagnostics(run_id: int, limit: int = 500, offset: int = 0) -> dict:
    return _list_artifact_rows(run_id, "missed_diagnostics",
                               sort_col="episode_id", sort_dir="ASC",
                               limit=max(1, min(limit, 5000)), offset=max(0, offset))


# ── JSON artifact endpoints ──────────────────────────────────────────────────

def _read_json_artifact(run_id: int, name: str) -> dict | None:
    p = run_dir(run_id) / f"{name}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


@router.get("/{run_id}/recommendations")
def get_recommendations(run_id: int) -> dict:
    body = _read_json_artifact(run_id, "ultra_recommendations")
    if body is None:
        raise HTTPException(status_code=404, detail="recommendations not yet written")
    return body


@router.get("/{run_id}/research-bundle")
def get_research_bundle(run_id: int) -> dict:
    body = _read_json_artifact(run_id, "research_bundle")
    if body is None:
        raise HTTPException(status_code=404, detail="research_bundle not yet written")
    return body


@router.get("/{run_id}/export-manifest")
def get_export_manifest(run_id: int) -> dict:
    body = _read_json_artifact(run_id, "export_manifest")
    if body is None:
        raise HTTPException(status_code=404, detail="export_manifest not yet written")
    return body


# ── Control endpoints (Phase 5 fills behavior; Phase 1 = stubs) ─────────────

def _rebuild_in_background(run_id: int, overrides: dict | None = None) -> None:
    """Re-derive downstream artifacts (patterns / lift / split / recs) from
    existing episodes + pre_pump_* parquets — does NOT re-fetch bars.

    `overrides` (optional) merges into the original run's settings_json — used
    by recalculate to apply a new split_impact_window_days etc.
    """
    import json as _json
    from datetime import datetime as _dt
    from pathlib import Path as _P
    from replay_storage import (
        run_dir as _rd, write_parquet as _wp, write_json as _wj,
        query_parquet as _qp,
    )
    from db import get_db as _gdb, USE_PG as _PG
    from ultra_pump_research_engine import (
        _MISSED_RECOMMENDED_FIXES, _ARTIFACT_DESCRIPTIONS,
        _derived_settings_hash, _write_empty_artifact, artifact_schema,
        DEFAULT_SPLIT_IMPACT_WINDOW,
    )
    from ultra_pump_patterns import mine_patterns, sample_baseline_windows
    from ultra_pump_split import (
        classify_episodes_split_status, build_split_partitions,
        split_aware_pattern_stats,
    )
    from ultra_pump_recommendations import build_recommendations

    rdir = _rd(run_id)
    if not rdir.exists():
        return

    ph = "%s" if _PG else "?"
    with _gdb() as db:
        db.execute(f"SELECT * FROM ultra_pump_runs WHERE id={ph}", [run_id])
        row = db.fetchone()
    if not row:
        return
    settings_json = row.get("settings_json") if isinstance(row, dict) else None
    payload = {}
    if settings_json:
        try:
            payload = _json.loads(settings_json)
        except Exception:
            payload = {}
    if overrides:
        payload.update(overrides)

    # Load existing artifacts
    def _load(slot: str) -> list[dict]:
        p = rdir / f"{slot}.parquet"
        if not p.exists() or p.stat().st_size == 0:
            return []
        try:
            return _qp(p, [], sort_col="episode_id" if "episode" in slot else "pattern_key",
                       sort_dir="ASC", limit=1_000_000, offset=0)
        except Exception:
            return []

    episodes        = _load("pump_episodes")
    pre_pump_bars   = _load("pre_pump_ultra_bars")
    pre_pump_signals= _load("pre_pump_ultra_signals")
    pre_pump_combos = _load("pre_pump_ultra_combinations")
    if not episodes:
        return

    pump_horizon    = int(payload.get("pump_horizon") or 60)
    pre_pump_window = int(payload.get("pre_pump_window_bars") or 14)
    split_window    = int(payload.get("split_impact_window_days") or DEFAULT_SPLIT_IMPACT_WINDOW)

    # Re-mine patterns
    baseline_windows_rows = sample_baseline_windows(
        episodes, pre_pump_window_bars=pre_pump_window, pump_horizon=pump_horizon,
    )
    baseline_total = len(baseline_windows_rows)
    pattern_rows, lift_rows, timing_rows = mine_patterns(
        episodes, pre_pump_bars, pre_pump_signals, pre_pump_combos,
        baseline_total=baseline_total,
    )

    # Re-classify splits
    episodes_with_split = classify_episodes_split_status(
        episodes, split_impact_window_days=split_window,
    )
    partitions = build_split_partitions(episodes_with_split)
    split_impact_rows, lift_rows_updated = split_aware_pattern_stats(
        pattern_rows, lift_rows, episodes_with_split,
        pre_pump_signals, pre_pump_combos,
    )

    # Rewrite artifacts
    def _rw(slot: str, rows: list[dict], reason: str) -> None:
        p = rdir / f"{slot}.parquet"
        if rows:
            _wp(p, rows)
        else:
            _write_empty_artifact(p, artifact_schema(slot), reason)

    _rw("ultra_pattern_stats", pattern_rows, "No patterns (rebuild).")
    _rw("ultra_pattern_lift_stats", lift_rows_updated, "No lift rows (rebuild).")
    _rw("ultra_timing_stats", timing_rows, "No timing rows (rebuild).")
    _rw("baseline_windows", baseline_windows_rows, "No baseline windows (rebuild).")
    _rw("split_impact_stats", split_impact_rows, "No split-impact stats (rebuild).")
    _rw("split_related_pumps", partitions["split_related_pumps"], "No split-related (rebuild).")
    _rw("clean_non_split_pumps", partitions["clean_non_split_pumps"], "No clean (rebuild).")
    _rw("post_reverse_split_pumps", partitions["post_reverse_split_pumps"], "No post-reverse (rebuild).")

    # Recommendations
    summary = {
        "total_episodes": len(episodes),
        "rebuild": True,
        "baseline_total": baseline_total,
        "pattern_count": len(pattern_rows),
    }
    recs_bundle = build_recommendations(pattern_rows, lift_rows_updated, summary=summary)
    created_at_iso = _dt.utcnow().isoformat() + "Z"
    derived_hash = _derived_settings_hash(payload)
    _wj(rdir / "ultra_recommendations.json", {
        "run_id": run_id,
        "generated_at": created_at_iso,
        "derived_settings_hash": derived_hash,
        "rebuild": True,
        **recs_bundle,
    })

    # Keep research_bundle.json in sync with the rebuilt recommendations so
    # /research-bundle and /recommendations never disagree after a rebuild.
    existing_bundle: dict = {}
    rb_path = rdir / "research_bundle.json"
    if rb_path.exists():
        try:
            existing_bundle = _json.loads(rb_path.read_text(encoding="utf-8"))
        except Exception:
            existing_bundle = {}
    warnings = list(existing_bundle.get("warnings") or [])
    settings = dict(existing_bundle.get("settings") or {})
    settings.update({
        "universe":                       payload.get("universe"),
        "pump_target":                    payload.get("pump_target"),
        "pump_horizon":                   pump_horizon,
        "pre_pump_window_bars":           pre_pump_window,
        "scanner_detection_window_bars": int(payload.get("scanner_detection_window_bars") or 14),
        "split_impact_window_days":      split_window,
        "lookback_bars":                  int(payload.get("lookback_bars") or 500),
        "start_date":                     payload.get("start_date"),
        "end_date":                       payload.get("end_date"),
    })
    research_bundle = {
        "run_id":               run_id,
        "generated_at":         created_at_iso,
        "derived_settings_hash": derived_hash,
        "summary":              summary,
        "verdict_counts":       recs_bundle.get("verdict_counts", {}),
        "top_recommendations":  recs_bundle.get("recommendations", [])[:25],
        "split_impact_stats":   split_impact_rows,
        "split_partition_counts": {
            "clean_non_split":      len(partitions["clean_non_split_pumps"]),
            "split_related":        len(partitions["split_related_pumps"]),
            "post_reverse_split":   len(partitions["post_reverse_split_pumps"]),
        },
        "warnings":             warnings,
        "phase":                "rebuild_complete",
        "rebuild":              True,
        "settings":             settings,
    }
    _wj(rb_path, research_bundle)


@router.post("/{run_id}/rebuild")
def rebuild_run(run_id: int, background_tasks: BackgroundTasks) -> dict:
    """Re-derive downstream artifacts (patterns/lift/splits/recs) from existing
    episodes + pre_pump parquets. Does NOT re-fetch market bars.
    """
    ph = _ph()
    run = _query_one(f"SELECT id, status FROM ultra_pump_runs WHERE id={ph}", [run_id])
    if not run:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    state = _get_state()
    if state.get("running") and state.get("run_id") == run_id:
        raise HTTPException(status_code=409, detail="Run is still active; cannot rebuild")
    background_tasks.add_task(_rebuild_in_background, run_id, None)
    return {"run_id": run_id, "queued": True, "action": "rebuild",
            "message": "Rebuild queued — patterns, lift, splits, recommendations will be re-derived."}


@router.post("/{run_id}/recalculate")
def recalculate_run(
    run_id: int,
    background_tasks: BackgroundTasks,
    split_impact_window_days: int | None = None,
) -> dict:
    """Like rebuild, but allows tweaking a derived-only setting
    (e.g. split_impact_window_days) without invalidating episode detection."""
    ph = _ph()
    run = _query_one(f"SELECT id FROM ultra_pump_runs WHERE id={ph}", [run_id])
    if not run:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    state = _get_state()
    if state.get("running") and state.get("run_id") == run_id:
        raise HTTPException(status_code=409, detail="Run is still active; cannot recalculate")
    overrides: dict[str, Any] = {}
    if split_impact_window_days is not None:
        if split_impact_window_days not in ALLOWED_SPLIT_IMPACT_WINDOW:
            raise HTTPException(status_code=400,
                                detail=f"split_impact_window_days must be one of {sorted(ALLOWED_SPLIT_IMPACT_WINDOW)}")
        overrides["split_impact_window_days"] = split_impact_window_days
    background_tasks.add_task(_rebuild_in_background, run_id, overrides)
    return {"run_id": run_id, "queued": True, "action": "recalculate",
            "overrides": overrides,
            "message": "Recalculate queued — derived artifacts will be regenerated with the overrides."}


@router.post("/{run_id}/full-rescan")
def full_rescan_run(run_id: int, background_tasks: BackgroundTasks) -> dict:
    """Re-run the entire detection pipeline from scratch using the original
    settings. This re-fetches market bars and rebuilds every artifact."""
    state = _get_state()
    if state.get("running"):
        raise HTTPException(status_code=409,
                            detail=f"Another run is in progress (run_id={state.get('run_id')})")
    ph = _ph()
    run = _query_one(f"SELECT id, settings_json FROM ultra_pump_runs WHERE id={ph}", [run_id])
    if not run:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    settings_json = run.get("settings_json") if isinstance(run, dict) else None
    if not settings_json:
        raise HTTPException(status_code=400, detail="run has no settings_json to replay")
    try:
        payload = json.loads(settings_json)
    except Exception:
        raise HTTPException(status_code=400, detail="run settings_json is malformed")

    new_run_id = _insert_run_row(payload)
    background_tasks.add_task(_run_engine, new_run_id, payload)
    return {"run_id": new_run_id, "queued": True, "action": "full_rescan",
            "source_run_id": run_id,
            "message": "Full rescan queued — a NEW run was created with the original settings."}


# ── Export ───────────────────────────────────────────────────────────────────

_EXPORT_PARTS_PARQUET = {
    "pump_episodes", "x2_to_x4_episodes", "x4_plus_episodes",
    "scanner_caught_pumps", "missed_pumps",
    "pre_pump_ultra_bars", "pre_pump_ultra_signals", "pre_pump_ultra_combinations",
    "missed_diagnostics",
    "ultra_pattern_stats", "ultra_pattern_lift_stats", "ultra_timing_stats",
    "baseline_windows", "baseline_ultra_patterns", "baseline_pattern_stats",
    "split_impact_stats", "split_related_pumps",
    "clean_non_split_pumps", "post_reverse_split_pumps",
}
_EXPORT_PARTS_JSON = {"run", "research_bundle", "export_manifest", "ultra_recommendations", "warnings"}
_EXPORT_PARTS_SPECIAL = {"all_non_empty_zip"}


@router.get("/{run_id}/export")
def export_run(
    run_id: int,
    part: str = Query("pump_episodes"),
    fmt: str = "json",
    offset: int = 0,
    limit: int = 100_000,
) -> Response:
    ph = _ph()
    run = _query_one(f"SELECT * FROM ultra_pump_runs WHERE id={ph}", [run_id])
    if not run:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    valid = _EXPORT_PARTS_PARQUET | _EXPORT_PARTS_JSON | _EXPORT_PARTS_SPECIAL
    if part not in valid:
        raise HTTPException(status_code=400, detail=f"part must be one of {sorted(valid)}")

    run_status = run["status"] if isinstance(run, dict) else getattr(run, "status", None)
    if run_status in ("running", "paused") and part != "run":
        raise HTTPException(
            status_code=409,
            detail=(f"Run {run_id} is still {run_status}. Artifacts are finalized after the "
                    "run completes — only the 'run' metadata is available now."),
        )

    use_csv = str(fmt).lower() == "csv"

    # ── JSON-only parts ──
    if part in _EXPORT_PARTS_JSON:
        if part == "run":
            content = json.dumps(dict(run), default=str, indent=2)
        else:
            p = run_dir(run_id) / f"{part}.json"
            if not p.exists():
                content = json.dumps({"run_id": run_id, "status": "no_data",
                                      "message": f"{part}.json not yet written"}, indent=2)
            else:
                content = p.read_text(encoding="utf-8")
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="ultra_pump_{run_id}_{part}.json"'},
        )

    # ── all_non_empty_zip ──
    if part == "all_non_empty_zip":
        import zipfile
        rdir = run_dir(run_id)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            if not rdir.exists():
                zf.writestr("EMPTY.txt", f"run {run_id} has no artifacts on disk")
            else:
                for f in sorted(rdir.iterdir()):
                    if not f.is_file():
                        continue
                    if f.suffix == ".parquet" and f.stat().st_size <= 1024:
                        # parquet with only schema (empty) — skip from "non_empty" zip
                        # but only if the parquet has 0 rows
                        try:
                            n = count_parquet(f, [])
                        except Exception:
                            n = 0
                        if n == 0:
                            continue
                    zf.write(f, arcname=f.name)
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="ultra_pump_{run_id}_all_non_empty.zip"'},
        )

    # ── Parquet-backed parts → JSON or CSV ──
    p = artifact_path(run_id, part, "parquet")
    if not p.exists() or p.stat().st_size == 0:
        # Empty / missing → still respond with an empty body and the reason
        em = _read_json_artifact(run_id, "export_manifest") or {}
        reason = None
        for a in em.get("artifacts", []):
            if a.get("artifact_name") == part:
                reason = a.get("reason_if_empty")
                break
        if use_csv:
            return Response(
                content=f"# no rows for {part}: {reason or 'artifact missing'}\n",
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="ultra_pump_{run_id}_{part}.csv"'},
            )
        return Response(
            content=json.dumps({"run_id": run_id, "part": part, "rows": [],
                                "reason_if_empty": reason}, default=str, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="ultra_pump_{run_id}_{part}.json"'},
        )

    rows = query_parquet(p, [], sort_col="anchor_date" if part.endswith("episodes") else "episode_id",
                         sort_dir="DESC", limit=max(1, min(limit, 1_000_000)),
                         offset=max(0, offset))

    if use_csv:
        buf = io.StringIO()
        if rows:
            fieldnames = list(rows[0].keys())
            writer = csv.DictWriter(buf, fieldnames=fieldnames)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: _csv_neutralize_value(v) for k, v in r.items()})
        else:
            buf.write(f"# no rows for {part}\n")
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="ultra_pump_{run_id}_{part}.csv"'},
        )

    body = {
        "run_id": run_id,
        "exported_at": datetime.datetime.utcnow().isoformat() + "Z",
        "part": part,
        "offset": offset,
        "limit": limit,
        "row_count": len(rows),
        "rows": rows,
    }
    return Response(
        content=json.dumps(body, default=str, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="ultra_pump_{run_id}_{part}.json"'},
    )

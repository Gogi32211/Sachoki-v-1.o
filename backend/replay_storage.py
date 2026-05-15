"""
replay_storage.py — Hybrid Postgres+Disk storage for Signal Replay.

Postgres stores: run metadata (signal_replay_runs), artifact registry (replay_artifacts).
Disk stores:     heavy artifacts as Parquet files (events, outcomes, stats) + JSON.
DuckDB:          in-process query engine for Parquet files (no server needed).

Env var: REPLAY_STORAGE_DIR — persistent volume mount path.
Default: ./replay_runs (safe for local dev; set to /data/replay_runs in prod).
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

# ── Directory helpers ─────────────────────────────────────────────────────────

def storage_root() -> Path:
    d = Path(os.environ.get("REPLAY_STORAGE_DIR", "replay_runs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_dir(run_id: int) -> Path:
    return storage_root() / f"run_{run_id:06d}"


def artifact_path(run_id: int, artifact_type: str, fmt: str = "parquet") -> Path:
    ext = {"parquet": ".parquet", "json": ".json", "csv": ".csv"}.get(fmt, f".{fmt}")
    return run_dir(run_id) / f"{artifact_type}{ext}"


# ── Parquet I/O ───────────────────────────────────────────────────────────────

def write_parquet(path: Path, rows: list[dict]) -> int:
    """Write list[dict] → Parquet (zstd). Returns row count."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_bytes(b"")
        return 0
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, str(path), compression="zstd")
    return len(rows)


def _open_duckdb(path: Path):
    """Open DuckDB in-memory connection and verify parquet is readable."""
    import duckdb
    if not path.exists() or path.stat().st_size == 0:
        return None, []
    con = duckdb.connect()
    return con, []


def _duck_query(path: Path, sql: str, params: list | None = None) -> list[dict]:
    """Execute DuckDB SQL against a parquet file. `sql` must SELECT from read_parquet(path)."""
    import duckdb
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        con = duckdb.connect()
        result = con.execute(sql, params or [])
        cols = [d[0] for d in result.description]
        rows = result.fetchall()
        con.close()
        return [dict(zip(cols, row)) for row in rows]
    except Exception as exc:
        log.error("duck_query failed (%s): %s", path.name, exc)
        return []


def _duck_count(path: Path, sql: str, params: list | None = None) -> int:
    import duckdb
    if not path.exists() or path.stat().st_size == 0:
        return 0
    try:
        con = duckdb.connect()
        result = con.execute(sql, params or [])
        row = result.fetchone()
        con.close()
        return int(row[0]) if row else 0
    except Exception as exc:
        log.error("duck_count failed (%s): %s", path.name, exc)
        return 0


def query_parquet(
    path: Path,
    conditions: list[tuple[str, str, Any]] | None = None,
    sort_col: str = "id",
    sort_dir: str = "DESC",
    limit: int = 200,
    offset: int = 0,
    columns: str = "*",
) -> list[dict]:
    """
    SELECT {columns} FROM read_parquet(path) [WHERE ...] ORDER BY ... LIMIT/OFFSET.

    conditions: list of (column_name, operator, value) e.g. [("symbol", "=", "AAPL")]
    Operator must be one of: = >= <= > < !=
    """
    _ALLOWED_OPS = {"=", ">=", "<=", ">", "<", "!="}
    where_parts: list[str] = []
    params: list[Any] = []
    for col, op, val in (conditions or []):
        if op not in _ALLOWED_OPS:
            raise ValueError(f"disallowed operator: {op!r}")
        where_parts.append(f'"{col}"{op}?')
        params.append(val)

    sql = f"SELECT {columns} FROM read_parquet('{path}')"
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    # DuckDB supports NULLS LAST syntax
    sql += f" ORDER BY {sort_col} {sort_dir} NULLS LAST LIMIT {limit} OFFSET {offset}"
    return _duck_query(path, sql, params)


def count_parquet(
    path: Path,
    conditions: list[tuple[str, str, Any]] | None = None,
) -> int:
    _ALLOWED_OPS = {"=", ">=", "<=", ">", "<", "!="}
    where_parts: list[str] = []
    params: list[Any] = []
    for col, op, val in (conditions or []):
        if op not in _ALLOWED_OPS:
            raise ValueError(f"disallowed operator: {op!r}")
        where_parts.append(f'"{col}"{op}?')
        params.append(val)
    sql = f"SELECT COUNT(*) FROM read_parquet('{path}')"
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    return _duck_count(path, sql, params)


# ── JSON I/O ──────────────────────────────────────────────────────────────────

def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, default=str, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── Artifact registry ─────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def register_artifact(
    db,
    run_id: int,
    artifact_type: str,
    path: Path,
    row_count: int,
    fmt: str = "parquet",
    schema_version: int = _SCHEMA_VERSION,
) -> None:
    """Upsert artifact record in replay_artifacts. Must be called inside a get_db() context."""
    from db import USE_PG
    ph = "%s" if USE_PG else "?"
    size_bytes = path.stat().st_size if path.exists() else 0
    sha = _sha256(path) if path.exists() and size_bytes > 0 else ""
    now_expr = "NOW()" if USE_PG else "datetime('now')"

    db.execute(
        f"DELETE FROM replay_artifacts WHERE run_id={ph} AND artifact_type={ph}",
        [run_id, artifact_type],
    )
    db.execute(
        f"INSERT INTO replay_artifacts "
        f"(run_id, artifact_type, file_path, format, row_count, size_bytes, sha256, "
        f"schema_version, created_at, updated_at) "
        f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{now_expr},{now_expr})",
        [run_id, artifact_type, str(path), fmt, row_count, size_bytes, sha, schema_version],
    )


# ── Deletion ──────────────────────────────────────────────────────────────────

def delete_run_directory(run_id: int) -> bool:
    """Remove run directory and all its contents from disk."""
    d = run_dir(run_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        log.info("replay_storage: deleted run dir %s", d)
        return True
    return False


def delete_all_run_directories() -> int:
    """Remove all run_XXXXXX directories under storage_root. Returns count deleted."""
    root = storage_root()
    count = 0
    for child in root.iterdir():
        if child.is_dir() and child.name.startswith("run_"):
            shutil.rmtree(child, ignore_errors=True)
            count += 1
    return count

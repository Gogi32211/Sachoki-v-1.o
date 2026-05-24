"""
progress.py — Scan progress store: Redis-backed with in-memory fallback.

Redis key : scan_progress:{run_id}  (TTL 6 h)
Memory    : _mem dict — lost on restart, good enough for staging

Redis is only for live progress. Completed scan data lives in PostgreSQL.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

# In-memory fallback: run_id → progress dict
_mem: dict[int, dict] = {}


def _redis():
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    try:
        import redis
        r = redis.from_url(url, decode_responses=True, socket_timeout=2)
        r.ping()
        return r
    except Exception as exc:
        log.debug("Redis unavailable: %s", exc)
        return None


def redis_available() -> bool:
    try:
        return _redis() is not None
    except Exception:
        return False


def set_progress(run_id: int, data: dict) -> None:
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    _mem[run_id] = data
    r = _redis()
    if r:
        try:
            r.setex(f"scan_progress:{run_id}", 21600, json.dumps(data, default=str))
        except Exception as exc:
            log.debug("Redis set_progress error: %s", exc)


def get_progress(run_id: int) -> dict | None:
    r = _redis()
    if r:
        try:
            raw = r.get(f"scan_progress:{run_id}")
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    return _mem.get(run_id)


def latest_run_id() -> int | None:
    if not _mem:
        return None
    return max(_mem.keys())


def get_latest_progress() -> dict | None:
    rid = latest_run_id()
    if rid is None:
        return None
    return get_progress(rid)

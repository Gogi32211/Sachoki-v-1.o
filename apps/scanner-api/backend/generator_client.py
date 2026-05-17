"""
generator_client.py — Phase E: bridge between scanner-api and generator-api.

Mirror of engine_client.py / market_data_client.py. When GENERATOR_API_URL
is set, calls forward to the remote generator-api over HTTP. When unset,
falls back to the in-process generator.py module — same surface either way.

Public API:

    generate_and_save(scan_run_id, candidates, admin_token=None) -> summary
        POST /api/generator/run when HTTP. In-process otherwise.

    get_view(scan_run_id, view_type) -> payload | None
        GET /api/generator/views/{view_type}. In-process otherwise.

    generator_api_health() -> dict
        Probe for /api/debug/status.

Once Phase E is verified, scanner-api can stop importing `from . import
generator` directly — but the import stays as in-process fallback for
local dev and the rollback case.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

GENERATOR_API_URL     = os.getenv("GENERATOR_API_URL", "").rstrip("/")
GENERATOR_API_TIMEOUT = float(os.getenv("GENERATOR_API_TIMEOUT", "30"))


def is_http_mode() -> bool:
    return bool(GENERATOR_API_URL)


def generator_api_health() -> dict:
    out: dict = {
        "generator_api_url_configured": bool(GENERATOR_API_URL),
        "generator_api_mode":           "http" if GENERATOR_API_URL else "in_process",
        "generator_api_url":            GENERATOR_API_URL or None,
        "generator_api_reachable":      None,
        "generator_api_version":        None,
        "generator_api_phase":          None,
        "generator_api_error":          None,
    }
    if not GENERATOR_API_URL:
        return out
    try:
        import httpx
        h = httpx.get(f"{GENERATOR_API_URL}/health", timeout=5)
        if h.status_code == 200 and h.json().get("status") == "ok":
            out["generator_api_reachable"] = True
            try:
                v = httpx.get(f"{GENERATOR_API_URL}/version", timeout=5).json()
                out["generator_api_version"] = v.get("version")
                out["generator_api_phase"]   = v.get("phase")
            except Exception:
                pass
        else:
            out["generator_api_reachable"] = False
            out["generator_api_error"]     = f"HTTP {h.status_code}"
    except Exception as exc:
        out["generator_api_reachable"] = False
        out["generator_api_error"]     = type(exc).__name__
    return out


# ── Public surface ───────────────────────────────────────────────────────────

def generate_and_save(
    scan_run_id: int,
    candidates: list[dict] | None = None,
    *,
    admin_token: str | None = None,
) -> dict:
    """
    Run all 4 generators for scan_run_id and persist payloads.

    Two modes:
      HTTP path:  generator-api fetches candidates from Postgres itself
                  (we just send `{run_id}` + admin_token). The `candidates`
                  arg is IGNORED in HTTP mode by design — generator-api
                  reads them fresh from the shared DB.
      In-process: requires `candidates` (legacy behavior — scanner-api
                  reads them and passes the list).

    Returns a summary dict.
    """
    if GENERATOR_API_URL:
        try:
            return _generate_http(scan_run_id, admin_token)
        except Exception as exc:
            log.warning("generator-api HTTP run failed (%s); falling back to in-process",
                        type(exc).__name__)
    return _generate_in_process(scan_run_id, candidates or [])


def get_view(scan_run_id: int, view_type: str) -> dict | None:
    if GENERATOR_API_URL:
        try:
            return _get_view_http(scan_run_id, view_type)
        except Exception as exc:
            log.warning("generator-api HTTP get_view failed for %s (%s); "
                        "falling back to in-process", view_type, type(exc).__name__)
    return _get_view_in_process(scan_run_id, view_type)


# ── In-process paths ─────────────────────────────────────────────────────────

def _generate_in_process(scan_run_id: int, candidates: list[dict]) -> dict:
    from . import generator as _local
    return _local.generate_and_save(scan_run_id, candidates)


def _get_view_in_process(scan_run_id: int, view_type: str) -> dict | None:
    from . import generator as _local
    return _local.get_view(scan_run_id, view_type)


# ── HTTP paths ───────────────────────────────────────────────────────────────

def _generate_http(scan_run_id: int, admin_token: str | None) -> dict:
    import httpx
    if not admin_token:
        return {"ok": False, "error": "admin_token required for HTTP generator run"}
    url = f"{GENERATOR_API_URL}/api/generator/run"
    body = {"run_id": scan_run_id} if scan_run_id else {}
    headers = {"x-admin-token": admin_token}
    resp = httpx.post(url, json=body, headers=headers, timeout=GENERATOR_API_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _get_view_http(scan_run_id: int, view_type: str) -> dict | None:
    import httpx
    url = f"{GENERATOR_API_URL}/api/generator/views/{view_type}"
    params = {"run_id": scan_run_id} if scan_run_id else {}
    resp = httpx.get(url, params=params, timeout=10)
    resp.raise_for_status()
    j = resp.json()
    return j.get("payload")


__all__ = [
    "generate_and_save", "get_view",
    "is_http_mode", "generator_api_health",
    "GENERATOR_API_URL",
]

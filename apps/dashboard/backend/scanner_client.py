"""
scanner_client.py — declarative BFF → scanner-api / research-api client.

Architecture rule (single source of truth for upstream calls):
    1. Every upstream HTTP call is REGISTERED below in ENDPOINTS, with its
       method, path, timeout, retry policy, upstream service.
    2. Route handlers in main.py call `scanner.call("name", ...)` — they
       never construct URLs, set timeouts, or catch httpx exceptions.
    3. All errors come back as structured codes (UPSTREAM_TIMEOUT,
       UPSTREAM_UNAVAILABLE, UPSTREAM_HTTP_4xx, etc.) so the frontend can
       react to *categories* instead of parsing error strings.

Adding a new upstream endpoint = add one row in ENDPOINTS. Nothing else.
Changing a timeout / retry policy = edit one row.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping

import httpx

log = logging.getLogger(__name__)


# ── Upstream service URLs ────────────────────────────────────────────────────

SCANNER_API_URL  = os.getenv("SCANNER_API_URL",  "").rstrip("/")
RESEARCH_API_URL = os.getenv("RESEARCH_API_URL", "").rstrip("/")


# ── Structured error codes (the frontend matches on these, NOT on message) ──

class UpstreamErrorCode:
    NOT_CONFIGURED  = "UPSTREAM_NOT_CONFIGURED"   # SCANNER_API_URL missing
    TIMEOUT         = "UPSTREAM_TIMEOUT"          # httpx.TimeoutException
    UNAVAILABLE     = "UPSTREAM_UNAVAILABLE"      # connection refused / DNS
    HTTP_4XX        = "UPSTREAM_HTTP_4XX"         # 4xx response
    HTTP_5XX        = "UPSTREAM_HTTP_5XX"         # 5xx response
    UNKNOWN         = "UPSTREAM_UNKNOWN"          # any other exception


@dataclass(frozen=True)
class UpstreamError:
    code:    str
    message: str
    status:  int | None = None     # populated for HTTP_4XX / HTTP_5XX

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.status is not None:
            out["status"] = self.status
        return out


# ── Endpoint registry ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Endpoint:
    name:    str
    service: str             # "scanner" | "research"
    method:  str             # "GET" | "POST"
    path:    str
    timeout: float           # seconds
    retries: int = 0         # 0 = no retry on TIMEOUT/UNAVAILABLE
    kind:    str = "rpc"     # "rpc" | "ack" | "stream-poll"
                              # ack = endpoint just acknowledges (e.g. scan-run);
                              # caller should be tolerant of TIMEOUT and fall
                              # back to polling.


ENDPOINTS: dict[str, Endpoint] = {
    # ── Health / debug ───────────────────────────────────────────────────────
    "scanner_health":      Endpoint("scanner_health",      "scanner", "GET",  "/health",                              timeout=3),
    "scanner_version":     Endpoint("scanner_version",     "scanner", "GET",  "/version",                             timeout=3),
    "scanner_signals":     Endpoint("scanner_signals",     "scanner", "GET",  "/api/chart/signals",                   timeout=5),
    # Phase B-2 follow-up: full debug/status from scanner-api (returns
    # engine_api_* probe results). Lighter timeout — the engine-api probe
    # itself uses 5s, so 8s caps total at slightly above that.
    "scanner_debug_status":Endpoint("scanner_debug_status","scanner", "GET",  "/api/debug/status",                    timeout=8),

    # ── Sample lists / split universe ────────────────────────────────────────
    "sample_lists":        Endpoint("sample_lists",        "scanner", "GET",  "/api/scans/ultra/sample-lists",        timeout=10),
    "split_universe":      Endpoint("split_universe",      "scanner", "GET",  "/api/scans/ultra/split-universe",      timeout=15),

    # ── Scan lifecycle ───────────────────────────────────────────────────────
    # "ack" semantics — POST only confirms the scan was started.
    # Cold scanner-api can take >5s to ack (cold psycopg2 + first connect),
    # but the scan ALWAYS runs in background. Caller should poll status on
    # timeout instead of treating it as failure.
    "scan_run":            Endpoint("scan_run",            "scanner", "POST", "/api/scans/ultra/run",                 timeout=30, kind="ack"),
    "scan_status":         Endpoint("scan_status",         "scanner", "GET",  "/api/scans/ultra/status",              timeout=5, kind="stream-poll"),
    "scan_cancel":         Endpoint("scan_cancel",         "scanner", "POST", "/api/scans/ultra/cancel",              timeout=5),

    # ── Latest scan + candidates ─────────────────────────────────────────────
    "scan_latest":         Endpoint("scan_latest",         "scanner", "GET",  "/api/scans/ultra/latest",              timeout=5),
    "scan_latest_candidates": Endpoint("scan_latest_candidates",
                                                          "scanner", "GET",  "/api/scans/ultra/latest/candidates",   timeout=10),

    # ── Admin ────────────────────────────────────────────────────────────────
    # market-data sync can be long-running (N symbols × Massive HTTP). Cap at
    # 120s here; the upstream itself can take longer but the client should
    # not block — better to give up the ack and let the UI poll.
    "admin_sync_market_data": Endpoint("admin_sync_market_data",
                                                          "scanner", "POST", "/api/admin/sync-market-data",          timeout=120, kind="ack"),
    "admin_generate_views":   Endpoint("admin_generate_views",
                                                          "scanner", "POST", "/api/admin/generate-views",            timeout=30,  kind="ack"),
    "get_view":               Endpoint("get_view",        "scanner", "GET",  "/api/views",                           timeout=10),

    # ── Chart endpoints (hit Massive via scanner-api) ────────────────────────
    "chart_candles":       Endpoint("chart_candles",       "scanner", "GET",  "/api/chart/candles",                   timeout=15),
    "chart_score":         Endpoint("chart_score",         "scanner", "GET",  "/api/chart/score",                     timeout=10),
    "chart_snapshot":      Endpoint("chart_snapshot",      "scanner", "GET",  "/api/chart/snapshot",                  timeout=15),
    "chart_history":       Endpoint("chart_history",       "scanner", "GET",  "/api/chart/history",                   timeout=20),
}


# ── Single HTTP client used by all callers ───────────────────────────────────

def _service_url(service: str) -> str:
    if service == "scanner":  return SCANNER_API_URL
    if service == "research": return RESEARCH_API_URL
    return ""


def call(
    name: str,
    *,
    params:  Mapping[str, Any] | None = None,
    body:    Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[dict | None, UpstreamError | None]:
    """
    Invoke a registered upstream endpoint. Never raises.

    Returns (data, None) on success or (None, UpstreamError) on failure.
    Route handlers in main.py serialize UpstreamError via .to_dict() and
    surface it to the frontend with HTTP 502/503/504 as appropriate (see
    main.py _err_response()).

    Retry policy: TIMEOUT and UNAVAILABLE retried up to ep.retries times
    with linear backoff (1s × attempt). 4xx and 5xx are NOT retried —
    they indicate real upstream issues, not transient transport problems.
    """
    if name not in ENDPOINTS:
        return None, UpstreamError(UpstreamErrorCode.UNKNOWN, f"endpoint {name!r} not registered")

    ep = ENDPOINTS[name]
    base = _service_url(ep.service)
    if not base:
        return None, UpstreamError(
            UpstreamErrorCode.NOT_CONFIGURED,
            f"{ep.service.upper()}_API_URL not configured",
        )

    url = f"{base}{ep.path}"
    last_err: UpstreamError | None = None

    hdrs = dict(headers or {})
    for attempt in range(ep.retries + 1):
        try:
            if ep.method == "GET":
                resp = httpx.get(url, params=dict(params or {}),
                                 headers=hdrs or None, timeout=ep.timeout)
            elif ep.method == "POST":
                resp = httpx.post(url, json=dict(body or {}),
                                  headers=hdrs or None, timeout=ep.timeout)
            else:
                return None, UpstreamError(UpstreamErrorCode.UNKNOWN, f"unsupported method {ep.method}")
            resp.raise_for_status()
            return resp.json(), None

        except httpx.TimeoutException:
            last_err = UpstreamError(UpstreamErrorCode.TIMEOUT, f"{ep.service} timeout on {ep.name}")
        except httpx.ConnectError:
            last_err = UpstreamError(UpstreamErrorCode.UNAVAILABLE, f"{ep.service} unreachable for {ep.name}")
        except httpx.HTTPStatusError as exc:
            sc = exc.response.status_code
            code = UpstreamErrorCode.HTTP_4XX if 400 <= sc < 500 else UpstreamErrorCode.HTTP_5XX
            # 4xx / 5xx are real upstream signals; do not retry.
            return None, UpstreamError(code, f"{ep.service} HTTP {sc} on {ep.name}", status=sc)
        except Exception as exc:
            last_err = UpstreamError(UpstreamErrorCode.UNKNOWN, f"{type(exc).__name__}: {exc}")

        if attempt < ep.retries:
            log.info("retry %d/%d for %s (%s)", attempt + 1, ep.retries, ep.name, last_err.code if last_err else "?")

    return None, last_err or UpstreamError(UpstreamErrorCode.UNKNOWN, "no response")


def err_to_http_status(err: UpstreamError) -> int:
    """Map an UpstreamError to an HTTP status code suitable for the BFF response."""
    if err.code == UpstreamErrorCode.NOT_CONFIGURED: return 503
    if err.code == UpstreamErrorCode.TIMEOUT:        return 504
    if err.code == UpstreamErrorCode.UNAVAILABLE:    return 503
    if err.code == UpstreamErrorCode.HTTP_4XX:       return err.status or 502
    if err.code == UpstreamErrorCode.HTTP_5XX:       return 502
    return 502


def err_response_body(err: UpstreamError) -> dict:
    """
    Canonical response body when an upstream call fails. Frontend reads
    `error_code` to decide behavior (e.g. retry, fall back to status poll).
    """
    return {
        "ok":          False,
        "error":       err.message,
        "error_code":  err.code,
        "error_status": err.status,
    }


__all__ = [
    "ENDPOINTS",
    "Endpoint",
    "UpstreamError",
    "UpstreamErrorCode",
    "SCANNER_API_URL",
    "RESEARCH_API_URL",
    "call",
    "err_to_http_status",
    "err_response_body",
]

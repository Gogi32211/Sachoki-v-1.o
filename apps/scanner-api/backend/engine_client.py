"""
engine_client.py — Phase B-2: bridge between scanner-api and engine-api.

When ENGINE_API_URL is set, run_engines() forwards the request to the
remote engine-api service over HTTP. When unset, it falls back to the
in-process engine_api/ subpackage (the same code we shipped in B-1).

This lets us:
    - Develop locally without spinning up two processes.
    - Deploy gradually: ENGINE_API_URL=<staging-engine-api> and scanner-api
      flips to the remote path one env-var-flip at a time.
    - Roll back instantly: unset ENGINE_API_URL → back to in-process.

Public API matches the in-process barrel:

    run_engines(ticker, timeframe, df, split_flags=None) -> list[bar]

Caller never knows whether the call went over the wire.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

ENGINE_API_URL = os.getenv("ENGINE_API_URL", "").rstrip("/")
ENGINE_API_TIMEOUT = float(os.getenv("ENGINE_API_TIMEOUT", "30"))


def run_engines(
    *,
    ticker:    str,
    timeframe: str,
    df:        pd.DataFrame,
    split_flags: dict | None = None,
) -> list[dict]:
    """
    Run the engine pipeline. Picks HTTP path if ENGINE_API_URL is set,
    otherwise falls back to in-process. On HTTP failure with no useful
    response, raises — caller decides whether to swallow or surface.
    """
    if ENGINE_API_URL:
        try:
            return _run_engines_http(ticker, timeframe, df, split_flags)
        except Exception as exc:
            log.warning("engine-api HTTP path failed for %s (%s); falling back to in-process",
                        ticker, type(exc).__name__)
            # Hard fallback so a temporary engine-api outage doesn't break scans.
            return _run_engines_in_process(ticker, timeframe, df, split_flags)
    return _run_engines_in_process(ticker, timeframe, df, split_flags)


def is_http_mode() -> bool:
    """True iff ENGINE_API_URL is set (i.e. compute happens remotely)."""
    return bool(ENGINE_API_URL)


def engine_api_health() -> dict:
    """
    Probe engine-api /health + /version. Used by /api/debug/status to
    surface visual confirmation that scanner-api ↔ engine-api wiring
    actually works. Returns dict; never raises.
    """
    out: dict = {
        "engine_api_url_configured": bool(ENGINE_API_URL),
        "engine_api_mode":           "http" if ENGINE_API_URL else "in_process",
        "engine_api_url":            ENGINE_API_URL or None,
        "engine_api_reachable":      None,
        "engine_api_version":        None,
        "engine_api_phase":          None,
        "engine_api_error":          None,
    }
    if not ENGINE_API_URL:
        return out
    try:
        import httpx
        h = httpx.get(f"{ENGINE_API_URL}/health", timeout=5)
        if h.status_code == 200 and h.json().get("status") == "ok":
            out["engine_api_reachable"] = True
            try:
                v = httpx.get(f"{ENGINE_API_URL}/version", timeout=5).json()
                out["engine_api_version"] = v.get("version")
                out["engine_api_phase"]   = v.get("phase")
            except Exception:
                pass
        else:
            out["engine_api_reachable"] = False
            out["engine_api_error"]     = f"HTTP {h.status_code}"
    except Exception as exc:
        out["engine_api_reachable"] = False
        out["engine_api_error"]     = type(exc).__name__
    return out


# ── In-process path ──────────────────────────────────────────────────────────

def _run_engines_in_process(
    ticker: str,
    timeframe: str,
    df: pd.DataFrame,
    split_flags: dict | None,
) -> list[dict]:
    from .engine_api import run_engines as _in_proc_run
    return _in_proc_run(
        ticker=ticker, timeframe=timeframe, df=df, split_flags=split_flags,
    )


# ── HTTP path ────────────────────────────────────────────────────────────────

def _run_engines_http(
    ticker: str,
    timeframe: str,
    df: pd.DataFrame,
    split_flags: dict | None,
) -> list[dict]:
    """
    POST /api/engines/run with serialized OHLCV. The remote service is
    pure-compute: it never reaches back into our DB or Massive — we send
    everything it needs in the body.
    """
    import httpx

    if df is None or df.empty:
        return []

    # Serialize OHLCV. Use ISO timestamps so the engine-api can parse
    # them deterministically across Python / Pandas versions.
    payload_ohlcv: list[dict] = []
    for ts, row in df.iterrows():
        payload_ohlcv.append({
            "ts":     pd.Timestamp(ts).isoformat(),
            "open":   float(row.get("open",   0) or 0),
            "high":   float(row.get("high",   0) or 0),
            "low":    float(row.get("low",    0) or 0),
            "close":  float(row.get("close",  0) or 0),
            "volume": float(row.get("volume", 0) or 0),
        })

    body: dict[str, Any] = {
        "ticker":    ticker,
        "timeframe": timeframe,
        "ohlcv":     payload_ohlcv,
    }
    if split_flags is not None:
        body["split_flags"] = split_flags

    url = f"{ENGINE_API_URL}/api/engines/run"
    resp = httpx.post(url, json=body, timeout=ENGINE_API_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data.get("bars", [])


__all__ = ["run_engines", "is_http_mode", "ENGINE_API_URL"]

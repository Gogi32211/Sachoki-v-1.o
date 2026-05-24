"""
market_data_client.py — Phase C-3: bridge between scanner-api and market-data-api.

Mirror of engine_client.py (Phase B-2 pattern). When MARKET_DATA_API_URL
is set, calls forward to the remote market-data-api over HTTP. When unset,
falls back to the in-process market_data.py module — same surface either way.

This is the file that keeps the door open for staging rollback. Unset
MARKET_DATA_API_URL → instantly back to in-process.

Public API (mirrors scanner-api's own market_data.py):

    get_bars(symbol, tf, days, ...) -> pd.DataFrame | None
    sync_bars(symbols, tf, days, force, ...) -> dict
    get_split_flags_for_ticker(symbol) -> dict
    market_data_api_health() -> dict        — for /api/debug/status

Once C-3 is verified, scanner-api can stop importing `from . import
market_data` directly and route everything through this client.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable, Any

import pandas as pd

log = logging.getLogger(__name__)

MARKET_DATA_API_URL     = os.getenv("MARKET_DATA_API_URL", "").rstrip("/")
MARKET_DATA_API_TIMEOUT = float(os.getenv("MARKET_DATA_API_TIMEOUT", "20"))


# ── Mode helpers ─────────────────────────────────────────────────────────────

def is_http_mode() -> bool:
    """True iff MARKET_DATA_API_URL is set (i.e. market-data lives remote)."""
    return bool(MARKET_DATA_API_URL)


def market_data_api_health() -> dict:
    """
    Probe market-data-api /health + /version. Mirrors engine_client.
    Surfaced via scanner-api's /api/debug/status so the dashboard System
    page can show wiring status.
    """
    out: dict = {
        "market_data_api_url_configured": bool(MARKET_DATA_API_URL),
        "market_data_api_mode":           "http" if MARKET_DATA_API_URL else "in_process",
        "market_data_api_url":            MARKET_DATA_API_URL or None,
        "market_data_api_reachable":      None,
        "market_data_api_version":        None,
        "market_data_api_phase":          None,
        "market_data_api_error":          None,
    }
    if not MARKET_DATA_API_URL:
        return out
    try:
        import httpx
        h = httpx.get(f"{MARKET_DATA_API_URL}/health", timeout=5)
        if h.status_code == 200 and h.json().get("status") == "ok":
            out["market_data_api_reachable"] = True
            try:
                v = httpx.get(f"{MARKET_DATA_API_URL}/version", timeout=5).json()
                out["market_data_api_version"] = v.get("version")
                out["market_data_api_phase"]   = v.get("phase")
            except Exception:
                pass
        else:
            out["market_data_api_reachable"] = False
            out["market_data_api_error"]     = f"HTTP {h.status_code}"
    except Exception as exc:
        out["market_data_api_reachable"] = False
        out["market_data_api_error"]     = type(exc).__name__
    return out


# ── Public surface ───────────────────────────────────────────────────────────

def get_bars(
    symbol: str,
    tf: str = "1d",
    days: int = 180,
    *,
    provider: str = "massive",
    adjusted: bool = True,
    allow_fetch: bool = True,
) -> pd.DataFrame | None:
    if MARKET_DATA_API_URL:
        try:
            return _get_bars_http(symbol, tf, days, provider, adjusted, allow_fetch)
        except Exception as exc:
            log.warning("market-data-api HTTP get_bars failed for %s (%s); "
                        "falling back to in-process", symbol, type(exc).__name__)
            return _get_bars_in_process(symbol, tf, days, provider, adjusted, allow_fetch)
    return _get_bars_in_process(symbol, tf, days, provider, adjusted, allow_fetch)


def sync_bars(
    symbols: Iterable[str],
    tf: str = "1d",
    days: int = 180,
    *,
    provider: str = "massive",
    adjusted: bool = True,
    force: bool = False,
    admin_token: str | None = None,
) -> dict:
    """admin_token is REQUIRED in HTTP mode. In-process path doesn't use it."""
    if MARKET_DATA_API_URL:
        try:
            return _sync_bars_http(list(symbols), tf, days, provider, adjusted, force, admin_token)
        except Exception as exc:
            log.warning("market-data-api HTTP sync failed (%s); "
                        "falling back to in-process", type(exc).__name__)
            return _sync_bars_in_process(symbols, tf, days, provider, adjusted, force)
    return _sync_bars_in_process(symbols, tf, days, provider, adjusted, force)


def get_split_flags_for_ticker(symbol: str) -> dict:
    if MARKET_DATA_API_URL:
        try:
            return _split_flags_http(symbol)
        except Exception as exc:
            log.warning("market-data-api HTTP split-flags failed for %s (%s); "
                        "falling back to in-process", symbol, type(exc).__name__)
            return _split_flags_in_process(symbol)
    return _split_flags_in_process(symbol)


# ── In-process paths ─────────────────────────────────────────────────────────

def _get_bars_in_process(symbol, tf, days, provider, adjusted, allow_fetch):
    from . import market_data as _local
    return _local.get_bars(symbol, tf=tf, days=days,
                            provider=provider, adjusted=adjusted,
                            allow_fetch=allow_fetch)


def _sync_bars_in_process(symbols, tf, days, provider, adjusted, force):
    from . import market_data as _local
    return _local.sync_bars(symbols, tf=tf, days=days,
                             provider=provider, adjusted=adjusted, force=force)


def _split_flags_in_process(symbol):
    from . import split_universe as _local
    return _local.get_split_flags_for_ticker(symbol)


# ── HTTP paths ───────────────────────────────────────────────────────────────

def _get_bars_http(symbol, tf, days, provider, adjusted, allow_fetch):
    import httpx
    url = f"{MARKET_DATA_API_URL}/api/market-data/bars/{symbol.upper().strip()}"
    params = {
        "tf": tf, "days": days, "provider": provider,
        "adjusted": "true" if adjusted else "false",
        "allow_fetch": "true" if allow_fetch else "false",
    }
    resp = httpx.get(url, params=params, timeout=MARKET_DATA_API_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    bars = data.get("bars") or []
    if not bars:
        return None
    df = pd.DataFrame(bars)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts").sort_index()
    return df[["open", "high", "low", "close", "volume"]]


def _sync_bars_http(symbols, tf, days, provider, adjusted, force, admin_token):
    import httpx
    if not admin_token:
        # Caller forgot to pass admin_token (e.g. legacy call site). Surface
        # cleanly so they see WHY instead of a 401 from upstream.
        return {
            "ok":    False,
            "error": "admin_token required for HTTP sync to market-data-api",
        }
    url = f"{MARKET_DATA_API_URL}/api/market-data/sync"
    body = {"symbols": list(symbols), "tf": tf, "days": days, "force": force}
    headers = {"x-admin-token": admin_token}
    resp = httpx.post(url, json=body, headers=headers, timeout=MARKET_DATA_API_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _split_flags_http(symbol):
    import httpx
    url = f"{MARKET_DATA_API_URL}/api/market-data/split-flags/{symbol.upper().strip()}"
    resp = httpx.get(url, timeout=10)
    resp.raise_for_status()
    j = resp.json()
    return j.get("flags") or {}


__all__ = [
    "get_bars", "sync_bars", "get_split_flags_for_ticker",
    "is_http_mode", "market_data_api_health",
    "MARKET_DATA_API_URL",
]

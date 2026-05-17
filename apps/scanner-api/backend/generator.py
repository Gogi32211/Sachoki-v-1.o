"""
generator.py — Phase D-1: dashboard-ready view generator.

Architecture role (per docs/ARCHITECTURE_TARGET.md):
    This module is the eventual generator-api service running as a module
    inside scanner-api today. Its public surface (generate_all, get_view) is
    the same surface that will be exposed via HTTP when extracted into
    apps/generator-api/ later.

Generators:
    top_movers        — gainers / losers / stats by change_pct
    best_setups       — top candidates by ultra_score with signal summary
    sector_heat       — per-sector aggregates (count, avg score, avg change)
    dashboard_summary — meta + cross-references for the Home page

Storage:
    Table `scan_generated_views` keyed by (scan_run_id, view_type, generator_version).
    Idempotent: regenerating overwrites the row via ON CONFLICT.

Rules:
    - PURE: takes candidates as input, returns a dict. No HTTP / Massive
      / engine compute here.
    - DETERMINISTIC: same candidates → same output. Re-runnable forever.
    - Version-stamped: GENERATOR_VERSION on every payload so the dashboard
      can detect stale views after a generator change.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

log = logging.getLogger(__name__)

GENERATOR_VERSION = "d1.0"

VIEW_TYPES = ("top_movers", "best_setups", "sector_heat", "dashboard_summary")


# ── Candidate → display-row shaping ──────────────────────────────────────────

def _mover_shape(c: dict) -> dict:
    """Compact shape for top_movers gainers/losers list."""
    return {
        "symbol":      c.get("symbol") or c.get("ticker") or "",
        "price":       c.get("price"),
        "change_pct":  c.get("change_pct"),
        "ultra_score": c.get("ultra_score"),
        "turbo_score": (c.get("scores") or {}).get("turbo_score") or c.get("turbo_score"),
        "band":        c.get("band") or "",
        "sector":      c.get("sector") or "",
    }


def _setup_shape(c: dict) -> dict:
    """Compact shape for best_setups list. Surfaces signal_score + first
    T/Z signal + first GOG/setup label so the dashboard card can summarize
    the row without a full chart lookup."""
    signals = c.get("signals") or {}
    scores  = c.get("scores")  or {}
    first   = lambda fam: (signals.get(fam) or [None])[0]
    return {
        "symbol":      c.get("symbol") or c.get("ticker") or "",
        "price":       c.get("price"),
        "change_pct":  c.get("change_pct"),
        "ultra_score": c.get("ultra_score"),
        "turbo_score": scores.get("turbo_score") or c.get("turbo_score"),
        "band":        c.get("band") or "",
        "sector":      c.get("sector") or "",
        "tz_sig":      first("t") or first("z") or "",
        "gog_tier":    first("gog") or "",
        "setup":       first("setup") or "",
        "rtb_phase":   scores.get("rtb_phase") or "",
        "beta_zone":   scores.get("beta_zone") or "",
        "category":    scores.get("category") or "",
        "why":         (c.get("why_selected") or [])[:3],
    }


# ── Builders ─────────────────────────────────────────────────────────────────

def build_top_movers(candidates: list[dict], n: int = 10) -> dict:
    """Top-N gainers + Top-N losers by change_pct."""
    pool = [c for c in candidates if c.get("change_pct") is not None]
    gainers = sorted(pool, key=lambda x: x["change_pct"], reverse=True)[:n]
    losers  = sorted(pool, key=lambda x: x["change_pct"])[:n]
    with_chg    = len(pool)
    without_chg = len(candidates) - with_chg
    return {
        "gainers": [_mover_shape(c) for c in gainers],
        "losers":  [_mover_shape(c) for c in losers],
        "stats": {
            "total_candidates":    len(candidates),
            "with_change_pct":     with_chg,
            "without_change_pct":  without_chg,
        },
    }


def build_best_setups(candidates: list[dict], n: int = 25) -> dict:
    """Top-N by ultra_score, then by turbo_score, then by change_pct."""
    def _key(c: dict):
        scores = c.get("scores") or {}
        return (
            -(c.get("ultra_score")        or 0),
            -(scores.get("turbo_score")   or 0),
            -(c.get("change_pct")         or 0),
        )
    sorted_pool = sorted(candidates, key=_key)
    return {
        "setups": [_setup_shape(c) for c in sorted_pool[:n]],
        "stats": {
            "total_candidates": len(candidates),
            "scored":           sum(1 for c in candidates if c.get("ultra_score")),
        },
    }


def build_sector_heat(candidates: list[dict]) -> dict:
    """Per-sector aggregates. Hot = ultra_score >= 65 (band A or A+)."""
    bucket: dict[str, dict] = {}
    for c in candidates:
        sec = c.get("sector") or "Unknown"
        b = bucket.setdefault(sec, {
            "sector": sec, "count": 0,
            "_score_sum": 0.0, "_score_n": 0,
            "_chg_sum":   0.0, "_chg_n":   0,
            "hot_count": 0,
        })
        b["count"] += 1
        s = c.get("ultra_score")
        if s is not None:
            b["_score_sum"] += s
            b["_score_n"]   += 1
            if s >= 65:
                b["hot_count"] += 1
        ch = c.get("change_pct")
        if ch is not None:
            b["_chg_sum"] += ch
            b["_chg_n"]   += 1

    sectors: list[dict] = []
    for b in bucket.values():
        sectors.append({
            "sector":     b["sector"],
            "count":      b["count"],
            "avg_score":  round(b["_score_sum"] / b["_score_n"], 1) if b["_score_n"] else None,
            "avg_change": round(b["_chg_sum"]   / b["_chg_n"],   2) if b["_chg_n"]   else None,
            "hot_count":  b["hot_count"],
        })
    sectors.sort(key=lambda x: (-(x["hot_count"] or 0), -(x["avg_score"] or 0)))
    return {
        "sectors": sectors,
        "stats": {
            "sector_count":     len(sectors),
            "total_candidates": len(candidates),
            "hot_candidates":   sum(s["hot_count"] for s in sectors),
        },
    }


def build_dashboard_summary(
    candidates: list[dict],
    *,
    movers: dict | None = None,
    setups: dict | None = None,
    sectors: dict | None = None,
    scan_run_id: int | None = None,
) -> dict:
    """Compact meta blob the Home page can render in one call."""
    band_counts: dict[str, int] = {}
    score_buckets = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
    for c in candidates:
        b = c.get("band") or ""
        band_counts[b] = band_counts.get(b, 0) + 1
        s = c.get("ultra_score") or 0
        key = ("0-20"  if s <= 20 else
               "21-40" if s <= 40 else
               "41-60" if s <= 60 else
               "61-80" if s <= 80 else "81-100")
        score_buckets[key] += 1
    return {
        "scan_run_id":          scan_run_id,
        "total_candidates":     len(candidates),
        "band_counts":          band_counts,
        "score_buckets":        score_buckets,
        "top_3_gainers":        (movers  or build_top_movers(candidates, 3)).get("gainers", [])[:3],
        "top_3_losers":         (movers  or build_top_movers(candidates, 3)).get("losers",  [])[:3],
        "top_3_setups":         (setups  or build_best_setups(candidates, 3)).get("setups", [])[:3],
        "top_sectors":          (sectors or build_sector_heat(candidates)).get("sectors", [])[:5],
    }


def generate_all(candidates: list[dict], *, scan_run_id: int | None = None) -> dict:
    """Run all generators on a candidate list and return a dict of views."""
    movers  = build_top_movers(candidates, n=10)
    setups  = build_best_setups(candidates, n=25)
    sectors = build_sector_heat(candidates)
    summary = build_dashboard_summary(
        candidates, movers=movers, setups=setups, sectors=sectors,
        scan_run_id=scan_run_id,
    )
    return {
        "top_movers":        movers,
        "best_setups":       setups,
        "sector_heat":       sectors,
        "dashboard_summary": summary,
    }


# ── DB persistence ───────────────────────────────────────────────────────────

def save_views(scan_run_id: int, views: dict) -> int:
    """
    UPSERT each view payload into scan_generated_views. Returns rows written.
    Idempotent: same (scan_run_id, view_type, generator_version) overwrites.
    """
    if not scan_run_id:
        return 0
    from . import db as _db
    if not _db.DATABASE_URL:
        return 0
    import psycopg2.extras
    now = datetime.now(timezone.utc)
    rows = []
    for vt in VIEW_TYPES:
        payload = views.get(vt)
        if payload is None:
            continue
        rows.append((scan_run_id, vt, json.dumps(payload), GENERATOR_VERSION, now))
    if not rows:
        return 0
    with _db.get_write_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO scan_generated_views
                    (scan_run_id, view_type, payload_json, generator_version, created_at)
                VALUES %s
                ON CONFLICT (scan_run_id, view_type, generator_version) DO UPDATE SET
                    payload_json = EXCLUDED.payload_json,
                    updated_at   = NOW()
                """,
                rows,
            )
        conn.commit()
    return len(rows)


def get_view(scan_run_id: int, view_type: str) -> dict | None:
    """Read one view from DB. Returns None on miss."""
    if not scan_run_id or view_type not in VIEW_TYPES:
        return None
    from . import db as _db
    if not _db.DATABASE_URL:
        return None
    try:
        with _db.get_conn() as cur:
            cur.execute(
                """
                SELECT payload_json, generator_version, updated_at
                FROM scan_generated_views
                WHERE scan_run_id=%s AND view_type=%s
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (scan_run_id, view_type),
            )
            row = cur.fetchone()
    except Exception as exc:
        log.warning("get_view failed for %s/%s: %s", scan_run_id, view_type, exc)
        return None
    if not row:
        return None
    payload = row.get("payload_json") if isinstance(row, dict) else row[0]
    if isinstance(payload, str):
        try: payload = json.loads(payload)
        except Exception: return None
    return payload


# ── Orchestrator ─────────────────────────────────────────────────────────────

def generate_and_save(scan_run_id: int, candidates: list[dict]) -> dict:
    """Generate all views for `scan_run_id`, save to DB, return a summary."""
    if not candidates:
        return {
            "scan_run_id": scan_run_id, "views_generated": [],
            "view_count": 0, "candidate_count": 0,
            "generator_version": GENERATOR_VERSION,
            "error": "no_candidates",
        }
    views   = generate_all(candidates, scan_run_id=scan_run_id)
    written = save_views(scan_run_id, views)
    return {
        "scan_run_id":       scan_run_id,
        "views_generated":   list(VIEW_TYPES),
        "view_count":        written,
        "candidate_count":   len(candidates),
        "generator_version": GENERATOR_VERSION,
        "summary": {
            vt: _payload_brief(views[vt]) for vt in VIEW_TYPES
        },
    }


def _payload_brief(payload: dict) -> dict:
    """Compact diagnostic about a generated view payload."""
    if not isinstance(payload, dict):
        return {"size": 0}
    out = {}
    for k in ("gainers", "losers", "setups", "sectors"):
        if k in payload and isinstance(payload[k], list):
            out[f"{k}_n"] = len(payload[k])
    if "stats" in payload:
        out["stats"] = payload["stats"]
    return out


__all__ = [
    "GENERATOR_VERSION", "VIEW_TYPES",
    "build_top_movers", "build_best_setups", "build_sector_heat",
    "build_dashboard_summary",
    "generate_all", "generate_and_save",
    "save_views", "get_view",
]

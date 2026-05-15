"""
dashboard_routes.py — Trading Command Center API.

Endpoints aggregate data from scanner, ultra-scan, and sector engines
and optionally run them through the Claude AI analyst layer.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# ── Simple in-memory cache ────────────────────────────────────────────────────
_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 120  # seconds

# Per-ticker caches (sector/industry + news)
_ctx_cache:           dict[str, tuple[float, dict]] = {}
_news_cache:          dict[str, tuple[float, dict]] = {}
_news_analysis_cache: dict[str, tuple[float, dict]] = {}


def _cached(key: str, ttl: int = _CACHE_TTL):
    entry = _cache.get(key)
    if entry and time.time() - entry[0] < ttl:
        return True, entry[1]
    return False, None


def _store(key: str, value: Any):
    _cache[key] = (time.time(), value)
    return value


def _load_ultra_rows(tf: str) -> dict:
    """
    Load Ultra Scan rows for the given tf without hardcoding a universe.

    Strategy:
      1. Check in-memory cache for common universes (sp500, nasdaq, sp500+nasdaq).
      2. Fall back to DB: find the latest is_latest=1 completed run for the tf
         regardless of universe; hydrate memory cache from DB; return rows.

    Always returns a dict (never raises):
        {
          "rows":            list[dict],
          "source":          "memory" | "db" | "none",
          "universe":        str,
          "tf":              str,
          "scan_run_id":     int | None,
          "candidate_count": int,
          "error":           str | None,
        }
    """
    meta = {
        "rows": [], "source": "none", "universe": "", "tf": tf,
        "scan_run_id": None, "candidate_count": 0, "error": None,
    }

    try:
        from ultra_orchestrator import get_ultra_results
        for universe in ("sp500", "nasdaq", "sp500+nasdaq"):
            try:
                resp = get_ultra_results(universe=universe, tf=tf, nasdaq_batch="")
            except Exception:
                continue
            rows = resp.get("results", []) if isinstance(resp, dict) else []
            if rows:
                meta.update({
                    "rows":             rows,
                    "source":           "memory",
                    "universe":         universe,
                    "candidate_count":  len(rows),
                })
                return meta
    except Exception as exc:
        meta["error"] = f"memory check: {exc}"

    # DB fallback — find latest completed run for tf, any universe
    try:
        from ultra_orchestrator import get_ultra_latest_from_db, load_latest_ultra_scan_from_db, get_ultra_results
        info = get_ultra_latest_from_db(tf=tf)
        if not info.get("has_data"):
            if info.get("error"):
                meta["error"] = (meta["error"] or "") + f" db lookup: {info['error']}"
            return meta

        universe     = info.get("universe") or ""
        nasdaq_batch = info.get("nasdaq_batch") or ""
        if load_latest_ultra_scan_from_db(universe, tf, nasdaq_batch):
            resp = get_ultra_results(universe=universe, tf=tf, nasdaq_batch=nasdaq_batch)
            rows = resp.get("results", []) if isinstance(resp, dict) else []
            meta.update({
                "rows":            rows,
                "source":          "db",
                "universe":        universe,
                "scan_run_id":     info.get("scan_run_id"),
                "candidate_count": len(rows),
            })
    except Exception as exc:
        meta["error"] = (meta["error"] or "") + f" db fallback: {exc}"

    return meta


# ── Market-open helpers ───────────────────────────────────────────────────────

def _market_status() -> dict:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/New_York")
    now = datetime.now(tz)
    wd = now.weekday()

    market_open  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    pre_open     = now.replace(hour=4,  minute=0,  second=0, microsecond=0)
    after_close  = now.replace(hour=20, minute=0,  second=0, microsecond=0)

    is_weekend = wd >= 5

    if is_weekend:
        status, phase, secs_to_next = "closed", "weekend", 0
    elif now < pre_open:
        status, phase = "closed", "overnight"
        secs_to_next = int((pre_open - now).total_seconds())
    elif now < market_open:
        status, phase = "pre_market", "pre_market"
        secs_to_next = int((market_open - now).total_seconds())
    elif now <= market_close:
        status, phase = "open", "regular"
        secs_to_next = int((market_close - now).total_seconds())
    elif now <= after_close:
        status, phase = "after_hours", "after_hours"
        secs_to_next = int((after_close - now).total_seconds())
    else:
        status, phase, secs_to_next = "closed", "overnight", 0

    return {
        "status":       status,
        "phase":        phase,
        "secs_to_next": secs_to_next,
        "local_time":   now.strftime("%H:%M:%S"),
        "local_date":   now.strftime("%Y-%m-%d"),
        "day_of_week":  now.strftime("%A"),
    }


def _scanner_status() -> dict:
    try:
        from scanner import get_scan_progress, get_last_scan_time
        prog = get_scan_progress()
        last = get_last_scan_time("1d")
        return {
            "running":   prog.get("running", False),
            "done":      prog.get("done", 0),
            "total":     prog.get("total", 0),
            "found":     prog.get("found", 0),
            "last_scan": last,
        }
    except Exception as exc:
        log.warning("scanner_status error: %s", exc)
        return {"running": False, "done": 0, "total": 0, "found": 0, "last_scan": None}


def _ultra_status() -> dict:
    """Return live ultra status, falling back to DB for last_scan when memory is empty."""
    try:
        from ultra_orchestrator import get_ultra_status
        s = get_ultra_status()
        last_scan = s.get("completed_at")

        # If memory shows no completed scan, check DB
        if not last_scan:
            try:
                from ultra_orchestrator import get_ultra_latest_from_db
                db_info = get_ultra_latest_from_db()
                if db_info.get("has_data"):
                    last_scan = db_info.get("finished_at")
            except Exception:
                pass

        return {
            "running":   s.get("running", False),
            "done":      s.get("done", 0),
            "total":     s.get("total", 0),
            "last_scan": last_scan,
        }
    except Exception as exc:
        log.warning("ultra_status error: %s", exc)
        return {"running": False, "done": 0, "total": 0, "last_scan": None}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/status")
def dashboard_status():
    """Market open/closed state + scanner status."""
    return {
        "market":  _market_status(),
        "scanner": _scanner_status(),
        "ultra":   _ultra_status(),
    }


@router.get("/pulse")
def dashboard_pulse():
    """Market pulse: price/change/momentum for SPY, QQQ, IWM, VIX. Cached 90s."""
    hit, val = _cached("pulse", ttl=90)
    if hit:
        return val

    tickers = ["SPY", "QQQ", "IWM", "VIX", "DIA", "IWO"]
    results = []
    try:
        import yfinance as yf
        data = yf.download(
            tickers, period="5d", interval="1d",
            auto_adjust=True, progress=False, threads=True,
        )
        close = data["Close"] if "Close" in data else data.get("close", None)
        if close is None:
            raise ValueError("No close data")
        for t in tickers:
            if t not in close.columns:
                continue
            series = close[t].dropna()
            if len(series) < 2:
                continue
            prev  = float(series.iloc[-2])
            last  = float(series.iloc[-1])
            chg   = (last - prev) / prev * 100 if prev else 0.0
            chg5d = (last - float(series.iloc[0])) / float(series.iloc[0]) * 100 if len(series) >= 5 else chg
            results.append({
                "ticker":    t,
                "price":     round(last, 2),
                "change_1d": round(chg, 2),
                "change_5d": round(chg5d, 2),
                "trend":     "up" if chg > 0 else "down" if chg < 0 else "flat",
            })
    except Exception as exc:
        log.warning("pulse fetch error: %s", exc)

    payload = {"pulse": results, "fetched_at": datetime.now(timezone.utc).isoformat()}
    return _store("pulse", payload)


@router.get("/top50")
def dashboard_top50(
    tf: str = Query("1d"),
    limit: int = Query(50, ge=1, le=200),
):
    """Top N ultra-scan candidates ordered by ultra_score desc."""
    cache_key = f"top50_{tf}_{limit}"
    hit, val = _cached(cache_key, ttl=60)
    if hit:
        return val

    cards = []
    src_meta = _load_ultra_rows(tf)
    try:
        rows = list(src_meta.get("rows") or [])
        rows.sort(key=lambda r: float(r.get("ultra_score", 0) or 0), reverse=True)
        for r in rows[:limit]:
            score = float(r.get("ultra_score", 0) or 0)
            bucket = (
                "BUY_READY"         if score >= 80 else
                "WATCH_CLOSELY"     if score >= 65 else
                "WAIT_CONFIRMATION" if score >= 50 else
                "TOO_LATE"          if score >= 35 else
                "AVOID"
            )
            cards.append({
                "ticker":        r.get("ticker", ""),
                "ultra_score":   round(score, 1),
                "band":          r.get("ultra_score_band", ""),
                "action_bucket": bucket,
                "profile":       r.get("profile", "") or r.get("ultra_score_priority", ""),
                "last_price":    r.get("last_price"),
                "change_pct":    r.get("change_pct"),
                "volume":        r.get("volume"),
                "vol_bucket":    r.get("vol_bucket", ""),
                "abr":           r.get("abr", "") or r.get("abr_label", ""),
                "signals":       r.get("active_signals", []) or [],
                "ema_ok":        r.get("ema_ok", False),
                "bull_score":    r.get("bull_score", 0),
                "scanned_at":    r.get("scanned_at", ""),
            })
    except Exception as exc:
        log.warning("top50 error: %s", exc)

    payload = {
        "cards":   cards,
        "count":   len(cards),
        "tf":      tf,
        "source":  src_meta.get("source"),
        "universe": src_meta.get("universe"),
        "scan_run_id":     src_meta.get("scan_run_id"),
        "candidate_count": src_meta.get("candidate_count"),
        "diagnostics": {
            "latest_scan_found": src_meta.get("source") != "none",
            "error":             src_meta.get("error"),
            "reason_empty": None if cards else (
                "no_ultra_scan_in_db_or_memory" if src_meta.get("source") == "none"
                else "scan_returned_zero_candidates"
            ),
        },
    }
    # Don't cache empty results — allow next call to re-check DB/memory
    if cards:
        _store(cache_key, payload)
    return payload


@router.get("/sector-heat")
def dashboard_sector_heat():
    """Sector heatmap with hotness scoring."""
    hit, val = _cached("sector_heat", ttl=180)
    if hit:
        return val

    sectors = []
    try:
        from sector_engine import get_sector_overview, SECTORS
        overview = get_sector_overview()
        items = overview.get("sectors", []) if isinstance(overview, dict) else []
        for item in items:
            etf    = item.get("ticker", "")
            ret1d  = float(item.get("return_1d", 0) or 0)
            ret5d  = float(item.get("return_5d", 0) or 0)
            rs     = float(item.get("rs_score", 0) or 0)
            hotness = ret1d * 0.5 + ret5d * 0.3 + rs * 0.2
            sectors.append({
                "etf":       etf,
                "name":      SECTORS.get(etf, etf),
                "return_1d": round(ret1d, 2),
                "return_5d": round(ret5d, 2),
                "rs_score":  round(rs, 2),
                "hotness":   round(hotness, 2),
                "trend":     "hot" if hotness > 1 else "cold" if hotness < -1 else "neutral",
            })
        sectors.sort(key=lambda s: s["hotness"], reverse=True)
    except Exception as exc:
        log.warning("sector_heat error: %s", exc)

    payload = {"sectors": sectors}
    return _store("sector_heat", payload)


@router.get("/fresh-signals")
def dashboard_fresh_signals(limit: int = Query(30, ge=1, le=100)):
    """Most recent bullish scan results sorted by bull_score desc."""
    hit, val = _cached(f"fresh_signals_{limit}", ttl=60)
    if hit:
        return val

    signals = []
    try:
        from scanner import get_results
        rows = get_results(interval="1d", limit=limit, tab="bull")
        for r in rows:
            signals.append({
                "ticker":     r.get("ticker", ""),
                "bull_score": r.get("bull_score", 0),
                "bear_score": r.get("bear_score", 0),
                "sig_name":   r.get("sig_name", ""),
                "last_price": r.get("last_price"),
                "change_pct": r.get("change_pct"),
                "vol_bucket": r.get("vol_bucket", ""),
                "scanned_at": r.get("scanned_at", ""),
            })
    except Exception as exc:
        log.warning("fresh_signals error: %s", exc)

    payload = {"signals": signals, "count": len(signals)}
    return _store(f"fresh_signals_{limit}", payload)


@router.get("/risk-alerts")
def dashboard_risk_alerts():
    """Risk alerts from VIX spike, SPY trend, and extended movers."""
    hit, val = _cached("risk_alerts", ttl=120)
    if hit:
        return val

    alerts = []
    try:
        import yfinance as yf
        data = yf.download("SPY VIX", period="5d", interval="1d",
                           auto_adjust=True, progress=False, threads=True)
        close = data.get("Close", data)
        if "VIX" in close.columns:
            vix_series = close["VIX"].dropna()
            if len(vix_series) >= 2:
                vix_now  = float(vix_series.iloc[-1])
                vix_prev = float(vix_series.iloc[-2])
                if vix_now > 30:
                    alerts.append({"type": "vix_high", "severity": "high",
                                   "message": f"VIX elevated at {vix_now:.1f} — high fear regime"})
                elif vix_now > 20 and vix_now > vix_prev * 1.05:
                    alerts.append({"type": "vix_rising", "severity": "medium",
                                   "message": f"VIX rising to {vix_now:.1f} — watch volatility"})
        if "SPY" in close.columns:
            spy_series = close["SPY"].dropna()
            if len(spy_series) >= 5:
                spy5d = (float(spy_series.iloc[-1]) - float(spy_series.iloc[-5])) / float(spy_series.iloc[-5]) * 100
                if spy5d < -5:
                    alerts.append({"type": "spy_selloff", "severity": "high",
                                   "message": f"SPY down {abs(spy5d):.1f}% over 5 days — broad selloff"})
                elif spy5d > 5:
                    alerts.append({"type": "spy_extended", "severity": "medium",
                                   "message": f"SPY up {spy5d:.1f}% over 5 days — extended, caution on new longs"})
    except Exception as exc:
        log.warning("risk_alerts error: %s", exc)

    if not alerts:
        alerts.append({"type": "ok", "severity": "low", "message": "No major risk signals detected"})

    payload = {"alerts": alerts, "count": len(alerts)}
    return _store("risk_alerts", payload)


@router.get("/summary")
def dashboard_summary():
    """Aggregated summary cards: bull count, ultra top band, sector leader."""
    hit, val = _cached("summary", ttl=90)
    if hit:
        return val

    summary: dict[str, Any] = {}

    try:
        from scanner import get_results, get_last_scan_time
        all_rows     = get_results(interval="1d", limit=500)
        bull_rows    = [r for r in all_rows if r.get("bull_score", 0) >= 4]
        strong_rows  = [r for r in all_rows if r.get("bull_score", 0) >= 6]
        summary["scan_total"]   = len(all_rows)
        summary["bull_count"]   = len(bull_rows)
        summary["strong_count"] = len(strong_rows)
        summary["last_scan"]    = get_last_scan_time("1d")
    except Exception as exc:
        log.warning("summary scan error: %s", exc)
        summary.update({"scan_total": 0, "bull_count": 0, "strong_count": 0, "last_scan": None})

    try:
        rows     = list(_load_ultra_rows("1d").get("rows") or [])
        top_band = [r for r in rows if r.get("ultra_score_band") in ("A", "A+", "S")]
        summary["ultra_total"]    = len(rows)
        summary["ultra_top_band"] = len(top_band)
    except Exception as exc:
        log.warning("summary ultra error: %s", exc)
        summary.update({"ultra_total": 0, "ultra_top_band": 0})

    try:
        from sector_engine import get_sector_overview
        overview  = get_sector_overview()
        items     = overview.get("sectors", []) if isinstance(overview, dict) else []
        leaders   = sorted(items, key=lambda s: float(s.get("return_1d", 0) or 0), reverse=True)
        summary["sector_leader"]  = leaders[0]["ticker"] if leaders else None
        summary["sector_laggard"] = leaders[-1]["ticker"] if leaders else None
    except Exception as exc:
        log.warning("summary sector error: %s", exc)
        summary.update({"sector_leader": None, "sector_laggard": None})

    return _store("summary", summary)


# ── AI Best Setups ─────────────────────────────────────────────────────────────

_SETUP_SYSTEM = """You are a professional stock market analyst assistant.
Analyze scan data and identify the top trading setups objectively.
Always respond with valid JSON only — no markdown fences, no prose outside JSON.
Do not invent data — only use what is provided."""

_SETUP_PROMPT = """
Given the following top ultra-scan candidates (JSON), select the 5 best trading setups.
For each setup provide:
- ticker: string
- category: one of BEST_PULLBACK | BEST_BREAKOUT | BEST_EMA_RECLAIM | BEST_ABR_B_PLUS | BEST_SECTOR_LEADER | BEST_FRESH_SIGNAL | BEST_RISK_REWARD | AVOID_TOO_LATE
- action_bucket: one of BUY_READY | WATCH_CLOSELY | WAIT_CONFIRMATION | TOO_LATE | AVOID
- confidence: 1-10
- why_selected: array of 2-4 short bullet strings
- risk_flags: array of 1-3 short bullet strings
- what_to_watch_next: array of 1-3 short bullet strings

Candidate data:
{candidates_json}

Respond with JSON: {{"setups": [...]}}
"""

_VALID_BUCKETS = {"BUY_READY", "WATCH_CLOSELY", "WAIT_CONFIRMATION", "TOO_LATE", "AVOID"}
_VALID_CATS = {
    "BEST_PULLBACK", "BEST_BREAKOUT", "BEST_EMA_RECLAIM", "BEST_ABR_B_PLUS",
    "BEST_SECTOR_LEADER", "BEST_FRESH_SIGNAL", "BEST_RISK_REWARD", "AVOID_TOO_LATE",
}

_setup_cache: dict[str, tuple[float, list]] = {}
_AI_CACHE_TTL = 300  # 5 min


def _deterministic_setups(cards: list[dict]) -> list[dict]:
    ranked = sorted(cards, key=lambda c: float(c.get("ultra_score", 0) or 0), reverse=True)
    out = []
    for c in ranked[:5]:
        score   = float(c.get("ultra_score", 0) or 0)
        band    = c.get("band", "")
        abr     = c.get("abr", "")
        ema_ok  = c.get("ema_ok", False)
        low_vol = c.get("vol_bucket", "") == "LOW"

        bucket = (
            "BUY_READY"         if score >= 80 else
            "WATCH_CLOSELY"     if score >= 65 else
            "WAIT_CONFIRMATION" if score >= 50 else
            "TOO_LATE"          if score >= 35 else
            "AVOID"
        )
        cat = (
            "BEST_ABR_B_PLUS"  if abr in ("B+", "A") else
            "BEST_EMA_RECLAIM" if ema_ok              else
            "BEST_PULLBACK"
        )

        reasons = []
        if band in ("S", "A+", "A"):
            reasons.append(f"Ultra {band}-band · score {score:.0f}")
        if abr in ("B+", "A"):
            reasons.append(f"ABR {abr} — strong accumulation profile")
        if ema_ok:
            reasons.append("EMA structure confirmed")
        if score >= 80 and not reasons:
            reasons.append(f"High-conviction score {score:.0f}")
        elif not reasons:
            reasons.append(f"Top ultra score {score:.0f}")

        risks = ["Low volume — wait for confirmation" if low_vol else "Standard execution risk"]

        out.append({
            "ticker":             c["ticker"],
            "category":           cat,
            "action_bucket":      bucket,
            "confidence":         min(10, max(1, int(score / 10))),
            "ultra_score":        round(score, 1),
            "band":               band,
            "why_selected":       reasons,
            "risk_flags":         risks,
            "what_to_watch_next": ["Monitor for volume expansion", "Watch EMA50 for support"],
            "source":             "deterministic",
        })
    return out


@router.get("/best-setups")
def dashboard_best_setups(tf: str = Query("1d")):
    """AI-selected best 5 setups. Cached 5 minutes. Falls back to deterministic."""
    cache_key = f"best_setups_{tf}"
    entry = _setup_cache.get(cache_key)
    if entry and time.time() - entry[0] < _AI_CACHE_TTL:
        return {"setups": entry[1], "cached": True}

    # Fetch top candidates from memory or DB — any universe
    candidates = []
    src_meta = _load_ultra_rows(tf)
    try:
        rows = list(src_meta.get("rows") or [])
        rows.sort(key=lambda r: float(r.get("ultra_score", 0) or 0), reverse=True)
        for r in rows[:20]:
            candidates.append({
                "ticker":      r.get("ticker", ""),
                "ultra_score": float(r.get("ultra_score", 0) or 0),
                "band":        r.get("ultra_score_band", ""),
                "change_pct":  r.get("change_pct"),
                "vol_bucket":  r.get("vol_bucket", ""),
                "ema_ok":      r.get("ema_ok", False),
                "abr":         r.get("abr", "") or r.get("abr_label", ""),
            })
    except Exception as exc:
        log.warning("best_setups candidates error: %s", exc)

    if not candidates:
        # Surface the reason; do not cache.
        return {
            "setups":  [],
            "cached":  False,
            "source":  src_meta.get("source"),
            "universe": src_meta.get("universe"),
            "diagnostics": {
                "latest_scan_found": src_meta.get("source") != "none",
                "error":             src_meta.get("error"),
                "reason_empty":      "no_ultra_scan_in_db_or_memory",
            },
        }

    # Try Claude
    setups = None
    try:
        from claude_client import ask_json
        prompt = _SETUP_PROMPT.format(candidates_json=json.dumps(candidates, indent=2))
        raw = ask_json(prompt, system=_SETUP_SYSTEM, max_tokens=1200)
        if isinstance(raw, dict) and isinstance(raw.get("setups"), list):
            validated = []
            for item in raw["setups"][:5]:
                if not isinstance(item, dict) or "ticker" not in item:
                    continue
                validated.append({
                    "ticker":            str(item["ticker"]),
                    "category":          item.get("category", "BEST_PULLBACK") if item.get("category") in _VALID_CATS else "BEST_PULLBACK",
                    "action_bucket":     item.get("action_bucket", "WATCH_CLOSELY") if item.get("action_bucket") in _VALID_BUCKETS else "WATCH_CLOSELY",
                    "confidence":        max(1, min(10, int(item.get("confidence", 5)))),
                    "ultra_score":       next((c["ultra_score"] for c in candidates if c["ticker"] == item["ticker"]), None),
                    "band":              next((c["band"] for c in candidates if c["ticker"] == item["ticker"]), ""),
                    "why_selected":      [str(w)[:100] for w in (item.get("why_selected") or [])[:4]],
                    "risk_flags":        [str(r)[:80]  for r in (item.get("risk_flags") or [])[:3]],
                    "what_to_watch_next":[str(w)[:100] for w in (item.get("what_to_watch_next") or [])[:3]],
                    "source":            "claude",
                })
            if validated:
                setups = validated
    except Exception as exc:
        log.warning("Claude best_setups error: %s", exc)

    if not setups:
        setups = _deterministic_setups(candidates)

    # Don't cache empty setups — let next call regenerate from latest DB data
    if setups:
        _setup_cache[cache_key] = (time.time(), setups)
    return {
        "setups":   setups,
        "cached":   False,
        "source":   src_meta.get("source"),
        "universe": src_meta.get("universe"),
    }


# ── AI Market Brief ────────────────────────────────────────────────────────────

_BRIEF_SYSTEM = """You are a professional stock market analyst.
Generate a concise, data-driven market brief based only on the provided data.
Respond with valid JSON only. Do not invent data, news, or statistics."""

_BRIEF_PROMPT = """
Generate a market brief based on the following data:
- Bull signals (score >= 4): {bull_count}
- Strong setups (score >= 6): {strong_count}
- Ultra top band (A/A+/S): {ultra_top_band}
- SPY 1d change: {spy_1d}%
- QQQ 1d change: {qqq_1d}%
- Hot sectors: {hot_sectors}
- Active risk alerts: {risk_alerts}
- Top 5 candidates by ultra score: {top_candidates}

Return this exact JSON structure:
{{
  "market_tone": "Strongly Bullish | Mildly Bullish | Neutral | Mildly Bearish | Strongly Bearish",
  "focus_summary": "2-3 sentences summarizing today's key theme",
  "hot_sectors": ["up to 3 sector names"],
  "what_to_focus_on": ["3-5 concise bullet points"],
  "what_to_avoid": ["2-3 concise bullet points"],
  "key_risks": ["1-3 key risks"],
  "confidence": "LOW | MEDIUM | HIGH"
}}
"""

_brief_cache: dict[str, tuple[float, dict]] = {}
_BRIEF_CACHE_TTL = 300  # 5 min


def _deterministic_brief(ctx: dict) -> dict:
    spy  = ctx.get("spy_1d", 0.0)
    bull = ctx.get("bull_count", 0)
    strong = ctx.get("strong_count", 0)
    hs   = ctx.get("hot_sectors", [])
    tone = (
        "Strongly Bullish"  if spy > 1.5 else
        "Mildly Bullish"    if spy > 0.3 else
        "Strongly Bearish"  if spy < -1.5 else
        "Mildly Bearish"    if spy < -0.3 else
        "Neutral"
    )
    return {
        "market_tone":      tone,
        "focus_summary":    (
            f"Scanner shows {bull} bullish signals with {strong} strong setups. "
            + (f"Hot sectors today: {', '.join(hs[:2])}." if hs else "No clear sector rotation detected.")
        ),
        "hot_sectors":      hs,
        "what_to_focus_on": [
            f"{strong} strong setups found (bull_score ≥ 6) — prioritize these" if strong > 0
            else "Limited strong setups — wait for quality entries",
            f"Active hot sectors: {', '.join(hs)}" if hs else "No clear sector leadership",
            "Prioritize ABR B+ setups with EMA50 reclaim confirmation",
            "Use Top Candidates filter to find highest-quality entries",
        ],
        "what_to_avoid": [
            "Avoid parabolic names extended > 20% without consolidation",
            "Skip low-volume setups without sector confirmation",
        ],
        "key_risks":    ctx.get("risk_alerts", ["Monitor standard risk"]),
        "confidence":   "LOW" if bull < 5 else "MEDIUM" if bull < 20 else "HIGH",
        "source":       "deterministic",
    }


@router.get("/ai-brief")
def dashboard_ai_brief():
    """AI-generated market brief. Cached 5 minutes. Falls back to deterministic."""
    cache_key = "ai_brief"
    entry = _brief_cache.get(cache_key)
    if entry and time.time() - entry[0] < _BRIEF_CACHE_TTL:
        return {**entry[1], "cached": True}

    ctx: dict[str, Any] = {}

    # Scanner data
    try:
        from scanner import get_results
        all_rows = get_results(interval="1d", limit=500)
        ctx["bull_count"]   = len([r for r in all_rows if r.get("bull_score", 0) >= 4])
        ctx["strong_count"] = len([r for r in all_rows if r.get("bull_score", 0) >= 6])
    except Exception:
        ctx.update({"bull_count": 0, "strong_count": 0})

    # Ultra data — any universe
    try:
        rows     = list(_load_ultra_rows("1d").get("rows") or [])
        top_band = [r for r in rows if r.get("ultra_score_band") in ("A", "A+", "S")]
        ctx["ultra_top_band"] = len(top_band)
        top5 = sorted(rows, key=lambda r: float(r.get("ultra_score", 0) or 0), reverse=True)[:5]
        ctx["top_candidates"] = [
            {"ticker": r.get("ticker"), "score": float(r.get("ultra_score", 0) or 0), "band": r.get("ultra_score_band", "")}
            for r in top5
        ]
    except Exception:
        ctx.update({"ultra_top_band": 0, "top_candidates": []})

    # Market pulse
    ctx.update({"spy_1d": 0.0, "qqq_1d": 0.0})
    hit, pulse_val = _cached("pulse", ttl=90)
    if hit and pulse_val:
        for item in pulse_val.get("pulse", []):
            if item["ticker"] == "SPY": ctx["spy_1d"] = item["change_1d"]
            if item["ticker"] == "QQQ": ctx["qqq_1d"] = item["change_1d"]

    # Hot sectors
    hot_sectors: list[str] = []
    try:
        from sector_engine import get_sector_overview, SECTORS
        overview = get_sector_overview()
        items    = overview.get("sectors", []) if isinstance(overview, dict) else []
        hot      = sorted(
            [s for s in items if float(s.get("return_1d", 0) or 0) > 0.5],
            key=lambda s: float(s.get("return_1d", 0) or 0), reverse=True
        )
        hot_sectors = [SECTORS.get(s["ticker"], s["ticker"]) for s in hot[:3]]
    except Exception:
        pass
    ctx["hot_sectors"] = hot_sectors

    # Risk alerts
    hit_r, risk_val = _cached("risk_alerts", ttl=120)
    if hit_r and risk_val:
        high = [a for a in risk_val.get("alerts", []) if a.get("severity") in ("high", "critical")]
        ctx["risk_alerts"] = [a["message"] for a in high[:3]] if high else ["None"]
    else:
        ctx["risk_alerts"] = ["None"]

    # Try Claude
    brief = None
    try:
        from claude_client import ask_json
        prompt = _BRIEF_PROMPT.format(**{
            k: json.dumps(v) if isinstance(v, (list, dict)) else v
            for k, v in ctx.items()
        })
        raw = ask_json(prompt, system=_BRIEF_SYSTEM, max_tokens=700)
        if isinstance(raw, dict) and "market_tone" in raw:
            brief = {**raw, "source": "claude"}
    except Exception as exc:
        log.warning("AI brief Claude error: %s", exc)

    if not brief:
        brief = _deterministic_brief(ctx)

    _brief_cache[cache_key] = (time.time(), brief)
    return {**brief, "cached": False}


_MARKET_NEWS_TTL = 300  # 5 min

@router.get("/news")
def dashboard_news(limit: int = Query(8, ge=1, le=30)):
    """
    Market news from Massive API for top Ultra Scan candidates.
    Deduplicates headlines across tickers and returns the freshest items.
    """
    hit, val = _cached("market_news", ttl=_MARKET_NEWS_TTL)
    if hit:
        return val

    # Check Massive configured first — fast path
    if not _massive_news_configured():
        payload = {
            "items":              [],
            "source":             "massive",
            "provider_configured": False,
            "message":            "Massive API is not configured. Set MASSIVE_API_KEY to enable news.",
        }
        return payload  # do NOT cache unconfigured response

    # Get top candidate tickers from latest scan (memory or DB, any universe)
    tickers: list[str] = []
    try:
        src_meta = _load_ultra_rows("1d")
        rows = list(src_meta.get("rows") or [])
        rows_sorted = sorted(rows, key=lambda r: float(r.get("ultra_score", 0) or 0), reverse=True)
        tickers = [r["ticker"] for r in rows_sorted[:6] if r.get("ticker")]
    except Exception as exc:
        log.warning("dashboard_news: could not get candidates: %s", exc)

    if not tickers:
        payload = {
            "items":               [],
            "source":              "massive",
            "provider_configured": True,
            "message":             "No Ultra Scan candidates available. Run Ultra Scan first.",
        }
        return payload  # do NOT cache empty-candidates response

    # Fetch news for each ticker, deduplicate by URL
    seen_urls: set[str] = set()
    all_items: list[dict] = []

    for ticker in tickers[:5]:
        try:
            configured, items, _ = _fetch_massive_news(ticker, limit=4)
            for item in items:
                url = item.get("url", "")
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                item["for_ticker"] = ticker
                all_items.append(item)
        except Exception as exc:
            log.warning("dashboard_news: fetch failed for %s: %s", ticker, exc)

    # Sort by published_at descending
    def _pub_sort_key(item: dict) -> str:
        return item.get("published_at") or ""

    all_items.sort(key=_pub_sort_key, reverse=True)
    top_items = all_items[:limit]

    if not top_items:
        payload = {
            "items":               [],
            "source":              "massive",
            "provider_configured": True,
            "message":             f"No recent Massive news found for current candidates ({', '.join(tickers[:3])}).",
        }
        return _store("market_news", payload)

    payload = {
        "items":               top_items,
        "source":              "massive",
        "provider_configured": True,
        "count":               len(top_items),
        "tickers_sampled":     tickers[:5],
        "message":             "",
    }
    return _store("market_news", payload)


@router.get("/ticker-context/{symbol}")
def dashboard_ticker_context(symbol: str):
    """Ticker metadata: sector, industry, company, theme, upcoming events. Cached 6h."""
    symbol = symbol.upper().strip()
    _CTX_TTL = 21600
    entry = _ctx_cache.get(symbol)
    if entry and time.time() - entry[0] < _CTX_TTL:
        return entry[1]

    data: dict = {
        "symbol":   symbol,
        "company":  None,
        "sector":   None,
        "industry": None,
        "theme":    None,
        "events":   [],
    }

    try:
        import yfinance as yf
        tkr  = yf.Ticker(symbol)
        info: dict = {}
        try:
            info = tkr.info or {}
        except Exception:
            pass

        data["company"]  = info.get("shortName") or info.get("longName")
        data["sector"]   = info.get("sector")
        data["industry"] = info.get("industry")

        ind = (data["industry"] or "").lower()
        co  = (data["company"]  or "").lower()
        if any(x in ind for x in ["biotech", "drug", "pharmaceutical", "biologic"]):
            data["theme"] = "Biotech"
        elif "semiconductor" in ind:
            data["theme"] = "Semis"
        elif any(x in ind for x in ["software", "artificial intelligence", "cloud"]):
            data["theme"] = "AI/SW"
        elif any(x in co for x in ["bitcoin", "crypto", "blockchain"]):
            data["theme"] = "Crypto"
        elif any(x in ind for x in ["gold", "silver", "mining"]):
            data["theme"] = "Metals"

        # Upcoming earnings
        try:
            cal = tkr.calendar
            if hasattr(cal, "to_dict"):
                cal = cal.to_dict()
            if isinstance(cal, dict):
                ed_raw = cal.get("Earnings Date")
                eds = []
                if ed_raw is not None:
                    if hasattr(ed_raw, "__iter__") and not isinstance(ed_raw, str):
                        eds = list(ed_raw)[:1]
                    else:
                        eds = [ed_raw]
                for ed in eds:
                    try:
                        from datetime import date as _date, datetime as _dt
                        if hasattr(ed, "date") and callable(ed.date):
                            ed = ed.date()
                        now_utc = datetime.now(timezone.utc).date()
                        if hasattr(ed, "year"):
                            delta = (ed - now_utc).days if hasattr(ed, "month") else 0
                        else:
                            continue
                        urgency = (
                            "TODAY"     if delta == 0 else
                            "TOMORROW"  if delta == 1 else
                            "THIS_WEEK" if delta <= 7 else
                            "NEXT_7D"   if delta <= 14 else
                            "UPCOMING"
                        )
                        if -14 <= delta <= 30:
                            data["events"].append({
                                "event_type": "EARNINGS",
                                "event_date": str(ed),
                                "urgency":    urgency,
                                "risk_level": "MEDIUM",
                                "label": (
                                    "Earnings Today"       if delta == 0 else
                                    "Earnings Tomorrow"    if delta == 1 else
                                    f"Earnings in {delta}D" if delta > 0 else
                                    f"Earnings {abs(delta)}D ago"
                                ),
                            })
                    except Exception:
                        pass
        except Exception:
            pass

    except Exception as exc:
        log.warning("ticker_context error %s: %s", symbol, exc)

    _ctx_cache[symbol] = (time.time(), data)
    return data


# ── Ticker News via Massive + AI Analysis ────────────────────────────────────
# Primary news provider: Massive (Polygon-compatible REST API).
# yfinance is NOT used here.

_NEWS_TTL     = 1800   # 30 min for raw Massive news
_ANALYSIS_TTL = 86400  # 24 h for Haiku analysis (keyed by content hash)

_VALID_SENTIMENTS = {"BULLISH","MILDLY_BULLISH","NEUTRAL","MILDLY_BEARISH","BEARISH","RISKY","UNKNOWN"}
_VALID_IMPACTS    = {"SUPPORTS_SETUP","WEAKENS_SETUP","RISK_ONLY","NO_CLEAR_IMPACT","UNKNOWN"}

_NEWS_SYSTEM = (
    "You are a financial news analyst. Analyze the provided Massive news items for a stock ticker. "
    "Use ONLY the data supplied — headline, description, publisher, sentiment fields. "
    "Do not invent, assume, or hallucinate any information. "
    "Respond with valid JSON only, no prose outside JSON."
)

_NEWS_PROMPT = """\
Analyze these recent Massive news items for {symbol}:

{news_items_json}

Return this exact JSON:
{{
  "sentiment": "BULLISH|MILDLY_BULLISH|NEUTRAL|MILDLY_BEARISH|BEARISH|RISKY|UNKNOWN",
  "catalyst_type": "EARNINGS|FDA|ANALYST_UPGRADE|ANALYST_DOWNGRADE|MERGER|OFFERING|INSIDER_BUY|INSIDER_SELL|CONTRACT|SECTOR_NEWS|GENERAL|UNKNOWN",
  "relevance": "HIGH|MEDIUM|LOW",
  "summary": "1-2 sentence summary of what matters for this stock's setup",
  "why_it_matters": ["up to 2 short trading-relevant points"],
  "risks": ["up to 2 short risk points"],
  "setup_impact": "SUPPORTS_SETUP|WEAKENS_SETUP|RISK_ONLY|NO_CLEAR_IMPACT|UNKNOWN"
}}
If insufficient data, use UNKNOWN values. Never invent information not in the provided items.
"""


def _massive_news_configured() -> bool:
    """True if Massive API key is available."""
    from data_polygon import polygon_available
    return polygon_available()


def _fetch_massive_news(symbol: str, limit: int = 10) -> tuple[bool, list[dict], str | None]:
    """
    Fetch news from Massive /v2/reference/news endpoint.
    Returns: (configured, items, error_message_or_None)
    Never raises — callers handle the (False, [], msg) case.
    """
    import os, requests as _requests
    from data_polygon import _BASE

    key = (os.environ.get("MASSIVE_API_KEY") or
           os.environ.get("POLYGON_API_KEY") or "")
    if not key:
        return False, [], "Massive API is not configured."

    url = f"{_BASE}/v2/reference/news"
    params = {"ticker": symbol, "limit": limit, "order": "desc", "apiKey": key}

    try:
        r = _requests.get(url, params=params, timeout=(5, 10))
        if r.status_code == 403:
            log.warning("massive_news 403 for %s", symbol)
            return True, [], "Massive API access denied (check API key plan)."
        if r.status_code == 429:
            log.warning("massive_news 429 for %s", symbol)
            return True, [], "Massive API rate limit hit; try again shortly."
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        log.warning("massive_news fetch error %s: %s", symbol, exc)
        return True, [], f"Massive API error: {exc}"

    results = data.get("results") or []
    items: list[dict] = []
    for n in results:
        publisher = n.get("publisher") or {}
        if isinstance(publisher, str):
            publisher = {"name": publisher}

        # Extract ticker-specific insights (sentiment + reasoning)
        sentiment         = ""
        sentiment_reasoning = ""
        for ins in (n.get("insights") or []):
            if (ins.get("ticker") or "").upper() == symbol:
                sentiment           = ins.get("sentiment", "")
                sentiment_reasoning = ins.get("sentiment_reasoning", "")
                break

        items.append({
            "headline":            n.get("title", ""),
            "summary":             n.get("description", ""),
            "source":              publisher.get("name", ""),
            "publisher":           publisher.get("name", ""),
            "url":                 n.get("article_url", ""),
            "published_at":        n.get("published_utc", ""),
            "symbols":             n.get("tickers", []),
            "sentiment":           sentiment,
            "sentiment_reasoning": sentiment_reasoning,
            "category":            "",
            "image_url":           n.get("image_url", ""),
        })

    return True, items, None


def _news_hash(items: list[dict]) -> str:
    import hashlib
    hl = "|".join(i.get("headline", "") for i in items if i.get("headline"))
    return hashlib.md5(hl.encode()).hexdigest()[:12] if hl else ""


# ── Event classification from Massive news headlines ─────────────────────────

_EVENT_PATTERNS: list[tuple[str, list[str], str]] = [
    # (event_type, keywords, risk_level)
    ("REVERSE_SPLIT",     ["reverse split", "reverse stock split", "1-for-", "reverse-split"],           "HIGH"),
    ("OFFERING",          ["offering", "dilut", "secondary offering", "at-the-market", " atm offering"], "HIGH"),
    ("HALT",              ["trading halt", "halted", "nasdaq halt", "nyse halt"],                         "HIGH"),
    ("FDA",               ["fda", "food and drug", "pdufa", "nda ", "bla ", "clinical trial",
                           "fda approval", "drug approval"],                                              "MEDIUM"),
    ("EARNINGS",          ["earnings", "quarterly results", " q1 ", " q2 ", " q3 ", " q4 ",
                           "eps beat", "eps miss", "revenue beat", "revenue miss",
                           "profit report", "results beat"],                                              "MEDIUM"),
    ("MERGER",            ["merger", " acquisition", "acquire", "takeover", "buyout", "deal agreed"],    "MEDIUM"),
    ("ANALYST_UPGRADE",   ["upgrade", "upgraded to", "raises price target", "raised target",
                           "outperform", "buy rating", "initiates with buy"],                             "LOW"),
    ("ANALYST_DOWNGRADE", ["downgrade", "downgraded", "cuts target", "underperform",
                           "sell rating", "reduces target"],                                              "MEDIUM"),
    ("IPO",               ["ipo", "initial public offering", "went public", "began trading"],             "LOW"),
    ("INSIDER",           ["insider buy", "insider sell", "director buys", "ceo buys",
                           "ceo sells", "insider purchases", "insider selling"],                          "LOW"),
    ("SPLIT",             ["stock split", "share split", "2-for-1", "3-for-1", "forward split"],         "LOW"),
    ("DIVIDEND",          ["dividend", "special dividend", "quarterly dividend", "distribution"],         "LOW"),
    ("SEC_FILING",        ["sec filing", "8-k", "10-q", "10-k", "form s-1", "proxy statement"],          "LOW"),
]

_EVENT_LABELS = {
    "REVERSE_SPLIT":     "Reverse split risk",
    "OFFERING":          "Offering risk",
    "HALT":              "Trading halt",
    "FDA":               "FDA catalyst",
    "EARNINGS":          "Earnings news",
    "MERGER":            "M&A news",
    "ANALYST_UPGRADE":   "Analyst upgrade",
    "ANALYST_DOWNGRADE": "Analyst downgrade",
    "IPO":               "IPO news",
    "INSIDER":           "Insider activity",
    "SPLIT":             "Stock split",
    "DIVIDEND":          "Dividend news",
    "SEC_FILING":        "SEC filing",
    "NEWS_SPIKE":        "News spike",
}


def _classify_news_events(items: list[dict]) -> list[dict]:
    """
    Rule-based event classification from Massive news headlines + summaries.
    Returns a list of event dicts (same shape as ctx events).
    Only classifies events actually present in the provided data.
    """
    events: list[dict] = []
    seen: set[str] = set()

    for item in items:
        text = (
            (item.get("headline") or "") + " " + (item.get("summary") or "")
        ).lower()

        pub = item.get("published_at", "")
        try:
            pub_dt   = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            delta_h  = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
            urgency  = "RECENT_24H" if delta_h <= 24 else "RECENT_7D"
        except Exception:
            urgency = "UNKNOWN"

        for event_type, keywords, risk_level in _EVENT_PATTERNS:
            if event_type in seen:
                continue
            if any(kw in text for kw in keywords):
                events.append({
                    "event_type": event_type,
                    "urgency":    urgency,
                    "risk_level": risk_level,
                    "label":      _EVENT_LABELS.get(event_type, event_type),
                    "source":     "massive_news",
                })
                seen.add(event_type)

    # NEWS_SPIKE when many items clustered
    if len(items) >= 4 and "NEWS_SPIKE" not in seen:
        events.append({
            "event_type": "NEWS_SPIKE",
            "urgency":    "RECENT_24H",
            "risk_level": "LOW",
            "label":      f"News spike ({len(items)} items)",
            "source":     "massive_news",
        })

    return events[:6]


@router.get("/ticker-news/{symbol}")
def dashboard_ticker_news(symbol: str):
    """
    Recent news for a ticker sourced from Massive API.
    Returns raw items + cached AI analysis (if available).
    Handles: configured+news, configured+no-news, not-configured.
    """
    symbol = symbol.upper().strip()

    entry = _news_cache.get(symbol)
    if entry and time.time() - entry[0] < _NEWS_TTL:
        return entry[1]

    configured, items, err_msg = _fetch_massive_news(symbol)

    if not configured:
        # Do NOT cache — key may be set later
        return {
            "symbol":              symbol,
            "provider":            "massive",
            "provider_configured": False,
            "news_count":          0,
            "items":               [],
            "news_events":         [],
            "message":             "Massive API is not configured.",
            "fetched_at":          datetime.now(timezone.utc).isoformat(),
        }

    nh         = _news_hash(items)
    ai_summary = None

    if nh:
        ana = _news_analysis_cache.get(f"massive:{nh}")
        if ana and time.time() - ana[0] < _ANALYSIS_TTL:
            ai_summary = ana[1]

    news_events = _classify_news_events(items)

    result: dict = {
        "symbol":              symbol,
        "provider":            "massive",
        "provider_configured": True,
        "news_count":          len(items),
        "latest_news_at":      items[0]["published_at"] if items else None,
        "ai_summary":          ai_summary,
        "news_hash":           nh,
        "news_events":         news_events,
        "items":               items,
        "fetched_at":          datetime.now(timezone.utc).isoformat(),
    }
    if not items:
        result["message"] = err_msg or "No recent Massive news found for this ticker."

    _news_cache[symbol] = (time.time(), result)
    return result


@router.post("/ticker-news/{symbol}/analyze")
def dashboard_ticker_news_analyze(symbol: str):
    """
    Run Claude Haiku analysis on Massive news for a ticker.
    Haiku only receives Massive-provided data — never invents news.
    Cached 24h per news content hash.
    """
    symbol = symbol.upper().strip()

    news_result = dashboard_ticker_news(symbol)

    # If Massive not configured, return without calling Haiku
    if not news_result.get("provider_configured"):
        return news_result

    items = news_result.get("items", [])
    nh    = news_result.get("news_hash", "")

    # No news → skip Haiku, clean fallback
    if not items:
        return {
            **news_result,
            "ai_summary": None,
            "ai_unavailable_reason": "NO_NEWS_DATA",
        }

    # Return cached analysis if fresh
    if nh:
        ana = _news_analysis_cache.get(f"massive:{nh}")
        if ana and time.time() - ana[0] < _ANALYSIS_TTL:
            return {**news_result, "ai_summary": ana[1]}

    # Build Haiku input: only Massive fields, no invented data
    haiku_items = []
    for it in items[:8]:
        entry: dict = {"headline": it["headline"]}
        if it.get("summary"):
            entry["description"] = it["summary"]
        if it.get("publisher"):
            entry["publisher"] = it["publisher"]
        if it.get("published_at"):
            entry["published_at"] = it["published_at"]
        if it.get("sentiment"):
            entry["massive_sentiment"] = it["sentiment"]
        if it.get("sentiment_reasoning"):
            entry["massive_sentiment_reasoning"] = it["sentiment_reasoning"]
        if it.get("symbols"):
            entry["related_tickers"] = it["symbols"]
        haiku_items.append(entry)

    ai_summary = None
    try:
        from claude_client import ask_json
        raw = ask_json(
            _NEWS_PROMPT.format(
                symbol=symbol,
                news_items_json=json.dumps(haiku_items, indent=2),
            ),
            system=_NEWS_SYSTEM,
            max_tokens=600,
        )
        if isinstance(raw, dict) and "sentiment" in raw:
            ai_summary = {
                "sentiment":      raw["sentiment"]     if raw["sentiment"]     in _VALID_SENTIMENTS else "UNKNOWN",
                "catalyst_type":  raw.get("catalyst_type", "UNKNOWN"),
                "relevance":      raw.get("relevance",     "MEDIUM"),
                "summary":        str(raw.get("summary", ""))[:300],
                "why_it_matters": [str(w)[:120] for w in (raw.get("why_it_matters") or [])[:2]],
                "risks":          [str(r)[:120] for r in (raw.get("risks")           or [])[:2]],
                "setup_impact":   raw["setup_impact"] if raw.get("setup_impact") in _VALID_IMPACTS else "UNKNOWN",
            }
    except Exception as exc:
        log.warning("news analyze error %s: %s", symbol, exc)

    # Cache successful analysis; leave ai_summary=None on failure (UI shows raw news)
    if ai_summary and nh:
        _news_analysis_cache[f"massive:{nh}"] = (time.time(), ai_summary)

    result = {**news_result, "ai_summary": ai_summary}
    if ai_summary:
        _news_cache[symbol] = (time.time(), result)
    return result


@router.get("/watchlist")
def dashboard_watchlist(tickers: str = Query("")):
    """
    Watchlist snapshot: current T/Z signal status for the provided tickers.
    Accepts comma-separated ticker symbols as a query param.
    """
    if not tickers.strip():
        return {"items": []}

    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()][:50]
    items = []

    try:
        from scanner import get_results
        all_rows   = get_results(interval="1d", limit=1000)
        result_map = {r["ticker"]: r for r in all_rows}

        for ticker in ticker_list:
            r     = result_map.get(ticker)
            score = r.get("bull_score", 0) if r else 0
            chg   = (r.get("change_pct") or 0.0) if r else None

            status = (
                "improving"  if score >= 6 else
                "valid"      if score >= 4 else
                "weakening"  if score >= 2 else
                "review"
            )
            bucket = (
                "BUY_READY"         if score >= 7 else
                "WATCH_CLOSELY"     if score >= 5 else
                "WAIT_CONFIRMATION" if score >= 3 else
                "AVOID"
            )
            items.append({
                "ticker":       ticker,
                "status":       status,
                "bull_score":   score,
                "change_pct":   chg,
                "sig_name":     r.get("sig_name", "") if r else "",
                "action_bucket": bucket,
            })
    except Exception as exc:
        log.warning("dashboard watchlist error: %s", exc)
        items = [
            {"ticker": t, "status": "review", "bull_score": 0,
             "change_pct": None, "sig_name": "", "action_bucket": "WAIT_CONFIRMATION"}
            for t in ticker_list
        ]

    return {"items": items}


# ── Bootstrap — single endpoint that hydrates the entire Dashboard ────────────

@router.get("/bootstrap")
def dashboard_bootstrap(
    tf:    str  = Query("1d"),
    force: bool = Query(False),
):
    """
    Single endpoint that returns everything the Dashboard needs on load/refresh.
    Reads from DB for persistence across restarts; falls back gracefully.

    Pass force=true to clear ultra-related dashboard caches before fetching
    (used by the Refresh button).

    dashboard_state values:
      NO_SCAN      — no Ultra scan data found anywhere
      SCAN_READY   — latest completed scan data available
      SCAN_RUNNING — scan is currently running
      SCAN_STALE   — data older than 48h
      ERROR        — internal failure
    """
    from datetime import datetime, timezone

    # Force-refresh: drop ultra-related cache entries so we re-read DB/memory
    if force:
        for key in list(_cache.keys()):
            if any(k in key for k in ("top50", "summary", "fresh_signals", "market_news", "top_movers")):
                _cache.pop(key, None)
        _setup_cache.clear()
        _brief_cache.clear()

    result: dict[str, Any] = {
        "dashboard_state": "NO_SCAN",
        "latest_scan":     {"has_data": False},
        "summary":         {},
        "top_candidates":  [],
        "best_setups":     [],
        "sectors":         [],
        "risk_alerts":     [],
        "fresh_signals":   [],
        "market_pulse":    {},
        "news":            {"provider": "massive", "provider_configured": False, "items": [], "message": ""},
        "data_health":     {
            "ultra":   {"status": "idle", "last_run_at": None, "source": "memory"},
            "tz":      {"status": "idle", "last_run_at": None},
            "massive": {"configured": False, "status": "not_configured"},
        },
        "diagnostics":     {"errors": [], "force": force},
    }

    # ── Latest scan info (DB-first, any universe) ────────────────────────────
    db_info: dict = {"has_data": False}
    is_running = False
    try:
        from ultra_orchestrator import get_ultra_latest_from_db, get_ultra_status
        db_info = get_ultra_latest_from_db(tf=tf)
        mem_status = get_ultra_status()

        result["latest_scan"] = db_info
        is_running = mem_status.get("running", False)

        if is_running:
            result["dashboard_state"] = "SCAN_RUNNING"
        elif db_info.get("has_data"):
            age_s = db_info.get("data_age_seconds") or 0
            result["dashboard_state"] = "SCAN_STALE" if age_s > 172800 else "SCAN_READY"
        else:
            result["dashboard_state"] = "NO_SCAN"

        last_at = db_info.get("finished_at") if db_info.get("has_data") else None
        result["data_health"]["ultra"] = {
            "status":      "running"   if is_running else
                           "completed" if db_info.get("has_data") else "idle",
            "last_run_at":      last_at,
            "total_candidates": db_info.get("total_candidates", 0),
            "source":           "db" if db_info.get("has_data") else "memory",
        }
    except Exception as exc:
        log.warning("bootstrap: ultra info error: %s", exc)
        result["diagnostics"]["errors"].append(f"latest_scan: {exc}")

    # ── Top candidates (any universe via _load_ultra_rows) ───────────────────
    try:
        src_meta = _load_ultra_rows(tf)
        rows = list(src_meta.get("rows") or [])
        rows.sort(key=lambda r: float(r.get("ultra_score", 0) or 0), reverse=True)
        result["data_health"]["ultra"]["source"] = src_meta.get("source") or result["data_health"]["ultra"].get("source")
        cards = []
        for r in rows[:50]:
            score = float(r.get("ultra_score", 0) or 0)
            bucket = (
                "BUY_READY"         if score >= 80 else
                "WATCH_CLOSELY"     if score >= 65 else
                "WAIT_CONFIRMATION" if score >= 50 else
                "TOO_LATE"          if score >= 35 else
                "AVOID"
            )
            cards.append({
                "ticker":        r.get("ticker", ""),
                "ultra_score":   round(score, 1),
                "band":          r.get("ultra_score_band", ""),
                "action_bucket": bucket,
                "profile":       r.get("profile", "") or r.get("ultra_score_priority", ""),
                "last_price":    r.get("last_price"),
                "change_pct":    r.get("change_pct"),
                "volume":        r.get("volume"),
                "vol_bucket":    r.get("vol_bucket", ""),
                "abr":           r.get("abr", "") or r.get("abr_label", ""),
                "signals":       r.get("active_signals", []) or [],
                "ema_ok":        r.get("ema_ok", False),
                "bull_score":    r.get("bull_score", 0),
                "scanned_at":    r.get("scanned_at", ""),
            })
        result["top_candidates"] = cards

        # Best setups (deterministic from DB candidates if present)
        if cards:
            result["best_setups"] = _deterministic_setups([
                {
                    "ticker":      c["ticker"],
                    "ultra_score": c["ultra_score"],
                    "band":        c["band"],
                    "abr":         c.get("abr", ""),
                    "ema_ok":      c.get("ema_ok", False),
                    "vol_bucket":  c.get("vol_bucket", ""),
                }
                for c in cards
            ])

            # If memory had rows but DB lookup returned nothing, still surface
            # SCAN_READY (memory hit). Don't downgrade SCAN_RUNNING.
            if not is_running and not db_info.get("has_data"):
                result["dashboard_state"] = "SCAN_READY"
                result["data_health"]["ultra"]["status"] = "completed"
                result["data_health"]["ultra"]["source"] = src_meta.get("source") or "memory"
                result["data_health"]["ultra"]["total_candidates"] = len(cards)
    except Exception as exc:
        log.warning("bootstrap: candidates error: %s", exc)
        result["diagnostics"]["errors"].append(f"top_candidates: {exc}")

    # ── Summary ──────────────────────────────────────────────────────────────
    try:
        from scanner import get_results, get_last_scan_time
        all_rows = get_results(interval="1d", limit=500)
        bull_rows   = [r for r in all_rows if r.get("bull_score", 0) >= 4]
        strong_rows = [r for r in all_rows if r.get("bull_score", 0) >= 6]
        top_band    = [c for c in result["top_candidates"] if c.get("band") in ("A", "A+", "S")]
        result["summary"] = {
            "scan_total":     len(all_rows),
            "bull_count":     len(bull_rows),
            "strong_count":   len(strong_rows),
            "last_scan":      get_last_scan_time("1d"),
            "ultra_total":    len(result["top_candidates"]),
            "ultra_top_band": len(top_band),
        }
    except Exception as exc:
        log.warning("bootstrap: summary error: %s", exc)

    # ── Sectors ──────────────────────────────────────────────────────────────
    try:
        from sector_engine import get_sector_overview, SECTORS
        overview = get_sector_overview()
        items = overview.get("sectors", []) if isinstance(overview, dict) else []
        sectors = []
        for item in items:
            etf   = item.get("ticker", "")
            ret1d = float(item.get("return_1d", 0) or 0)
            ret5d = float(item.get("return_5d", 0) or 0)
            rs    = float(item.get("rs_score", 0) or 0)
            hotness = ret1d * 0.5 + ret5d * 0.3 + rs * 0.2
            sectors.append({
                "etf":       etf,
                "name":      SECTORS.get(etf, etf),
                "return_1d": round(ret1d, 2),
                "return_5d": round(ret5d, 2),
                "rs_score":  round(rs, 2),
                "hotness":   round(hotness, 2),
                "trend":     "hot" if hotness > 1 else "cold" if hotness < -1 else "neutral",
            })
        sectors.sort(key=lambda s: s["hotness"], reverse=True)
        result["sectors"] = sectors
    except Exception as exc:
        log.warning("bootstrap: sectors error: %s", exc)

    # ── Risk alerts ──────────────────────────────────────────────────────────
    try:
        hit, risk_val = _cached("risk_alerts", ttl=120)
        if hit and risk_val:
            result["risk_alerts"] = risk_val.get("alerts", [])
    except Exception:
        pass

    # ── Fresh signals ────────────────────────────────────────────────────────
    try:
        from scanner import get_results
        fresh_rows = get_results(interval="1d", limit=20, tab="bull")
        result["fresh_signals"] = [
            {
                "ticker":     r.get("ticker", ""),
                "bull_score": r.get("bull_score", 0),
                "sig_name":   r.get("sig_name", ""),
                "last_price": r.get("last_price"),
                "change_pct": r.get("change_pct"),
                "vol_bucket": r.get("vol_bucket", ""),
                "scanned_at": r.get("scanned_at", ""),
            }
            for r in fresh_rows
        ]
    except Exception as exc:
        log.warning("bootstrap: fresh signals error: %s", exc)

    # ── Market pulse (cached) ────────────────────────────────────────────────
    try:
        hit, pulse_val = _cached("pulse", ttl=90)
        if hit and pulse_val:
            result["market_pulse"] = pulse_val
    except Exception:
        pass

    # ── News (Massive) ───────────────────────────────────────────────────────
    massive_ok = _massive_news_configured()
    result["data_health"]["massive"] = {
        "configured": massive_ok,
        "status":     "ready" if massive_ok else "not_configured",
    }
    if massive_ok and result["top_candidates"]:
        try:
            hit, news_val = _cached("market_news", ttl=_MARKET_NEWS_TTL)
            if hit and news_val:
                result["news"] = {
                    "provider":            "massive",
                    "provider_configured": True,
                    "items":               news_val.get("items", []),
                    "message":             news_val.get("message", ""),
                }
            else:
                # Fetch news for top 3 tickers inline (fast)
                top_tickers = [c["ticker"] for c in result["top_candidates"][:3]]
                seen: set[str] = set()
                news_items: list[dict] = []
                for t in top_tickers:
                    _, items, _ = _fetch_massive_news(t, limit=3)
                    for item in items:
                        url = item.get("url", "")
                        if url and url in seen:
                            continue
                        if url:
                            seen.add(url)
                        item["for_ticker"] = t
                        news_items.append(item)
                news_items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
                result["news"] = {
                    "provider":            "massive",
                    "provider_configured": True,
                    "items":               news_items[:8],
                    "message":             "" if news_items else
                                           f"No recent Massive news for {', '.join(top_tickers)}.",
                }
        except Exception as exc:
            log.warning("bootstrap: news error: %s", exc)
            result["news"] = {"provider": "massive", "provider_configured": True,
                              "items": [], "message": f"News fetch error: {exc}"}
    elif not massive_ok:
        result["news"]["message"] = "Massive API is not configured. Set MASSIVE_API_KEY."

    # ── T/Z data health ──────────────────────────────────────────────────────
    try:
        scan_last = result.get("summary", {}).get("last_scan")
        result["data_health"]["tz"] = {
            "status":      "ok" if scan_last else "idle",
            "last_run_at": scan_last,
        }
    except Exception:
        pass

    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return result


# ── Top Movers ────────────────────────────────────────────────────────────────

_TOP_MOVERS_TTL = 120  # 2 min

def _fetch_snapshot_batch(symbols: list[str]) -> dict[str, dict]:
    """
    Fetch Massive /v2/snapshot for up to 300 symbols.
    Returns dict keyed by uppercase ticker symbol.
    Never raises — returns {} on failure.
    """
    import os
    import requests as _requests
    from data_polygon import _BASE

    key = (os.environ.get("MASSIVE_API_KEY") or os.environ.get("POLYGON_API_KEY") or "")
    if not key or not symbols:
        return {}

    result: dict[str, dict] = {}
    for i in range(0, len(symbols), 100):
        batch = symbols[i : i + 100]
        try:
            r = _requests.get(
                f"{_BASE}/v2/snapshot/locale/us/markets/stocks/tickers",
                params={"tickers": ",".join(batch), "apiKey": key},
                timeout=(5, 15),
            )
            if r.status_code == 200:
                for item in r.json().get("tickers", []):
                    sym = (item.get("ticker") or "").upper()
                    if sym:
                        result[sym] = item
        except Exception as _exc:
            log.warning("top_movers: snapshot batch error: %s", _exc)

    return result


def _build_mover_list(watchlist_csv: str) -> list[str]:
    """Return deduplicated tracked symbol list: top Ultra candidates + watchlist."""
    symbols: set[str] = set()

    try:
        rows = sorted(
            _load_ultra_rows("1d").get("rows") or [],
            key=lambda r: float(r.get("ultra_score", 0) or 0),
            reverse=True,
        )
        for r in rows[:200]:
            t = (r.get("ticker") or "").upper().strip()
            if t:
                symbols.add(t)
    except Exception as exc:
        log.warning("top_movers: ultra list error: %s", exc)

    if watchlist_csv:
        for t in watchlist_csv.split(","):
            t = t.strip().upper()
            if t:
                symbols.add(t)

    return sorted(symbols)


def _score_bucket(score: float) -> str:
    if score >= 80: return "BUY_READY"
    if score >= 65: return "WATCH_CLOSELY"
    if score >= 50: return "WAIT_CONFIRMATION"
    if score >= 35: return "TOO_LATE"
    return "AVOID"


@router.get("/top-movers")
def dashboard_top_movers(
    limit:     int = Query(5, ge=1, le=20),
    watchlist: str = Query(""),
    session:   str = Query("both"),
):
    """
    Top movers from our tracked tickers: Ultra scan candidates + watchlist.
    Uses Massive snapshot API for current price / premarket data.
    Cached 2 min. Pass watchlist= as comma-separated tickers from the frontend.
    """
    wl_hash   = abs(hash(watchlist)) % 1_000_000
    cache_key = f"top_movers_{limit}_{wl_hash}"
    hit, val  = _cached(cache_key, ttl=_TOP_MOVERS_TTL)
    if hit:
        return val

    _EMPTY = {
        "source":     "massive",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regular":    {"gainers": [], "losers": []},
        "premarket":  {"gainers": [], "losers": [], "available": False},
    }

    symbols = _build_mover_list(watchlist)
    if not symbols:
        return {
            **_EMPTY,
            "has_symbols": False,
            "symbol_count": 0,
            "universe_source": "latest_ultra_scan_plus_watchlist",
            "message": "No tracked symbols. Run Ultra Scan or add tickers to Watchlist.",
        }

    if not _massive_news_configured():
        return {
            **_EMPTY,
            "has_symbols": True,
            "symbol_count": len(symbols),
            "universe_source": "latest_ultra_scan_plus_watchlist",
            "message": "Massive API not configured. Set MASSIVE_API_KEY.",
        }

    snap_map = _fetch_snapshot_batch(symbols)

    # Build ultra metadata map — any universe
    ultra_map: dict[str, dict] = {}
    try:
        for r in (_load_ultra_rows("1d").get("rows") or []):
            t = (r.get("ticker") or "").upper()
            if not t:
                continue
            sc = float(r.get("ultra_score", 0) or 0)
            ultra_map[t] = {
                "ultra_score":   round(sc, 1),
                "action_bucket": _score_bucket(sc),
                "abr_category":  r.get("abr", "") or r.get("abr_label", "") or "",
                "ema_ok":        bool(r.get("ema_ok", False)),
            }
    except Exception as exc:
        log.warning("top_movers: ultra map error: %s", exc)

    regular_rows:   list[dict] = []
    premarket_rows: list[dict] = []
    pm_available = False

    for sym, snap in snap_map.items():
        day      = snap.get("day")      or {}
        prev_day = snap.get("prevDay")  or {}
        pre_mkt  = snap.get("preMarket") or {}

        price      = day.get("c") or (snap.get("lastTrade") or {}).get("p")
        prev_close = prev_day.get("c")
        chg_pct    = snap.get("todaysChangePerc")

        if price is None and chg_pct is None:
            continue

        if chg_pct is None and price and prev_close and float(prev_close) != 0:
            chg_pct = (float(price) - float(prev_close)) / float(prev_close) * 100

        if chg_pct is None:
            continue

        chg_pct = round(float(chg_pct), 2)
        ui = ultra_map.get(sym, {})

        risk_flags: list[str] = []
        if chg_pct >= 20:
            risk_flags.append("PARABOLIC")
        elif chg_pct >= 12:
            risk_flags.append("EXTENDED")
        if ui.get("action_bucket") == "TOO_LATE":
            risk_flags.append("TOO_LATE")
        if chg_pct <= -15:
            risk_flags.append("SHARP_DROP")

        mover: dict = {
            "symbol":               sym,
            "price":                round(float(price), 2) if price is not None else None,
            "previous_close":       round(float(prev_close), 2) if prev_close else None,
            "change_pct":           chg_pct,
            "volume":               day.get("v"),
            "ultra_score":          ui.get("ultra_score"),
            "action_bucket":        ui.get("action_bucket") or None,
            "abr_category":         ui.get("abr_category") or None,
            "risk_flags":           risk_flags,
            "premarket_price":      None,
            "premarket_change_pct": None,
            "premarket_volume":     None,
        }
        regular_rows.append(mover)

        # Premarket
        pm_price = pre_mkt.get("c")
        pm_vol   = pre_mkt.get("v")
        if pm_price and prev_close and float(prev_close) != 0:
            pm_chg = (float(pm_price) - float(prev_close)) / float(prev_close) * 100
            pm_available = True
            pm_mover = {
                **mover,
                "premarket_price":      round(float(pm_price), 2),
                "premarket_change_pct": round(pm_chg, 2),
                "premarket_volume":     pm_vol,
            }
            premarket_rows.append(pm_mover)

    gainers = sorted(
        [m for m in regular_rows  if m["change_pct"] > 0],
        key=lambda m: m["change_pct"], reverse=True,
    )[:limit]
    losers = sorted(
        [m for m in regular_rows  if m["change_pct"] < 0],
        key=lambda m: m["change_pct"],
    )[:limit]
    pm_gainers = sorted(
        [m for m in premarket_rows if (m["premarket_change_pct"] or 0) > 0],
        key=lambda m: m["premarket_change_pct"] or 0, reverse=True,
    )[:limit]
    pm_losers = sorted(
        [m for m in premarket_rows if (m["premarket_change_pct"] or 0) < 0],
        key=lambda m: m["premarket_change_pct"] or 0,
    )[:limit]

    payload = {
        "has_symbols":      True,
        "symbol_count":     len(symbols),
        "source":           "massive",
        "universe_source":  "latest_ultra_scan_plus_watchlist",
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "regular":  {"gainers": gainers, "losers": losers},
        "premarket": {
            "gainers":   pm_gainers,
            "losers":    pm_losers,
            "available": pm_available,
        },
    }
    return _store(cache_key, payload)


@router.get("/debug-state")
def dashboard_debug_state(tf: str = Query("1d")):
    """
    Diagnostic endpoint: expose true Dashboard data state.
    Does NOT swallow ImportError or DB lookup errors — they are surfaced
    in `errors` so a missing function or schema mismatch is visible.
    """
    errors: list[str] = []
    now = time.time()

    # ── Latest scan in DB ────────────────────────────────────────────────────
    db_info: dict = {"has_data": False}
    try:
        from ultra_orchestrator import get_ultra_latest_from_db
        db_info = get_ultra_latest_from_db(tf=tf)
        if db_info.get("error"):
            errors.append(f"get_ultra_latest_from_db: {db_info['error']}")
    except Exception as exc:
        errors.append(f"import get_ultra_latest_from_db: {exc}")

    # ── Helper-resolved rows (memory or DB) ──────────────────────────────────
    try:
        src_meta = _load_ultra_rows(tf)
        if src_meta.get("error"):
            errors.append(f"_load_ultra_rows: {src_meta['error']}")
    except Exception as exc:
        src_meta = {"rows": [], "source": "none", "universe": "", "error": str(exc)}
        errors.append(f"_load_ultra_rows: {exc}")

    rows = src_meta.get("rows") or []

    # ── Per-universe memory counts ───────────────────────────────────────────
    memory_counts: dict[str, int] = {}
    try:
        from ultra_orchestrator import get_ultra_results
        for universe in ("sp500", "nasdaq", "sp500+nasdaq"):
            try:
                resp = get_ultra_results(universe=universe, tf=tf, nasdaq_batch="")
                memory_counts[universe] = len(resp.get("results", []) or [])
            except Exception as exc:
                errors.append(f"memory[{universe}]: {exc}")
    except Exception as exc:
        errors.append(f"import get_ultra_results: {exc}")

    # ── Cache state ──────────────────────────────────────────────────────────
    cache_state = {
        k: {"age_s": int(now - ts), "has_data": bool(v)}
        for k, (ts, v) in _cache.items()
    }
    setup_cache_keys = list(_setup_cache.keys())
    brief_cache_keys = list(_brief_cache.keys())

    return {
        "latest_ultra_scan_found":         bool(db_info.get("has_data")),
        "latest_ultra_scan_id":            db_info.get("scan_run_id"),
        "latest_ultra_scan_status":        db_info.get("status"),
        "latest_ultra_scan_finished_at":   db_info.get("finished_at"),
        "latest_ultra_candidate_count":    db_info.get("total_candidates"),
        "latest_ultra_scan_universe":      db_info.get("universe"),
        "latest_ultra_scan_tf":            db_info.get("tf"),
        "latest_ultra_data_age_seconds":   db_info.get("data_age_seconds"),
        "dashboard_top_candidates_count":  len(rows),
        "selected_universe":               src_meta.get("universe"),
        "selected_tf":                     tf,
        "source_used_by_dashboard":        src_meta.get("source"),
        "memory_counts":                   memory_counts,
        "cache_keys":                      cache_state,
        "setup_cache_keys":                setup_cache_keys,
        "brief_cache_keys":                brief_cache_keys,
        "massive_configured":              _massive_news_configured(),
        "ai_analysis_cache_count":         len(_news_analysis_cache),
        "errors":                          errors,
        "server_time":                     datetime.now(timezone.utc).isoformat(),
    }

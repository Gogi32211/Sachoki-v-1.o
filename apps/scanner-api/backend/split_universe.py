"""
split_universe.py — Phase 8G commit 4: port of root backend/split_universe.py.

Reverse-stock-split universe + lifecycle + stock-only filter.
Verbatim port — formulas unchanged. Only import paths differ.

The NASDAQ splits API is the data source. Network errors degrade silently
(per-date requests skipped). 6-hour in-memory cache. No CSV side-effect
(write_canonical_csv is omitted — can be re-added if a consumer needs it).

Provides:
  split_service                 — module-level singleton (lazy fetch on first use)
  SplitUniverseService.get_split_universe()        → list[dict]
  SplitUniverseService.get_split_tickers()         → list[str]
  SplitUniverseService.get_split_meta()            → dict[ticker -> meta]
  get_split_flags_for_ticker(symbol)               → dict matching unified_schema.empty_split()
  is_stock_like_split_event(row)                   → (bool, reason)
  classify_split_lifecycle(date_str, ratio)        → dict
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date as date_t
from typing import Dict, List, Optional, Tuple

from .engine_api import empty_split  # Phase B-1: engine_api boundary

log = logging.getLogger(__name__)

SPLIT_CACHE_VERSION = "split_lifecycle_stock_filter_v2"


# ── Symbol normalisation ─────────────────────────────────────────────────────

def normalize_split_symbol(symbol) -> str:
    if not symbol:
        return ""
    s = str(symbol).strip()
    s = re.sub(r"\s+", " ", s)
    return s.upper()


# ── Lifecycle constants ──────────────────────────────────────────────────────

SPLIT_HISTORY_DAYS  = 90
SPLIT_FUTURE_DAYS   = 14
SPLIT_WATCH_BASE    = 60
SPLIT_WATCH_HIGH    = 75
SPLIT_WATCH_EXTREME = 90


# ── Stock-only filter — tiered rules ─────────────────────────────────────────

_TYPE_FIELDS = (
    "assetType", "instrumentType", "securityType",
    "issueType", "type", "category", "marketCategory",
)
_TYPE_NON_STOCK: frozenset = frozenset({
    "etf", "etn", "etp", "fund", "trust",
    "warrant", "warrants", "right", "rights", "unit", "units",
    "preferred", "preferred stock", "note", "notes", "bond",
    "closed-end", "closed end", "exchange-traded fund",
    "exchange-traded note", "depositary receipt",
})

_PRODUCT_PHRASES: List[str] = [
    "etf", "etn", "etp",
    "exchange traded fund", "exchange-traded fund",
    "exchange traded note", "exchange-traded note",
    "closed-end fund", "closed end fund",
    "option income strategy",
    "leveraged etf", "inverse etf",
    "daily 2x", "daily 3x", "daily -2x", "daily -3x",
    "ultrashort", "ultra short", "ultrapro", "ultra pro",
    "bear 2x", "bear 3x", "bull 2x", "bull 3x",
    "2x long", "2x short", "3x long", "3x short",
    "senior notes", "notes due", "baby bond",
    "depositary shares", "depositary receipts",
    "preferred stock",
    "short etf", "short fund",
]

_ISSUER_BRANDS: List[str] = [
    "direxion", "proshares", "ishares", "spdr", "wisdomtree",
    "yieldmax", "defiance", "graniteshares", "roundhill", "t-rex",
    "rex shares", "ark etf", "vanguard etf", "invesco etf", "global x",
    "first trust etf", "vaneck etf", "jpmorgan etf", "kraneshares",
    "amplify etf", "simplify etf", "innovator etf", "bitwise etf",
    "grayscale trust",
]

_SECURITY_CLASS_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\bfund\b"),             "fund"),
    (re.compile(r"\bclosed[- ]?end\b"),   "closed-end"),
    (re.compile(r"\btrust\b"),            "trust"),
    (re.compile(r"\bwarrants?\b"),        "warrant"),
    (re.compile(r"\brights?\b"),          "rights"),
    (re.compile(r"\bunits?\b"),           "units"),
    (re.compile(r"\bpreferred\b"),        "preferred"),
    (re.compile(r"\binverse\b"),          "inverse"),
]

_NAME_FIELDS = (
    "companyName", "securityName", "name",
    "assetType", "instrumentType", "securityType", "issueType",
)


def is_stock_like_split_event(row: dict) -> Tuple[bool, Optional[str]]:
    for fld in _TYPE_FIELDS:
        val = (row.get(fld) or "").strip().lower()
        if val and val in _TYPE_NON_STOCK:
            return False, f"explicit_type:{fld}={val}"

    raw_text = " ".join(str(row.get(k) or "") for k in _NAME_FIELDS)
    text = re.sub(r"\s+", " ", raw_text).strip().lower()

    for phrase in _PRODUCT_PHRASES:
        if phrase in text:
            return False, f"product_phrase:{phrase}"
    for brand in _ISSUER_BRANDS:
        if brand in text:
            return False, f"issuer_keyword:{brand}"
    for pattern, label in _SECURITY_CLASS_PATTERNS:
        if pattern.search(text):
            return False, f"security_class:{label}"
    return True, None


# ── Lifecycle classification ─────────────────────────────────────────────────

def classify_split_lifecycle(
    split_date_str: str,
    ratio: float,
    today_dt: Optional[date_t] = None,
) -> dict:
    today = today_dt or datetime.now().date()
    split_date = datetime.strptime(split_date_str, "%Y-%m-%d").date()
    days_offset = (today - split_date).days

    watch_days = SPLIT_WATCH_BASE
    if ratio >= 20:
        watch_days = SPLIT_WATCH_EXTREME
    elif ratio >= 10:
        watch_days = SPLIT_WATCH_HIGH

    watch_until = split_date + timedelta(days=watch_days)

    if days_offset < -7:
        phase, wave = "UPCOMING_FAR", "FAR"
    elif days_offset <= -1:
        phase, wave = "PRE_SPLIT", "PRE"
    elif days_offset == 0:
        phase, wave = "SPLIT_DAY", "D0"
    elif days_offset <= 7:
        phase, wave = "WAVE_1", "W1"
    elif days_offset <= 20:
        phase, wave = "WAVE_2_SETUP", "W2"
    elif days_offset <= 45:
        phase, wave = "WAVE_3_SETUP", "W3"
    elif days_offset <= 60:
        phase, wave = "POST_MONITOR", "POST"
    elif days_offset <= watch_days:
        phase, wave = "EXTENDED_MONITOR", "EXT"
    else:
        phase, wave = "EXPIRED", "EXPIRED"

    _phase_heat = {
        "PRE_SPLIT": 2, "SPLIT_DAY": 3, "WAVE_1": 4,
        "WAVE_2_SETUP": 3, "WAVE_3_SETUP": 2,
        "POST_MONITOR": 1, "EXTENDED_MONITOR": 1,
    }
    heat = _phase_heat.get(phase, 0)
    if ratio >= 20:
        heat += 3
    elif ratio >= 10:
        heat += 2
    elif ratio >= 5:
        heat += 1
    heat = max(0, min(10, heat))

    return {
        "days_offset": days_offset,
        "phase":       phase,
        "wave":        wave,
        "watch_until": watch_until.isoformat(),
        "watch_days":  watch_days,
        "heat_score":  heat,
        "notes":       f"ratio={ratio:.0f}:1  phase={phase}  D{days_offset:+d}",
    }


# ── Service ──────────────────────────────────────────────────────────────────

@dataclass
class SplitUniverseResult:
    tickers: List[str] = field(default_factory=list)
    rows: List[dict]   = field(default_factory=list)
    total_events: int            = 0
    reverse_split_events: int    = 0
    stock_like_events: int       = 0
    filtered_non_stock: int      = 0
    generated_at: str            = ""
    cache_key: str               = ""


class SplitUniverseService:
    CACHE_DURATION_HOURS = 6
    MIN_RATIO            = 2.0
    # Per-request HTTP timeout when looping NASDAQ. Original code used 10s;
    # drop to 3s so even total cold-fetch (105 dates × 3s ≈ 5min worst case)
    # is bounded — but normally each request completes in <500ms when NASDAQ
    # is reachable. If NASDAQ is unreachable in this environment, every
    # request fails fast and we return 0 events.
    HTTP_TIMEOUT_SEC = 3
    # In-flight refresh guard. Prevents two simultaneous refreshers (e.g.
    # frontend polling + manual refresh) from each spinning up a NASDAQ loop.
    _refresh_in_flight: bool = False

    def __init__(self) -> None:
        self._cache:         Optional[List[dict]] = None
        self._cache_time:    Optional[datetime]   = None
        self._cache_version: Optional[str]        = None
        self._last_result:   Optional[SplitUniverseResult] = None

    def get_split_universe(self, force_refresh: bool = False) -> List[dict]:
        return self.get_split_universe_result(force_refresh=force_refresh).rows

    def get_split_universe_result(self, force_refresh: bool = False) -> SplitUniverseResult:
        if not force_refresh and self._is_cache_valid() and self._last_result is not None:
            return self._last_result

        # In-flight guard — if another caller is already refreshing, return
        # the stale cache (or an empty result) instead of starting a parallel
        # NASDAQ loop. Prevents thundering herd on cache miss.
        if SplitUniverseService._refresh_in_flight:
            if self._last_result is not None:
                return self._last_result
            return SplitUniverseResult(
                tickers=[], rows=[], total_events=0,
                generated_at=datetime.now().isoformat(timespec="seconds"),
                cache_key=f"{SPLIT_CACHE_VERSION}|in_flight",
            )

        SplitUniverseService._refresh_in_flight = True
        try:
            return self._do_refresh()
        finally:
            SplitUniverseService._refresh_in_flight = False

    def _do_refresh(self) -> SplitUniverseResult:
        today_dt = datetime.now().date()
        raw = self._fetch_nasdaq_splits()

        stock_rows: List[dict] = []
        excluded_n = 0
        for r in raw:
            ok, reason = is_stock_like_split_event(r)
            if ok:
                stock_rows.append(r)
            else:
                excluded_n += 1

        after_ratio = [r for r in stock_rows
                       if r.get("ratio") and r["ratio"] >= self.MIN_RATIO]

        for r in after_ratio:
            lc = classify_split_lifecycle(r["split_date"], r["ratio"], today_dt)
            r.update(lc)
            r["split_status"] = "upcoming" if lc["days_offset"] < 0 else "executed"

        active = [r for r in after_ratio if r["phase"] not in ("EXPIRED", "UPCOMING_FAR")]
        results = self._dedupe_by_ticker(active)
        tickers = sorted({normalize_split_symbol(r["ticker"]) for r in results if r.get("ticker")})

        result = SplitUniverseResult(
            tickers              = tickers,
            rows                 = results,
            total_events         = len(raw),
            reverse_split_events = len(after_ratio),
            stock_like_events    = len(stock_rows),
            filtered_non_stock   = excluded_n,
            generated_at         = datetime.now().isoformat(timespec="seconds"),
            cache_key            = f"{SPLIT_CACHE_VERSION}|stock_only|reverse_only",
        )

        self._cache         = results
        self._cache_time    = datetime.now()
        self._cache_version = SPLIT_CACHE_VERSION
        self._last_result   = result
        log.info("split universe refreshed: total=%d reverse=%d active=%d final=%d",
                 len(raw), len(after_ratio), len(active), len(results))
        return result

    def get_split_tickers(self, force_refresh: bool = False) -> List[str]:
        return self.get_split_universe_result(force_refresh=force_refresh).tickers

    def get_split_meta(self) -> Dict[str, dict]:
        return {r["ticker"]: r for r in self.get_split_universe()}

    # ── Internal ──────────────────────────────────────────────────────────────

    def _fetch_nasdaq_splits(self) -> List[dict]:
        url   = "https://api.nasdaq.com/api/calendar/splits"
        today = datetime.now()
        out:  List[dict] = []
        for offset in range(-SPLIT_HISTORY_DAYS, SPLIT_FUTURE_DAYS + 1):
            date_str = (today + timedelta(days=offset)).strftime("%Y-%m-%d")
            try:
                req = urllib.request.Request(
                    f"{url}?date={date_str}",
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=self.HTTP_TIMEOUT_SEC) as resp:
                    data = json.load(resp)
            except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
                log.debug("split fetch %s skipped: %s", date_str, exc)
                continue
            except Exception as exc:
                log.warning("split fetch %s error: %s", date_str, exc)
                continue

            rows = (data.get("data") or {}).get("rows") or []
            for row in rows:
                ratio_str = row.get("ratio") or ""
                ratio     = self._parse_ratio(ratio_str)
                ticker    = normalize_split_symbol(row.get("symbol") or "")
                if not ticker or ratio is None:
                    continue
                out.append({
                    "ticker":       ticker,
                    "split_date":   date_str,
                    "ratio":        ratio,
                    "ratio_str":    ratio_str,
                    "source":       "nasdaq",
                    "companyName":  row.get("companyName") or row.get("name") or "",
                    "securityName": row.get("securityName") or "",
                    "assetType":    row.get("assetType") or "",
                    "issueType":    row.get("issueType") or "",
                })
        return out

    @staticmethod
    def _parse_ratio(ratio_str: str) -> Optional[float]:
        if not ratio_str:
            return None
        s = ratio_str.replace("-for-", ":").replace("/", ":").replace(" ", "")
        if ":" in s:
            try:
                a, b = s.split(":", 1)
                af, bf = float(a), float(b)
                return bf / af if af > 0 else None
            except (ValueError, ZeroDivisionError):
                return None
        return None

    @staticmethod
    def _dedupe_by_ticker(results: List[dict]) -> List[dict]:
        seen: Dict[str, dict] = {}
        for r in results:
            t     = r["ticker"]
            phase = r.get("phase", "EXPIRED")
            active = phase not in ("EXPIRED", "UPCOMING_FAR")
            doff   = abs(r.get("days_offset", 999))
            if t not in seen:
                seen[t] = r
            else:
                e = seen[t]
                e_active = e.get("phase", "EXPIRED") not in ("EXPIRED", "UPCOMING_FAR")
                e_doff   = abs(e.get("days_offset", 999))
                if active and not e_active:
                    seen[t] = r
                elif doff < e_doff and not (e_active and not active):
                    seen[t] = r
        return list(seen.values())

    def _is_cache_valid(self) -> bool:
        if not self._cache_time or self._cache is None:
            return False
        if self._cache_version != SPLIT_CACHE_VERSION:
            return False
        return (datetime.now() - self._cache_time).total_seconds() < self.CACHE_DURATION_HOURS * 3600


split_service = SplitUniverseService()


# ── Per-ticker enrichment helper used by engine_registry / scan_engine ───────

def get_split_flags_for_ticker(symbol: str) -> dict:
    """
    Return a dict matching unified_schema.empty_split() filled with whatever
    we know about this ticker's reverse-split status.

    On any error (network down, NASDAQ API unavailable, etc.) returns an empty
    split shape with `has_split=has_reverse_split=False` — never raises.
    """
    flags = empty_split()
    sym = normalize_split_symbol(symbol)
    if not sym:
        return flags
    try:
        meta = split_service.get_split_meta()
    except Exception as exc:
        log.debug("get_split_meta failed for %s: %s", sym, exc)
        return flags

    row = meta.get(sym)
    if not row:
        # No split event found for this ticker — clean stock-only context.
        flags["stock_like_split_event"] = True
        return flags

    ratio = float(row.get("ratio") or 0)
    flags["has_split"]              = True
    flags["has_reverse_split"]      = ratio >= SplitUniverseService.MIN_RATIO
    flags["split_ratio"]            = ratio
    flags["split_date"]             = row.get("split_date")
    flags["phase"]                  = row.get("phase")
    flags["wave"]                   = row.get("wave")
    flags["days_offset"]            = row.get("days_offset")
    flags["heat_score"]             = row.get("heat_score")
    flags["stock_like_split_event"] = True   # passed the stock-only filter
    flags["split_filter_reason"]    = None
    # split_contaminated stays False here; it is the responsibility of the
    # scoring layer to decide when a phase is "contaminated" for a given
    # signal — we just expose the raw lifecycle data.
    flags["split_contaminated"]     = row.get("phase") in ("WAVE_1", "WAVE_2_SETUP")
    return flags


__all__ = [
    "split_service",
    "SplitUniverseService",
    "SplitUniverseResult",
    "is_stock_like_split_event",
    "classify_split_lifecycle",
    "normalize_split_symbol",
    "get_split_flags_for_ticker",
]

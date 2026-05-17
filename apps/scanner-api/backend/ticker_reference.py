"""
ticker_reference.py — Phase F-3: Postgres-backed per-ticker reference cache.

One table (`ticker_reference`) holds the canonical sector / industry / name
for every ticker we've ever seen, fed from Massive /v3/reference/tickers/{sym}.
Replaces the hand-maintained 260-ticker static map in sector_map.py.

Public surface:

    sync_ticker_details(symbols, force=False) -> dict
        For each symbol missing or stale in the DB, fetch from Massive and
        UPSERT. Returns {synced, skipped, failed, total} counters.

    get_sector_info(ticker) -> dict
        Read-through accessor used by scan_engine. Tries Postgres first,
        falls back to the legacy static sector_map.py map, then "Unknown".

    sic_to_gics(sic_code) -> tuple[str, str]
        Pure helper: 4-digit SIC → (GICS sector, sub-industry label).

The SIC ranges are based on the standard SEC SIC classification crosswalked
to GICS. Exhaustive but compact (~30 ranges). Anything not in a range falls
back to ("Unknown", sic_description-or-empty).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# How long a row is considered fresh before we re-pull from Massive.
# Listings change rarely; one week is plenty.
_REFERENCE_TTL_DAYS = 7


# ── SIC → GICS mapping ────────────────────────────────────────────────────────
# Reference: SEC SIC code list (https://www.sec.gov/info/edgar/siccodes.htm)
# crosswalked to GICS Level 1 sectors. Ranges are inclusive on both ends.
# Order matters — first matching range wins, so place more specific subranges
# before broader ones.

_SIC_RANGES: list[tuple[int, int, str]] = [
    # 0000–0999: Agriculture, Forestry, Fishing
    (   1,  999, "Materials"),
    # 1000–1499: Mining
    (1000, 1499, "Materials"),
    # 1500–1799: Construction
    (1500, 1799, "Industrials"),
    # 2000–2199: Food & Beverage / Tobacco
    (2000, 2199, "Consumer Staples"),
    # 2200–2399: Textile / Apparel
    (2200, 2399, "Consumer Discretionary"),
    # 2400–2599: Lumber / Wood / Furniture
    (2400, 2599, "Materials"),
    # 2600–2699: Paper
    (2600, 2699, "Materials"),
    # 2700–2799: Publishing / Printing
    (2700, 2799, "Communication Services"),
    # 2800–2829: Chemicals
    (2800, 2829, "Materials"),
    # 2830–2836: Pharmaceuticals (carve-out of 28xx)
    (2830, 2836, "Health Care"),
    # 2840–2899: Other chemicals
    (2840, 2899, "Materials"),
    # 2900–2999: Petroleum refining
    (2900, 2999, "Energy"),
    # 3000–3099: Rubber / Plastics
    (3000, 3099, "Materials"),
    # 3100–3199: Leather
    (3100, 3199, "Consumer Discretionary"),
    # 3200–3299: Stone / Clay / Glass / Concrete
    (3200, 3299, "Materials"),
    # 3300–3399: Primary metals
    (3300, 3399, "Materials"),
    # 3400–3499: Fabricated metal
    (3400, 3499, "Industrials"),
    # 3500–3599: Industrial machinery (excl. computers)
    (3500, 3569, "Industrials"),
    # 3570–3579: Computers
    (3570, 3579, "Information Technology"),
    # 3580–3599: Other machinery
    (3580, 3599, "Industrials"),
    # 3600–3669: Electrical industrial equipment
    (3600, 3669, "Industrials"),
    # 3670–3679: Electronic components / Semiconductors
    (3670, 3679, "Information Technology"),
    # 3680–3699: Other electrical
    (3680, 3699, "Information Technology"),
    # 3700–3799: Transportation equipment
    (3700, 3799, "Consumer Discretionary"),
    # 3800–3859: Measuring instruments
    (3800, 3819, "Information Technology"),
    # 3820–3829: Lab instruments
    (3820, 3829, "Health Care"),
    # 3840–3859: Medical instruments
    (3840, 3859, "Health Care"),
    # 3860–3899: Photographic equipment / Misc manufacturing
    (3860, 3899, "Consumer Discretionary"),
    # 3900–3999: Misc manufacturing
    (3900, 3999, "Consumer Discretionary"),
    # 4000–4499: Transportation (rail, water, motor freight)
    (4000, 4499, "Industrials"),
    # 4500–4599: Air transport
    (4500, 4599, "Industrials"),
    # 4600–4789: Pipelines / Transport services
    (4600, 4789, "Industrials"),
    # 4800–4829: Telecommunications
    (4800, 4829, "Communication Services"),
    # 4830–4899: Broadcasting / Cable
    (4830, 4899, "Communication Services"),
    # 4900–4999: Utilities
    (4900, 4999, "Utilities"),
    # 5000–5199: Wholesale trade
    (5000, 5199, "Consumer Discretionary"),
    # 5200–5599: Retail trade (non-food)
    (5200, 5599, "Consumer Discretionary"),
    # 5600–5699: Apparel retail
    (5600, 5699, "Consumer Discretionary"),
    # 5700–5799: Home furnishings retail
    (5700, 5799, "Consumer Discretionary"),
    # 5800–5899: Restaurants
    (5800, 5899, "Consumer Discretionary"),
    # 5900–5999: Misc retail (including drug stores 5912)
    (5912, 5912, "Consumer Staples"),
    (5900, 5999, "Consumer Discretionary"),
    # 6000–6199: Banks
    (6000, 6199, "Financials"),
    # 6200–6299: Securities brokers / dealers
    (6200, 6299, "Financials"),
    # 6300–6399: Insurance carriers
    (6300, 6399, "Financials"),
    # 6400–6499: Insurance agents / brokers
    (6400, 6499, "Financials"),
    # 6500–6599: Real estate
    (6500, 6599, "Real Estate"),
    # 6700–6770: Holding / Investment offices, REITs
    (6700, 6770, "Financials"),
    (6798, 6798, "Real Estate"),    # REITs specifically
    (6770, 6799, "Financials"),
    # 7000–7099: Hotels
    (7000, 7099, "Consumer Discretionary"),
    # 7200–7299: Personal services
    (7200, 7299, "Consumer Discretionary"),
    # 7300–7370: Business services
    (7300, 7370, "Industrials"),
    # 7371–7379: Computer services / software
    (7371, 7379, "Information Technology"),
    # 7380–7399: Other business services
    (7380, 7399, "Industrials"),
    # 7400–7799: Auto services / Misc repair / Amusement
    (7400, 7799, "Consumer Discretionary"),
    # 7800–7899: Motion pictures / Entertainment
    (7800, 7899, "Communication Services"),
    # 7900–7999: Amusement / Recreation
    (7900, 7999, "Consumer Discretionary"),
    # 8000–8099: Health services
    (8000, 8099, "Health Care"),
    # 8200–8299: Educational services
    (8200, 8299, "Consumer Discretionary"),
    # 8300–8399: Social services
    (8300, 8399, "Consumer Discretionary"),
    # 8700–8742: Engineering / Accounting / Research
    (8700, 8742, "Industrials"),
    # 8743–8748: Management consulting / Other services
    (8743, 8748, "Industrials"),
    # 8800–8999: Misc services
    (8800, 8999, "Industrials"),
    # 9100–9999: Public admin (rare for listed companies)
    (9100, 9999, "Industrials"),
]


def sic_to_gics(sic_code) -> str:
    """Map a 4-digit SIC code (string or int) to a GICS Level 1 sector."""
    if sic_code is None or sic_code == "":
        return "Unknown"
    try:
        sic = int(str(sic_code).strip())
    except (TypeError, ValueError):
        return "Unknown"
    for lo, hi, sector in _SIC_RANGES:
        if lo <= sic <= hi:
            return sector
    return "Unknown"


# ── DDL (idempotent — added to scanner-api startup migration) ────────────────

DDL = """
CREATE TABLE IF NOT EXISTS ticker_reference (
    ticker            VARCHAR(16)  PRIMARY KEY,
    name              TEXT         NOT NULL DEFAULT '',
    primary_exchange  VARCHAR(8)   NOT NULL DEFAULT '',
    sic_code          VARCHAR(8)   NOT NULL DEFAULT '',
    sic_description   TEXT         NOT NULL DEFAULT '',
    sector            VARCHAR(64)  NOT NULL DEFAULT '',
    industry          VARCHAR(128) NOT NULL DEFAULT '',
    market_cap        DOUBLE PRECISION,
    total_employees   INTEGER,
    list_date         DATE,
    type              VARCHAR(16)  NOT NULL DEFAULT '',
    is_active         BOOLEAN      NOT NULL DEFAULT TRUE,
    currency          VARCHAR(8)   NOT NULL DEFAULT '',
    last_synced       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tref_sector ON ticker_reference(sector);
CREATE INDEX IF NOT EXISTS idx_tref_synced ON ticker_reference(last_synced);
"""


# ── DB read path ──────────────────────────────────────────────────────────────

def _row_to_dict(row: tuple | None) -> dict | None:
    if not row:
        return None
    return {
        "ticker": row[0], "name": row[1], "primary_exchange": row[2],
        "sic_code": row[3], "sic_description": row[4],
        "sector": row[5], "industry": row[6],
    }


def get_from_db(ticker: str) -> dict | None:
    """Read one row by ticker; None if missing. Never raises.

    Uses db.get_conn() which yields a RealDictCursor — already in
    read-only autocommit mode.
    """
    try:
        from . import db as _db
        if not _db._available():
            return None
        with _db.get_conn() as cur:
            cur.execute(
                """SELECT ticker, name, primary_exchange, sic_code,
                          sic_description, sector, industry
                   FROM ticker_reference WHERE ticker=%s""",
                (ticker.upper().strip(),),
            )
            row = cur.fetchone()
            if not row:
                return None
            # RealDictCursor returns dict-like rows — no _row_to_dict needed.
            return {
                "ticker":            row.get("ticker"),
                "name":              row.get("name") or "",
                "primary_exchange":  row.get("primary_exchange") or "",
                "sic_code":          row.get("sic_code") or "",
                "sic_description":   row.get("sic_description") or "",
                "sector":            row.get("sector") or "",
                "industry":          row.get("industry") or "",
            }
    except Exception as exc:
        log.debug("ticker_reference.get_from_db(%s) failed: %s", ticker, exc)
        return None


def get_sector_info(ticker: str) -> dict[str, str]:
    """
    Resolve {sector, industry} for a ticker. Three-tier read:
      1. Postgres ticker_reference (Massive-sourced, authoritative)
      2. Legacy static sector_map.py (covers ~260 curated tickers)
      3. "Unknown" / "" fallback

    Never raises.
    """
    row = get_from_db(ticker)
    if row and (row.get("sector") or row.get("industry")):
        return {
            "sector":   row.get("sector") or "Unknown",
            "industry": row.get("industry") or row.get("sic_description") or "",
        }
    # Static fallback
    try:
        from .sector_map import get_sector_info as _static
        return _static(ticker)
    except Exception:
        return {"sector": "Unknown", "industry": ""}


# ── Sync (Massive → Postgres) ─────────────────────────────────────────────────

def _is_fresh(last_synced: datetime | None) -> bool:
    if last_synced is None:
        return False
    if last_synced.tzinfo is None:
        last_synced = last_synced.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - last_synced
    return age.days < _REFERENCE_TTL_DAYS


def sync_ticker_details(symbols: list[str], force: bool = False) -> dict:
    """
    Pull /v3/reference/tickers/{sym} from Massive for each symbol that's
    missing or stale (>7 days old) and UPSERT into ticker_reference.

    Returns {synced, skipped, failed, total} for progress reporting.
    """
    from . import db as _db
    if not _db._available():
        return {"synced": 0, "skipped": 0, "failed": 0, "total": len(symbols),
                "error": "DATABASE_URL not configured"}
    from .scan_engine import fetch_ticker_details

    symbols = [s.upper().strip() for s in symbols if s and s.strip()]
    symbols = list(dict.fromkeys(symbols))   # dedupe, preserve order

    synced = 0
    skipped = 0
    failed = 0

    with _db.get_write_conn() as conn:
        # Pre-load existing freshness map in one query.
        # get_write_conn yields a raw psycopg2 connection (no autocommit) —
        # we open a positional cursor for the lookup, then a fresh one per UPSERT.
        existing: dict[str, datetime] = {}
        if not force and symbols:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ticker, last_synced FROM ticker_reference WHERE ticker = ANY(%s)",
                    (symbols,),
                )
                existing = {row[0]: row[1] for row in cur.fetchall()}

        for sym in symbols:
            if not force and _is_fresh(existing.get(sym)):
                skipped += 1
                continue
            try:
                d = fetch_ticker_details(sym)
            except Exception as exc:
                log.warning("sync_ticker_details fetch %s failed: %s", sym, exc)
                d = None
            if d is None:
                failed += 1
                continue
            sector   = sic_to_gics(d.get("sic_code"))
            industry = d.get("sic_description") or ""
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO ticker_reference (
                          ticker, name, primary_exchange, sic_code,
                          sic_description, sector, industry,
                          market_cap, total_employees, list_date,
                          type, is_active, currency, last_synced)
                       VALUES (%s, %s, %s, %s,
                               %s, %s, %s,
                               %s, %s, %s,
                               %s, %s, %s, NOW())
                       ON CONFLICT (ticker) DO UPDATE SET
                          name              = EXCLUDED.name,
                          primary_exchange  = EXCLUDED.primary_exchange,
                          sic_code          = EXCLUDED.sic_code,
                          sic_description   = EXCLUDED.sic_description,
                          sector            = EXCLUDED.sector,
                          industry          = EXCLUDED.industry,
                          market_cap        = EXCLUDED.market_cap,
                          total_employees   = EXCLUDED.total_employees,
                          list_date         = EXCLUDED.list_date,
                          type              = EXCLUDED.type,
                          is_active         = EXCLUDED.is_active,
                          currency          = EXCLUDED.currency,
                          last_synced       = NOW()
                    """,
                    (
                        d["ticker"], d["name"], d["primary_exchange"],
                        d["sic_code"], d["sic_description"], sector, industry,
                        d.get("market_cap"), d.get("total_employees"),
                        d.get("list_date") or None, d["type"],
                        d["is_active"], d["currency"],
                    ),
                )
            synced += 1
        conn.commit()

    log.info("sync_ticker_details: synced=%d skipped=%d failed=%d total=%d",
             synced, skipped, failed, len(symbols))
    return {"synced": synced, "skipped": skipped, "failed": failed,
            "total": len(symbols)}


def coverage_stats() -> dict:
    """How much of the table is populated. For System page diagnostics."""
    try:
        from . import db as _db
        if not _db._available():
            return {"configured": False}
        with _db.get_conn() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM ticker_reference")
            total = (cur.fetchone() or {}).get("n", 0)
            cur.execute("""SELECT COUNT(*) AS n FROM ticker_reference
                           WHERE sector != '' AND sector != 'Unknown'""")
            classified = (cur.fetchone() or {}).get("n", 0)
            cur.execute("""SELECT sector, COUNT(*) AS n FROM ticker_reference
                           WHERE sector != '' GROUP BY sector
                           ORDER BY n DESC""")
            by_sector = {row["sector"]: row["n"] for row in cur.fetchall()}
        return {
            "configured": True, "total": total, "classified": classified,
            "by_sector": by_sector,
        }
    except Exception as exc:
        log.warning("coverage_stats: %s", exc)
        return {"configured": True, "error": type(exc).__name__}


__all__ = [
    "DDL",
    "sic_to_gics", "get_from_db", "get_sector_info",
    "sync_ticker_details", "coverage_stats",
]

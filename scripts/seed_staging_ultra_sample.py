#!/usr/bin/env python3
"""
seed_staging_ultra_sample.py

Seeds staging PostgreSQL with:
  - ultra_scan_runs table (schema from backend/ultra_scan_migration.py)
  - ultra_scan_candidates table
  - one completed is_latest=TRUE scan run
  - 100 synthetic candidate rows matching production row_json structure

Usage:
  # Synthetic sample (no production access needed):
  STAGING_DATABASE_URL=postgresql://... python scripts/seed_staging_ultra_sample.py

  # Export from production, import to staging:
  STAGING_DATABASE_URL=postgresql://... \\
  PRODUCTION_DATABASE_URL=postgresql://... \\
  python scripts/seed_staging_ultra_sample.py

  # Force re-seed even if sample run already exists:
  STAGING_DATABASE_URL=postgresql://... python scripts/seed_staging_ultra_sample.py --force

Safety:
  - READ-ONLY from production (SELECT only)
  - WRITE only to STAGING_DATABASE_URL
  - Never touches root backend code
  - Idempotent: skips if SEED_RUN already present (unless --force)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2-binary not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────
STAGING_URL     = os.environ.get("STAGING_DATABASE_URL") or os.environ.get("DATABASE_URL")
PRODUCTION_URL  = os.environ.get("PRODUCTION_DATABASE_URL")
FORCE           = "--force" in sys.argv
SEED_MARKER     = "SEED_SAMPLE_2A"   # stored in nasdaq_batch to identify this run
SAMPLE_LIMIT    = 200                # max candidates to pull from production

# ── Schema (exact copy from backend/ultra_scan_migration.py) ──────────────────
_DDL = """
CREATE TABLE IF NOT EXISTS ultra_scan_runs (
    id               SERIAL PRIMARY KEY,
    universe         VARCHAR(20) NOT NULL DEFAULT 'sp500',
    tf               VARCHAR(10) NOT NULL DEFAULT '1d',
    nasdaq_batch     VARCHAR(20) NOT NULL DEFAULT '',
    status           VARCHAR(20) NOT NULL DEFAULT 'running',
    is_latest        BOOLEAN NOT NULL DEFAULT FALSE,
    total_candidates INTEGER DEFAULT 0,
    last_turbo_scan  TEXT,
    sources_json     TEXT,
    warnings_json    TEXT,
    meta_json        TEXT,
    started_at       TIMESTAMPTZ DEFAULT NOW(),
    finished_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_usr_univ_tf    ON ultra_scan_runs(universe, tf, nasdaq_batch);
CREATE INDEX IF NOT EXISTS idx_usr_is_latest  ON ultra_scan_runs(is_latest);
CREATE INDEX IF NOT EXISTS idx_usr_status     ON ultra_scan_runs(status);
CREATE INDEX IF NOT EXISTS idx_usr_created_at ON ultra_scan_runs(created_at);

CREATE TABLE IF NOT EXISTS ultra_scan_candidates (
    id          BIGSERIAL PRIMARY KEY,
    scan_run_id INTEGER NOT NULL REFERENCES ultra_scan_runs(id) ON DELETE CASCADE,
    ticker      TEXT NOT NULL,
    ultra_score REAL DEFAULT 0,
    row_json    TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_usc_run_id ON ultra_scan_candidates(scan_run_id);
CREATE INDEX IF NOT EXISTS idx_usc_ticker ON ultra_scan_candidates(ticker);
CREATE INDEX IF NOT EXISTS idx_usc_score  ON ultra_scan_candidates(scan_run_id, ultra_score DESC);
"""

# ── Synthetic sample data ─────────────────────────────────────────────────────
# 100 realistic candidate rows matching production row_json structure.
# Fields match what backend/ultra_orchestrator.py stores per candidate.

_SECTORS = ["Technology", "Healthcare", "Financials", "Consumer Discretionary",
            "Industrials", "Communication Services", "Energy", "Materials"]
_T_SIGNALS = ["T4", "T1G", "T2G", "T1", "T2", "T3", "T6"]
_BANDS_V2  = ["A+", "A", "A", "B", "B", "B", "C"]
_PRIORITIES = ["HIGH_PRIORITY", "WATCH_A", "WATCH_A", "STRONG_WATCH",
               "STRONG_WATCH", "CONTEXT_WATCH"]
_PROFILES  = ["nasdaq", "sp500", "all_us"]
_ABR_CATS  = ["ACTIVATION", "BREAKING", "RETEST", "NONE"]
_RTB_PHASES = ["TREND", "BREAKOUT", "RANGE", "WATCH"]
_REGIMES   = ["ACTIONABLE_SETUP", "CLEAN_ENTRY", "SHAKEOUT_ABSORB",
              "REBOUND_SQUEEZE", "NONE"]

# 100 real-ish tickers spread across sectors
_TICKERS = [
    "NVDA","AAPL","MSFT","GOOGL","META","AMZN","TSLA","AVGO","AMD","QCOM",
    "CRM","ORCL","ADBE","SNOW","PLTR","DDOG","ZS","CRWD","NET","PANW",
    "LLY","UNH","ABBV","MRK","BMY","JNJ","AMGN","GILD","REGN","VRTX",
    "JPM","GS","MS","BAC","WFC","C","BLK","SCHW","AXP","COF",
    "HD","LOW","TGT","COST","WMT","AMZN","NKE","SBUX","MCD","CMG",
    "GE","CAT","HON","RTX","LMT","BA","DE","EMR","ETN","PH",
    "XOM","CVX","COP","SLB","MPC","VLO","PSX","OXY","EOG","PXD",
    "UBER","LYFT","ABNB","DASH","RBLX","SPOT","TTD","SNAP","PINS","Z",
    "FCX","NEM","AA","X","CLF","NUE","CF","MOS","LIN","APD",
    "SPY","QQQ","IWM","DIA","XLK","XLV","XLF","XLE","XLI","GLD",
]


def _make_candidate(i: int, ticker: str) -> dict:
    """Build a synthetic candidate row matching production row_json structure."""
    import random
    rng = random.Random(hash(ticker) ^ i)

    ultra_score = max(30, min(99, 55 + rng.randint(-25, 44)))
    turbo_score = max(20, min(95, ultra_score - rng.randint(0, 15)))
    band_idx    = 0 if ultra_score >= 90 else (1 if ultra_score >= 80 else
                  2 if ultra_score >= 65 else 3 if ultra_score >= 50 else 4)
    band_v2     = ["A+", "A", "B", "C", "D"][band_idx]
    priority    = _PRIORITIES[min(band_idx, len(_PRIORITIES)-1)]
    t_sig       = rng.choice(_T_SIGNALS)
    sector      = _SECTORS[i % len(_SECTORS)]
    abr         = rng.choice(_ABR_CATS)
    rtb         = rng.choice(_RTB_PHASES)
    regime      = rng.choice(_REGIMES)
    profile     = rng.choice(_PROFILES)
    price       = round(rng.uniform(8, 850), 2)
    change_pct  = round(rng.uniform(-3.5, 8.5), 2)
    volume      = rng.randint(500_000, 25_000_000)

    reasons = []
    if ultra_score >= 80: reasons.append("BUY_2809")
    if ultra_score >= 75: reasons.append("MOMO+CAT")
    if regime != "NONE":  reasons.append(f"REGIME:{regime}")
    if abr != "NONE":     reasons.append(f"ABR:{abr}")

    flags = []
    if rng.random() < 0.3: flags.append("MOMENTUM_A")
    if rng.random() < 0.2: flags.append("SETUP_ONLY")

    return {
        # identifiers
        "ticker":       ticker,
        "name":         f"{ticker} Corp",
        "sector":       sector,
        "industry":     f"{sector} Industry",
        "profile":      profile,
        # price
        "price":        price,
        "close":        price,
        "change_pct":   change_pct,
        "volume":       volume,
        "avg_vol":      int(volume * rng.uniform(0.7, 1.3)),
        # T/Z signals
        "t_signal":     t_sig,
        "z_signal":     "" if rng.random() > 0.3 else rng.choice(["Z1","Z2","Z4"]),
        "l_signal":     rng.choice(["L1","L3","L34","FRI34","BLUE",""]),
        # scoring
        "turbo_score":  turbo_score,
        "ultra_score":  ultra_score,
        "ultra_score_band":           ["A","A","B","C","D"][band_idx],
        "ultra_score_band_v2":        band_v2,
        "ultra_score_priority":       priority,
        "ultra_score_reasons":        reasons,
        "ultra_score_flags":          flags,
        "ultra_score_raw_before_penalty": ultra_score + rng.randint(0, 8),
        "ultra_score_penalty_total":  rng.randint(0, 5),
        "ultra_score_regime_bonus":   12 if regime == "ACTIONABLE_SETUP" else
                                      10 if regime == "SHAKEOUT_ABSORB" else
                                       8 if regime == "CLEAN_ENTRY" else 0,
        "ultra_score_caps_applied":   [],
        "ultra_score_cap_reason":     "",
        # combo signals
        "buy_2809":  ultra_score >= 75,
        "rocket":    ultra_score >= 88,
        "sig3g":     rng.random() < 0.4,
        "rtv":       rng.random() < 0.3,
        "cd":        rng.random() < 0.25,
        # profile/context
        "rtb_phase":        rtb,
        "sweet_spot":       ultra_score >= 70 and rng.random() < 0.6,
        "late_warning":     rng.random() < 0.15,
        "abr_category":     abr,
        "tz_intel_role":    rng.choice(["ACTIVATION","BREAKING","RETEST",""]),
        "wlnbb_bucket":     rng.choice(["L1","L3","L34","FRI34",""]),
        "ema_state":        rng.choice(["ABOVE","BELOW","CROSS_UP","CROSS_DOWN",""]),
        "action_bucket":    rng.choice(["BUY","WATCH","REVIEW",""]),
        "sequence":         rng.choice(["T4→T2","T1G→T2G","T4→Z3→T2","T6→T1",""]),
        "sequence_4bar":    rng.choice(["T4→T2→T2→T1","T1G→T2G→T2→T4",""]),
        # enrichment flags
        "ultra_enriched": True,
        "ultra_sources": {
            "has_turbo":         True,
            "has_tz_wlnbb":      rng.random() < 0.7,
            "has_tz_intel":      rng.random() < 0.5,
            "has_pullback":      rng.random() < 0.4,
            "has_rare_reversal": rng.random() < 0.2,
        },
    }


# ── DB helpers ────────────────────────────────────────────────────────────────

def _connect(url: str, readonly: bool = False):
    conn = psycopg2.connect(url, connect_timeout=10)
    if readonly:
        conn.set_session(readonly=True, autocommit=True)
    else:
        conn.autocommit = False
    return conn


def _cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


# ── Schema ────────────────────────────────────────────────────────────────────

def create_schema(conn):
    print("Creating schema (IF NOT EXISTS)...")
    with _cursor(conn) as cur:
        for stmt in [s.strip() for s in _DDL.split(";") if s.strip()]:
            cur.execute(stmt)
    conn.commit()
    print("  Schema ready.")


# ── Production export ─────────────────────────────────────────────────────────

def export_from_production() -> tuple[dict | None, list[dict]]:
    """Pull latest run + up to SAMPLE_LIMIT candidates from production. READ-ONLY."""
    print(f"\nConnecting to production (read-only)...")
    try:
        prod = _connect(PRODUCTION_URL, readonly=True)
    except Exception as exc:
        print(f"  ERROR connecting to production: {exc}")
        return None, []

    try:
        with _cursor(prod) as cur:
            cur.execute("""
                SELECT id, universe, tf, nasdaq_batch, status,
                       total_candidates, started_at, finished_at
                FROM ultra_scan_runs
                WHERE is_latest = TRUE AND status = 'completed'
                ORDER BY finished_at DESC
                LIMIT 1
            """)
            run = cur.fetchone()
            if not run:
                print("  No completed latest scan in production.")
                return None, []

            run = dict(run)
            print(f"  Found production run: id={run['id']} universe={run['universe']} "
                  f"tf={run['tf']} candidates={run['total_candidates']}")

            cur.execute("""
                SELECT ticker, ultra_score, row_json
                FROM ultra_scan_candidates
                WHERE scan_run_id = %s
                ORDER BY ultra_score DESC
                LIMIT %s
            """, (run["id"], SAMPLE_LIMIT))
            candidates = [dict(r) for r in cur.fetchall()]
            print(f"  Exported {len(candidates)} candidates.")
            return run, candidates
    except Exception as exc:
        print(f"  ERROR reading production: {exc}")
        return None, []
    finally:
        prod.close()


# ── Seed ─────────────────────────────────────────────────────────────────────

def seed_staging(staging_conn, prod_run: dict | None, prod_candidates: list[dict]):
    with _cursor(staging_conn) as cur:
        # Check if seed run already exists
        cur.execute(
            "SELECT id FROM ultra_scan_runs WHERE nasdaq_batch = %s LIMIT 1",
            (SEED_MARKER,),
        )
        existing = cur.fetchone()

        if existing and not FORCE:
            print(f"\nSeed run already exists (id={existing['id']}). "
                  f"Use --force to re-seed.")
            return existing["id"]

        if existing and FORCE:
            print(f"\nForce re-seed: deleting old seed run id={existing['id']}...")
            cur.execute("DELETE FROM ultra_scan_runs WHERE nasdaq_batch = %s", (SEED_MARKER,))
            staging_conn.commit()

        # Flip any existing is_latest to false for same universe/tf
        universe = prod_run["universe"] if prod_run else "sp500"
        tf       = prod_run["tf"]       if prod_run else "1d"
        cur.execute(
            "UPDATE ultra_scan_runs SET is_latest = FALSE "
            "WHERE universe = %s AND tf = %s",
            (universe, tf),
        )

        now = datetime.now(timezone.utc).isoformat()

        # Insert seed run
        if prod_run:
            started_at  = str(prod_run.get("started_at")  or now)
            finished_at = str(prod_run.get("finished_at") or now)
        else:
            started_at = finished_at = now

        n_candidates = len(prod_candidates) if prod_candidates else 100

        cur.execute(
            """
            INSERT INTO ultra_scan_runs
              (universe, tf, nasdaq_batch, status, is_latest, total_candidates,
               sources_json, warnings_json, started_at, finished_at)
            VALUES (%s, %s, %s, 'completed', TRUE, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                universe, tf, SEED_MARKER,
                n_candidates,
                json.dumps({"source": "seed_script"}),
                json.dumps([]),
                started_at, finished_at,
            ),
        )
        run_id = cur.fetchone()["id"]
        print(f"\nInserted seed scan run: id={run_id} ({universe}/{tf})")

        # Build candidate rows
        if prod_candidates:
            print(f"Inserting {len(prod_candidates)} candidates from production...")
            rows = [
                (run_id, c["ticker"], float(c["ultra_score"] or 0), c["row_json"])
                for c in prod_candidates
            ]
        else:
            print("Inserting 100 synthetic candidates...")
            synthetic = [_make_candidate(i, _TICKERS[i % len(_TICKERS)])
                         for i in range(100)]
            rows = [
                (run_id, c["ticker"], float(c["ultra_score"]), json.dumps(c))
                for c in synthetic
            ]

        psycopg2.extras.execute_batch(
            cur,
            "INSERT INTO ultra_scan_candidates (scan_run_id, ticker, ultra_score, row_json) "
            "VALUES (%s, %s, %s, %s)",
            rows,
            page_size=100,
        )

        staging_conn.commit()
        print(f"Inserted {len(rows)} candidates for run_id={run_id}.")
        return run_id


# ── Verify ────────────────────────────────────────────────────────────────────

def verify(staging_conn, run_id: int):
    print("\nVerification:")
    with _cursor(staging_conn) as cur:
        cur.execute("SELECT COUNT(*) AS n FROM ultra_scan_runs")
        print(f"  ultra_scan_runs rows:      {cur.fetchone()['n']}")

        cur.execute("SELECT COUNT(*) AS n FROM ultra_scan_candidates")
        print(f"  ultra_scan_candidates rows: {cur.fetchone()['n']}")

        cur.execute(
            "SELECT id, status, is_latest, universe, tf, total_candidates, finished_at "
            "FROM ultra_scan_runs WHERE is_latest = TRUE AND status = 'completed'"
        )
        run = cur.fetchone()
        if run:
            print(f"  Latest completed run: id={run['id']} universe={run['universe']} "
                  f"tf={run['tf']} is_latest={run['is_latest']} "
                  f"candidates={run['total_candidates']}")
        else:
            print("  WARNING: No is_latest=TRUE completed run found!")

        cur.execute(
            "SELECT COUNT(*) AS n FROM ultra_scan_candidates WHERE scan_run_id = %s",
            (run_id,),
        )
        print(f"  Candidates for run_id={run_id}: {cur.fetchone()['n']}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not STAGING_URL:
        print("ERROR: STAGING_DATABASE_URL (or DATABASE_URL) not set.")
        sys.exit(1)

    print(f"Connecting to staging DB...")
    try:
        staging = _connect(STAGING_URL)
    except Exception as exc:
        print(f"ERROR: Cannot connect to staging DB: {exc}")
        sys.exit(1)
    print("  Connected.")

    try:
        create_schema(staging)

        prod_run, prod_candidates = None, []
        if PRODUCTION_URL:
            prod_run, prod_candidates = export_from_production()
        else:
            print("\nNo PRODUCTION_DATABASE_URL set — using synthetic sample data.")

        run_id = seed_staging(staging, prod_run, prod_candidates)
        verify(staging, run_id)

        source = "production sample" if prod_candidates else "synthetic"
        print(f"\nDone. Seeded staging DB with {source} data.")
        print("scanner-api /api/scans/ultra/latest should now return has_data=true.")
    finally:
        staging.close()


if __name__ == "__main__":
    main()

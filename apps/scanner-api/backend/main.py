"""
scanner-api — Phase 3: read-only Ultra Scan DB integration.

All endpoints are read-only except /api/admin/seed (one-time staging seeder,
protected by SEED_TOKEN env var, to be removed in Phase 4).
"""
from __future__ import annotations

import json
import logging
import os
import secrets

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "info").upper())

app = FastAPI(title="scanner-api", version="0.2.0")

_VERSION = "0.2.0"
_PHASE = "3-readonly-db"

# Known Ultra Scan table names (confirmed from backend/ultra_scan_migration.py)
_RUN_TABLE = "ultra_scan_runs"
_CAND_TABLE = "ultra_scan_candidates"

# Safe columns for ORDER BY — never interpolate arbitrary user input
_SAFE_SORT_COLS = {"ultra_score", "ticker", "created_at"}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_row_json(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _normalize_candidate(row_json_str: str | None, db_score: float | None) -> dict:
    """
    Map a raw row_json string to the standard candidate shape.
    Returns null/empty for missing fields — never raises.
    """
    r = _parse_row_json(row_json_str)

    def _get(*keys, default=None):
        for k in keys:
            v = r.get(k)
            if v is not None:
                return v
        return default

    def _as_list(val):
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                return parsed if isinstance(parsed, list) else [parsed]
            except Exception:
                return [val] if val else []
        return []

    return {
        "symbol":               _get("ticker", default=""),
        "company":              _get("name", "company", default=""),
        "sector":               _get("sector", default=""),
        "industry":             _get("industry", default=""),
        "price":                _get("price", "close", "last_price", default=None),
        "change_pct":           _get("change_pct", "chg_pct", default=None),
        "volume":               _get("volume", default=None),
        "ultra_score":          _get("ultra_score", default=db_score),
        "setup_quality_score":  _get("turbo_score", "score", default=None),
        "band":                 _get("ultra_score_band_v2", "ultra_score_band", default=""),
        "priority":             _get("ultra_score_priority", default=""),
        "role":                 _get("tz_intel_role", "abr_role", default=""),
        "action_bucket":        _get("action_bucket", "bucket", default=""),
        "final_signal":         _get("t_signal", "final_signal", "signal", default=""),
        "sequence_4bar":        _get("sequence_4bar", "sequence", default=""),
        "abr_category":         _get("abr_category", "category", default=""),
        "wlnbb_bucket":         _get("wlnbb_bucket", "l_bucket", default=""),
        "ema_state":            _get("ema_state", "ema_cross", default=""),
        "risk_flags":           _as_list(_get("ultra_score_flags", default=[])),
        "events":               [],
        "why_selected":         _as_list(_get("ultra_score_reasons", default=[])),
    }


def _get_latest_run(cur, universe: str | None, tf: str | None) -> dict | None:
    """
    Find the latest completed scan run.
    Tries universe+tf first; falls back to any is_latest completed run.
    """
    if universe and tf:
        cur.execute(
            f"""
            SELECT id, universe, tf, nasdaq_batch, status,
                   started_at, finished_at, total_candidates
            FROM {_RUN_TABLE}
            WHERE universe=%s AND tf=%s AND is_latest=TRUE AND status='completed'
            ORDER BY finished_at DESC LIMIT 1
            """,
            (universe, tf),
        )
        row = cur.fetchone()
        if row:
            return dict(row)

    cur.execute(
        f"""
        SELECT id, universe, tf, nasdaq_batch, status,
               started_at, finished_at, total_candidates
        FROM {_RUN_TABLE}
        WHERE is_latest=TRUE AND status='completed'
        ORDER BY finished_at DESC LIMIT 1
        """
    )
    row = cur.fetchone()
    return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Health / version
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "scanner-api"}


@app.get("/version")
def version():
    return {"service": "scanner-api", "version": _VERSION, "phase": _PHASE}


# ─────────────────────────────────────────────────────────────────────────────
# Debug endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/debug/status")
def debug_status():
    from . import db as _db

    db_configured = bool(_db.DATABASE_URL)
    db_connected, db_error = (False, None)
    latest_scan_found = False
    latest_scan_id = None
    latest_candidate_count = 0

    if db_configured:
        db_connected, db_error = _db.ping()

    if db_connected:
        try:
            with _db.get_conn() as cur:
                if _db.table_exists(_RUN_TABLE):
                    run = _get_latest_run(cur, None, None)
                    if run:
                        latest_scan_found = True
                        latest_scan_id = run["id"]
                        latest_candidate_count = run.get("total_candidates") or 0
        except Exception as exc:
            log.warning("debug/status DB probe: %s", exc)

    return {
        "service":                      "scanner-api",
        "mode":                         "read_only_db_phase",
        "database_configured":          db_configured,
        "database_connected":           db_connected,
        "database_error":               db_error or None,
        "redis_configured":             bool(os.getenv("REDIS_URL")),
        "massive_configured":           bool(os.getenv("MASSIVE_API_KEY")),
        "scanning_enabled":             False,
        "scheduler_enabled":            False,
        "latest_ultra_scan_found":      latest_scan_found,
        "latest_ultra_scan_id":         latest_scan_id,
        "latest_ultra_candidate_count": latest_candidate_count,
    }


@app.get("/api/debug/db")
def debug_db():
    from . import db as _db

    if not _db.DATABASE_URL:
        return {
            "database_configured":  False,
            "database_connected":   False,
            "tables":               [],
            "ultra_related_tables": [],
            "candidate_tables":     [],
            "scan_run_tables":      [],
            "notes":                ["DATABASE_URL not set"],
        }

    connected, err = _db.ping()
    if not connected:
        return {
            "database_configured":  True,
            "database_connected":   False,
            "error_type":           err,
            "tables":               [],
            "ultra_related_tables": [],
            "candidate_tables":     [],
            "scan_run_tables":      [],
            "notes":                ["Connection failed — check DATABASE_URL"],
        }

    try:
        tables = _db.list_tables()
    except Exception as exc:
        return {
            "database_configured":  True,
            "database_connected":   True,
            "error_type":           type(exc).__name__,
            "tables":               [],
            "ultra_related_tables": [],
            "candidate_tables":     [],
            "scan_run_tables":      [],
            "notes":                ["Failed to list tables"],
        }

    keywords = {"ultra", "scan", "candidate", "turbo", "dashboard"}
    all_names = [t["table"] for t in tables]
    ultra_related   = [n for n in all_names if any(k in n for k in keywords)]
    candidate_tables = [n for n in all_names if "candidate" in n]
    scan_run_tables  = [n for n in all_names if "scan" in n and "run" in n]

    notes = []
    if _RUN_TABLE not in all_names:
        notes.append(f"'{_RUN_TABLE}' not found — DB may be empty staging instance")
    if _CAND_TABLE not in all_names:
        notes.append(f"'{_CAND_TABLE}' not found")
    if not notes:
        notes.append("Ultra Scan tables present and ready")

    return {
        "database_configured":  True,
        "database_connected":   True,
        "tables":               tables,
        "ultra_related_tables": ultra_related,
        "candidate_tables":     candidate_tables,
        "scan_run_tables":      scan_run_tables,
        "notes":                notes,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Ultra Scan read endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/scans/ultra/latest")
def get_latest_ultra_scan(
    universe: str | None = Query(default=None),
    tf: str | None = Query(default=None),
):
    from . import db as _db

    if not _db.DATABASE_URL:
        return {"has_data": False, "message": "DATABASE_URL not configured", "source": "db"}

    connected, err = _db.ping()
    if not connected:
        return {"has_data": False, "message": f"DB connection failed ({err})", "source": "db"}

    try:
        with _db.get_conn() as cur:
            if not _db.table_exists(_RUN_TABLE):
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' ORDER BY table_name"
                )
                found = [r["table_name"] for r in cur.fetchall()]
                return {
                    "has_data":     False,
                    "message":      "Ultra Scan tables not found in this database.",
                    "source":       "db",
                    "tables_found": found,
                }

            run = _get_latest_run(cur, universe, tf)
            if not run:
                return {"has_data": False, "message": "No completed Ultra Scan found.", "source": "db"}

            return {
                "has_data": True,
                "source":   "db",
                "run": {
                    "id":               run["id"],
                    "status":           run["status"],
                    "universe":         run["universe"],
                    "timeframe":        run["tf"],
                    "nasdaq_batch":     run.get("nasdaq_batch") or "",
                    "started_at":       str(run["started_at"]) if run.get("started_at") else None,
                    "finished_at":      str(run["finished_at"]) if run.get("finished_at") else None,
                    "total_candidates": run.get("total_candidates") or 0,
                },
            }
    except Exception as exc:
        log.exception("get_latest_ultra_scan error")
        return JSONResponse(
            status_code=500,
            content={"has_data": False, "message": "Internal error", "error_type": type(exc).__name__},
        )


@app.get("/api/scans/ultra/latest/candidates")
def get_latest_ultra_candidates(
    universe: str | None = Query(default=None),
    tf: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(default="ultra_score"),
    sort_dir: str = Query(default="desc"),
):
    from . import db as _db

    sort_col   = sort_by if sort_by in _SAFE_SORT_COLS else "ultra_score"
    sort_order = "DESC" if sort_dir.lower() != "asc" else "ASC"

    _no_data = {
        "has_data": False, "scan_run_id": None,
        "total_available": 0, "count": 0, "candidates": [],
    }

    if not _db.DATABASE_URL:
        return {**_no_data, "message": "DATABASE_URL not configured"}

    connected, err = _db.ping()
    if not connected:
        return {**_no_data, "message": f"DB connection failed ({err})"}

    try:
        with _db.get_conn() as cur:
            if not _db.table_exists(_RUN_TABLE) or not _db.table_exists(_CAND_TABLE):
                return {**_no_data, "message": "Ultra Scan tables not found in this database."}

            run = _get_latest_run(cur, universe, tf)
            if not run:
                return {**_no_data, "message": "No completed Ultra Scan found."}

            run_id = run["id"]

            cur.execute(
                f"SELECT COUNT(*) AS n FROM {_CAND_TABLE} WHERE scan_run_id=%s",
                (run_id,),
            )
            total = (cur.fetchone() or {}).get("n") or 0

            cur.execute(
                f"""
                SELECT ticker, ultra_score, row_json
                FROM {_CAND_TABLE}
                WHERE scan_run_id=%s
                ORDER BY {sort_col} {sort_order}
                LIMIT %s OFFSET %s
                """,
                (run_id, limit, offset),
            )
            rows = cur.fetchall()

            candidates = [
                _normalize_candidate(r.get("row_json"), r.get("ultra_score"))
                for r in rows
            ]

            return {
                "has_data":        True,
                "scan_run_id":     run_id,
                "universe":        run["universe"],
                "timeframe":       run["tf"],
                "total_available": int(total),
                "count":           len(candidates),
                "limit":           limit,
                "offset":          offset,
                "candidates":      candidates,
            }

    except Exception as exc:
        log.exception("get_latest_ultra_candidates error")
        return JSONResponse(
            status_code=500,
            content={**_no_data, "error_type": type(exc).__name__},
        )


# ─────────────────────────────────────────────────────────────────────────────
# One-time staging seeder  (Phase 3.5 — remove in Phase 4)
# Protected by SEED_TOKEN env var. Never runs scans. Synthetic data only.
# ─────────────────────────────────────────────────────────────────────────────

_DDL_SCHEMA = """
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
CREATE INDEX IF NOT EXISTS idx_usc_score  ON ultra_scan_candidates(scan_run_id, ultra_score DESC)
"""

_SEED_MARKER  = "SEED_SAMPLE_3.5"
_SEED_TICKERS = [
    "NVDA","AAPL","MSFT","GOOGL","META","AMZN","TSLA","AVGO","AMD","QCOM",
    "CRM","ORCL","ADBE","SNOW","PLTR","DDOG","ZS","CRWD","NET","PANW",
    "LLY","UNH","ABBV","MRK","BMY","AMGN","GILD","REGN","VRTX","ISRG",
    "JPM","GS","MS","BAC","WFC","BLK","SCHW","AXP","COF","SPGI",
    "HD","LOW","TGT","COST","NKE","SBUX","MCD","CMG","BKNG","MAR",
    "GE","CAT","HON","RTX","LMT","DE","EMR","ETN","PH","ITW",
    "XOM","CVX","COP","SLB","MPC","VLO","OXY","EOG","FANG","HAL",
    "UBER","ABNB","DASH","RBLX","SPOT","TTD","SNAP","PINS","APP","COUR",
    "FCX","NEM","AA","NUE","CF","MOS","LIN","APD","ECL","SHW",
    "T","VZ","TMUS","NFLX","DIS","WBD","PARA","FOXA","OMC","IPG",
]
_SECTORS = ["Technology","Healthcare","Financials","Consumer Discretionary",
            "Industrials","Communication Services","Energy","Materials"]
_T_SIGS  = ["T4","T1G","T2G","T1","T2","T3","T6"]
_REGIMES = ["ACTIONABLE_SETUP","CLEAN_ENTRY","SHAKEOUT_ABSORB","REBOUND_SQUEEZE","NONE"]
_ABR     = ["ACTIVATION","BREAKING","RETEST","NONE"]
_RTB     = ["TREND","BREAKOUT","RANGE","WATCH"]


def _synthetic_row(i: int, ticker: str) -> dict:
    import random
    rng = random.Random(hash(ticker) ^ i ^ 0xFACE)
    score = max(35, min(99, 58 + rng.randint(-23, 41)))
    turbo = max(20, min(95, score - rng.randint(0, 12)))
    bi    = 0 if score >= 90 else 1 if score >= 80 else 2 if score >= 65 else 3 if score >= 50 else 4
    bv2   = ["A+","A","B","C","D"][bi]
    pri   = ["HIGH_PRIORITY","WATCH_A","STRONG_WATCH","CONTEXT_WATCH","LOW"][bi]
    rgm   = rng.choice(_REGIMES)
    reasons = []
    if score >= 80: reasons.append("BUY_2809")
    if score >= 75: reasons.append("MOMO+CAT")
    if rgm != "NONE": reasons.append(f"REGIME:{rgm}")
    abr_val = rng.choice(_ABR)
    if abr_val != "NONE": reasons.append(f"ABR:{abr_val}")
    price = round(rng.uniform(8, 820), 2)
    return {
        "ticker": ticker, "name": f"{ticker} Inc", "sector": _SECTORS[i % len(_SECTORS)],
        "industry": f"{_SECTORS[i%len(_SECTORS)]} Group", "profile": rng.choice(["nasdaq","sp500"]),
        "price": price, "close": price, "change_pct": round(rng.uniform(-3.5, 9.0), 2),
        "volume": rng.randint(400_000, 30_000_000), "avg_vol": rng.randint(500_000, 20_000_000),
        "t_signal": rng.choice(_T_SIGS), "z_signal": rng.choice(["Z1","Z4","",""]),
        "l_signal": rng.choice(["L1","L3","L34","FRI34","BLUE",""]),
        "turbo_score": turbo, "ultra_score": score,
        "ultra_score_band": ["A","A","B","C","D"][bi], "ultra_score_band_v2": bv2,
        "ultra_score_priority": pri, "ultra_score_reasons": reasons,
        "ultra_score_flags": ["MOMENTUM_A"] if rng.random() < 0.3 else [],
        "ultra_score_raw_before_penalty": score + rng.randint(0, 8),
        "ultra_score_penalty_total": rng.randint(0, 5),
        "ultra_score_regime_bonus": 12 if rgm=="ACTIONABLE_SETUP" else 8 if rgm=="CLEAN_ENTRY" else 0,
        "ultra_score_caps_applied": [], "ultra_score_cap_reason": "",
        "buy_2809": score >= 75, "rocket": score >= 88,
        "sig3g": rng.random() < 0.4, "rtv": rng.random() < 0.3,
        "rtb_phase": rng.choice(_RTB), "sweet_spot": score >= 70 and rng.random() < 0.6,
        "abr_category": abr_val, "tz_intel_role": rng.choice(["ACTIVATION","BREAKING","",""]),
        "wlnbb_bucket": rng.choice(["L1","L3","L34","",""]),
        "ema_state": rng.choice(["ABOVE","CROSS_UP","","BELOW"]),
        "action_bucket": rng.choice(["BUY","WATCH","REVIEW",""]),
        "sequence": rng.choice(["T4→T2","T1G→T2G","",""]),
        "sequence_4bar": rng.choice(["T4→T2→T2→T1","",""]),
        "ultra_enriched": True,
        "ultra_sources": {"has_turbo": True, "has_tz_wlnbb": rng.random()<0.7,
                          "has_tz_intel": rng.random()<0.5},
    }


@app.post("/api/admin/seed")
def admin_seed(x_seed_token: str = Header(default="")):
    """
    One-time staging DB seeder. Protected by SEED_TOKEN env var.
    Creates schema + inserts 100 synthetic candidates. Idempotent.
    REMOVE in Phase 4.
    """
    expected = os.environ.get("SEED_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="SEED_TOKEN not configured on this service")
    if not secrets.compare_digest(x_seed_token, expected):
        raise HTTPException(status_code=401, detail="Invalid seed token")

    from . import db as _db
    import psycopg2
    import psycopg2.extras

    if not _db.DATABASE_URL:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")

    results: dict = {"schema_created": False, "run_id": None, "candidates_inserted": 0,
                     "skipped": False, "error": None}
    try:
        conn = psycopg2.connect(_db.DATABASE_URL, connect_timeout=10)
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Create schema
        for stmt in [s.strip() for s in _DDL_SCHEMA.split(";") if s.strip()]:
            cur.execute(stmt)
        conn.commit()
        results["schema_created"] = True

        # Idempotency check
        cur.execute("SELECT id FROM ultra_scan_runs WHERE nasdaq_batch=%s LIMIT 1", (_SEED_MARKER,))
        existing = cur.fetchone()
        if existing:
            results["skipped"] = True
            results["run_id"] = existing["id"]
            cur.close(); conn.close()
            return results

        # Flip existing is_latest
        cur.execute("UPDATE ultra_scan_runs SET is_latest=FALSE WHERE universe='sp500' AND tf='1d'")

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        cur.execute(
            """INSERT INTO ultra_scan_runs
               (universe, tf, nasdaq_batch, status, is_latest, total_candidates,
                sources_json, warnings_json, started_at, finished_at)
               VALUES ('sp500','1d',%s,'completed',TRUE,100,%s,%s,%s,%s)
               RETURNING id""",
            (_SEED_MARKER, json.dumps({"source":"seed_endpoint"}), json.dumps([]), now, now),
        )
        run_id = cur.fetchone()["id"]

        rows = [
            (run_id, t, float(c["ultra_score"]), json.dumps(c))
            for i, t in enumerate(_SEED_TICKERS)
            for c in [_synthetic_row(i, t)]
        ]
        psycopg2.extras.execute_batch(
            cur,
            "INSERT INTO ultra_scan_candidates (scan_run_id, ticker, ultra_score, row_json) "
            "VALUES (%s,%s,%s,%s)",
            rows, page_size=100,
        )
        conn.commit()
        cur.close(); conn.close()

        results["run_id"] = run_id
        results["candidates_inserted"] = len(rows)
        log.info("Seed complete: run_id=%s candidates=%s", run_id, len(rows))
        return results

    except Exception as exc:
        log.exception("admin_seed error")
        results["error"] = type(exc).__name__
        return JSONResponse(status_code=500, content=results)

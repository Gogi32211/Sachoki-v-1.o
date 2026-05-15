"""
Chart Observations API v2 — auto-fill from stock_stat (or live fallback)
User enters only: ticker + date → system prefills everything → user confirms.

Primary source: stock_stat table (populated after Run Stock Stat).
Fallback: live bar_signals computation for the single ticker — works for any
universe/ticker without needing a full scan first.
"""
import csv
import glob
import logging
import os
import requests as _requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import date, timedelta

from db import get_db, USE_PG

log = logging.getLogger(__name__)

router = APIRouter(prefix="/obs", tags=["chart_observations"])


# ── Auto-fill lookup ─────────────────────────────────────────────────────────

@router.get("/prefill")
def prefill(ticker: str, obs_date: str):
    """
    Lookup ticker+date from stock_stat table → return prefilled observation data.
    stock_stat table must exist in the DB (imported from CSV on Railway).
    """
    t = ticker.upper()

    select_cols = """
        ticker, date, T, Z, L, F, G, B,
        turbo_score, rtb_total, rtb_phase,
        beta_score, beta_zone, sweet_spot_active,
        signal_score, last_price,
        ultra_score, ultra_score_band_v2, ultra_score_priority,
        ultra_score_reasons
    """

    with get_db() as db:
        try:
            db.execute(
                f"SELECT {select_cols} FROM stock_stat "
                "WHERE ticker=? AND date=? LIMIT 1",
                (t, obs_date),
            )
            row = db.fetchone()

            if not row:
                db.execute(
                    f"SELECT {select_cols} FROM stock_stat "
                    "WHERE ticker=? AND date <= ? "
                    "ORDER BY date DESC LIMIT 1",
                    (t, obs_date),
                )
                row = db.fetchone()
        except Exception as exc:
            msg = str(exc).lower()
            if "stock_stat" in msg and ("does not exist" in msg or "no such table" in msg):
                raise HTTPException(
                    503,
                    "stock_stat data not loaded yet — import the scanner CSV into the DB before using Chart Observations.",
                )
            raise HTTPException(500, f"stock_stat query failed: {exc}")

        if not row:
            return _prefill_from_live(t, obs_date)

        db.execute(
            "SELECT date, T, Z, turbo_score, beta_score, ultra_score "
            "FROM stock_stat WHERE ticker=? AND date < ? "
            "ORDER BY date DESC LIMIT 3",
            (t, row['date']),
        )
        prev_bars = db.fetchall()

    return _build_response(t, row, prev_bars)


def _build_response(ticker: str, row: dict, prev_bars: list) -> dict:
    return {
        "ticker":          ticker,
        "obs_date":        str(row.get('date') or row.get('obs_date', '')),
        "t_signal":        row.get('T') or row.get('t'),
        "z_prev_1":        (prev_bars[0].get('Z') or prev_bars[0].get('z')) if len(prev_bars) > 0 else None,
        "z_prev_2":        (prev_bars[1].get('Z') or prev_bars[1].get('z')) if len(prev_bars) > 1 else None,
        "t_prev_1":        (prev_bars[0].get('T') or prev_bars[0].get('t')) if len(prev_bars) > 0 else None,
        "l_signal":        row.get('L') or row.get('l'),
        "f_signal":        row.get('F') or row.get('f'),
        "gog_signal":      row.get('G') or row.get('g'),
        "turbo_score":     row.get('turbo_score'),
        "rtb_total":       row.get('rtb_total'),
        "rtb_phase":       row.get('rtb_phase'),
        "beta_score":      row.get('beta_score'),
        "beta_zone":       row.get('beta_zone'),
        "sweet_spot":      bool(row.get('sweet_spot_active')) if row.get('sweet_spot_active') is not None else False,
        "score_at":        row.get('ultra_score'),
        "entry_price":     row.get('last_price') or row.get('close'),
        "ultra_band":      row.get('ultra_score_band_v2'),
        "signal_reasons":  row.get('ultra_score_reasons'),
        "sequence_label":  _build_sequence(prev_bars, row),
        "score_before":    prev_bars[0].get('ultra_score') if prev_bars else None,
        "source":          row.get("_source", "stock_stat"),
    }


def _prefill_from_live(ticker: str, obs_date: str) -> dict:
    """Live fallback: compute signals for a single ticker via bar_signals endpoint.

    Called when the ticker/date is not in the stock_stat table. Slower (~2–4s)
    but works for any universe without needing a full scan first.
    """
    port = int(os.environ.get("PORT", 8080))
    bars = 200
    try:
        resp = _requests.get(
            f"http://localhost:{port}/api/bar_signals/{ticker}",
            params={"tf": "1d", "bars": bars, "universe": "nasdaq"},
            timeout=30,
        )
        resp.raise_for_status()
        bar_list: list[dict] = resp.json()
    except Exception as exc:
        log.warning("live fallback failed for %s/%s: %s", ticker, obs_date, exc)
        raise HTTPException(404, f"No data for {ticker} on {obs_date}")

    if not bar_list:
        raise HTTPException(404, f"No data for {ticker} on {obs_date}")

    # Find requested date (exact or nearest before)
    target = obs_date
    hit = None
    for b in reversed(bar_list):
        d = str(b.get("date", ""))[:10]
        if d <= target:
            hit = b
            break

    if hit is None:
        raise HTTPException(404, f"No data for {ticker} on {obs_date}")

    # Build a row dict matching stock_stat schema
    tz_raw = hit.get("tz") or ""
    row = {
        "date":             str(hit.get("date", ""))[:10],
        "T":                tz_raw if tz_raw.startswith("T") else None,
        "Z":                tz_raw if tz_raw.startswith("Z") else None,
        "L":                " ".join(hit.get("l") or []) or None,
        "F":                " ".join(hit.get("f") or []) or None,
        "G":                " ".join(hit.get("g") or []) or None,
        "B":                " ".join(hit.get("b") or []) or None,
        "turbo_score":      hit.get("turbo_score"),
        "rtb_total":        hit.get("rtb_total"),
        "rtb_phase":        hit.get("rtb_phase"),
        "beta_score":       hit.get("beta_score"),
        "beta_zone":        hit.get("beta_zone"),
        "sweet_spot_active": hit.get("sweet_spot_active"),
        "signal_score":     hit.get("signal_score"),
        "close":            hit.get("close"),
        "ultra_score":      None,
        "ultra_score_band_v2": None,
        "ultra_score_priority": None,
        "ultra_score_reasons": None,
        "_source":          "live",
    }

    # Build 3 previous bars for sequence
    hit_idx = next(
        (i for i, b in enumerate(bar_list) if str(b.get("date", ""))[:10] == row["date"]),
        len(bar_list) - 1,
    )
    prev_bars = []
    for pb in reversed(bar_list[max(0, hit_idx - 3): hit_idx]):
        ptz = pb.get("tz") or ""
        prev_bars.append({
            "T":           ptz if ptz.startswith("T") else None,
            "Z":           ptz if ptz.startswith("Z") else None,
            "turbo_score": pb.get("turbo_score"),
            "beta_score":  pb.get("beta_score"),
            "ultra_score": None,
        })

    log.info("live prefill: %s/%s turbo=%s tz=%s", ticker, obs_date, row["turbo_score"], tz_raw)
    return _build_response(ticker, row, prev_bars)


def _build_sequence(prev_bars, current):
    """Auto-build sequence string like Z2|Z6→T4."""
    def _sig(d, *keys):
        for k in keys:
            v = d.get(k)
            if v:
                return v
        return ''
    parts = []
    for b in reversed(prev_bars[:2]):
        sig = _sig(b, 'Z', 'z') or _sig(b, 'T', 't')
        if sig:
            parts.append(sig)
    cur_sig = _sig(current, 'T', 't') or _sig(current, 'Z', 'z')
    if cur_sig:
        parts.append(cur_sig)
    return '|'.join(parts) if parts else None


# ── Save observation ─────────────────────────────────────────────────────────

class ObsConfirm(BaseModel):
    obs_date:        str
    ticker:          str
    exchange:        str            = "NQ"
    # auto-filled (from prefill)
    t_signal:        Optional[str]  = None
    z_prev_1:        Optional[str]  = None
    z_prev_2:        Optional[str]  = None
    t_prev_1:        Optional[str]  = None
    sequence_label:  Optional[str]  = None
    l_signal:        Optional[str]  = None
    gog_signal:      Optional[str]  = None
    f_signal:        Optional[str]  = None
    lvbo_present:    bool           = False
    eb_reversal:     bool           = False
    vbo_present:     bool           = False
    score_before:    Optional[int]  = None
    score_at:        Optional[int]  = None
    turbo_score:     Optional[int]  = None
    rtb_phase:       Optional[str]  = None
    rtb_total:       Optional[int]  = None
    beta_zone:       Optional[str]  = None
    sweet_spot:      bool           = False
    entry_price:     Optional[float] = None
    # user-supplied:
    k_signal_match:  Optional[str]  = None   # K1..K11 or NONE
    k_fired:         bool           = False
    entry_quality:   Optional[str]  = None   # PERFECT/GOOD/OK/BAD
    notes:           Optional[str]  = None


@router.post("/save")
def save_observation(obs: ObsConfirm):
    delta = None
    if obs.score_at is not None and obs.score_before is not None:
        delta = obs.score_at - obs.score_before

    sql = """
        INSERT INTO chart_observations
        (obs_date, ticker, exchange,
         t_signal, z_prev_1, z_prev_2, t_prev_1, sequence_label,
         l_signal, gog_signal, f_signal,
         lvbo_present, eb_reversal, vbo_present,
         score_before, score_at, score_delta, turbo_score,
         rtb_phase, rtb_total, beta_zone, sweet_spot,
         entry_price, k_signal_match, k_fired, entry_quality, notes)
        VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (obs_date, ticker, t_signal)
        DO UPDATE SET k_signal_match=EXCLUDED.k_signal_match,
                      entry_quality=EXCLUDED.entry_quality,
                      k_fired=EXCLUDED.k_fired,
                      notes=EXCLUDED.notes,
                      updated_at=CURRENT_TIMESTAMP
    """
    params = (
        obs.obs_date, obs.ticker.upper(), obs.exchange,
        obs.t_signal, obs.z_prev_1, obs.z_prev_2, obs.t_prev_1, obs.sequence_label,
        obs.l_signal, obs.gog_signal, obs.f_signal,
        obs.lvbo_present, obs.eb_reversal, obs.vbo_present,
        obs.score_before, obs.score_at, delta, obs.turbo_score,
        obs.rtb_phase, obs.rtb_total, obs.beta_zone, obs.sweet_spot,
        obs.entry_price, obs.k_signal_match, obs.k_fired,
        obs.entry_quality, obs.notes,
    )
    with get_db() as db:
        db.execute(sql, params)
        db.commit()
    return {"status": "saved", "ticker": obs.ticker.upper()}


# ── Auto-update results from paper_portfolio ─────────────────────────────────

@router.post("/sync-results")
def sync_results_from_portfolio():
    """Auto-populate result_5d/10d from paper_portfolio closed trades."""
    with get_db() as db:
        db.execute("""
            UPDATE chart_observations
            SET result_5d  = pp.realized_return_p,
                result_10d = pp.realized_return_p,
                result_outcome = CASE
                    WHEN pp.realized_return_p > 2  THEN 'WIN'
                    WHEN pp.realized_return_p < -3 THEN 'LOSS'
                    ELSE 'NEUTRAL'
                END,
                updated_at = CURRENT_TIMESTAMP
            FROM paper_portfolio pp
            WHERE chart_observations.ticker   = pp.ticker
              AND chart_observations.obs_date = pp.signal_date
              AND pp.status    = 'CLOSED'
              AND chart_observations.result_10d IS NULL
        """)
        db.commit()
    return {"synced": "ok"}


# ── Stats & calibration ──────────────────────────────────────────────────────

@router.get("/stats")
def get_stats(days: int = 180):
    cutoff = str(date.today() - timedelta(days=days))
    with get_db() as db:
        db.execute("""
            SELECT t_signal, sequence_label, k_signal_match,
                   COUNT(*) as n,
                   ROUND(AVG(result_10d)::numeric, 2) as avg10d,
                   ROUND(AVG(score_delta)::numeric, 1) as avg_score_jump,
                   ROUND(100.0 * COUNT(CASE WHEN result_outcome='WIN' THEN 1 END)
                         / NULLIF(COUNT(result_outcome), 0), 1) as win_rate
            FROM chart_observations
            WHERE obs_date >= ?
            GROUP BY t_signal, sequence_label, k_signal_match
            ORDER BY avg10d DESC NULLS LAST
        """, (cutoff,))
        data = db.fetchall()
    return {"stats": data}


# ── stock_stat CSV → DB import ───────────────────────────────────────────────

_STOCK_STAT_DIR = "stock_stat_output"

_SS_COLUMNS = [
    "ticker", "date", "t", "z", "l", "f", "g", "b",
    "turbo_score", "rtb_total", "rtb_phase",
    "beta_score", "beta_zone", "sweet_spot_active",
    "signal_score", "last_price",
    "ultra_score", "ultra_score_band_v2", "ultra_score_priority",
    "ultra_score_reasons",
]

# CSV-header → DB-column (CSV writes Z/T/L/F/G/B uppercase, close is the price)
_CSV_TO_DB = {
    "Z": "z", "T": "t", "L": "l", "F": "f", "G": "g", "B": "b",
    "close": "last_price",
}


def _coerce_int(v):
    if v in (None, "", "NaN", "nan"): return None
    try: return int(float(v))
    except (TypeError, ValueError): return None


def _coerce_float(v):
    if v in (None, "", "NaN", "nan"): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _coerce_bool(v):
    if v in (None, ""): return None
    s = str(v).strip().lower()
    if s in ("1", "true", "t", "yes", "y"):  return True
    if s in ("0", "false", "f", "no", "n"):  return False
    return None


_INT_COLS  = {"turbo_score", "rtb_total", "beta_score", "signal_score", "ultra_score"}
_REAL_COLS = {"last_price"}
_BOOL_COLS = {"sweet_spot_active"}


def _row_for_db(csv_row: dict) -> dict | None:
    """Project a CSV row to the stock_stat schema. Returns None if ticker/date missing."""
    out: dict = {}
    for db_col in _SS_COLUMNS:
        # find source value: direct match, uppercase alias, or csv→db map
        val = csv_row.get(db_col)
        if val is None:
            # check reverse map: e.g. db 'z' ← csv 'Z'
            for csv_k, mapped in _CSV_TO_DB.items():
                if mapped == db_col:
                    val = csv_row.get(csv_k)
                    if val is not None:
                        break
        if db_col in _INT_COLS:
            out[db_col] = _coerce_int(val)
        elif db_col in _REAL_COLS:
            out[db_col] = _coerce_float(val)
        elif db_col in _BOOL_COLS:
            out[db_col] = _coerce_bool(val)
        else:
            out[db_col] = val if val not in ("", "NaN", "nan") else None
    if not out.get("ticker") or not out.get("date"):
        return None
    return out


def _latest_stock_stat_csv() -> str | None:
    if not os.path.isdir(_STOCK_STAT_DIR):
        return None
    files = sorted(
        glob.glob(os.path.join(_STOCK_STAT_DIR, "stock_stat_*.csv")),
        key=os.path.getmtime,
        reverse=True,
    )
    return files[0] if files else None


def import_stock_stat_csv(csv_path: str | None = None) -> dict:
    """Bulk-upsert rows from the latest stock_stat CSV into the stock_stat table.

    Safe to call from background tasks (e.g. after /api/stock-stat/trigger completes).
    """
    path = csv_path or _latest_stock_stat_csv()
    if not path or not os.path.isfile(path):
        return {"status": "no_csv", "path": path, "rows": 0}

    cols = _SS_COLUMNS
    placeholders = ",".join(["?"] * len(cols))
    col_list = ",".join(cols)
    update_set = ",".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in ("ticker", "date"))

    if USE_PG:
        sql = (
            f"INSERT INTO stock_stat ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT (ticker, date) DO UPDATE SET {update_set}"
        )
    else:
        sql = (
            f"INSERT INTO stock_stat ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT (ticker, date) DO UPDATE SET {update_set}"
        )

    inserted = 0
    skipped  = 0
    batch: list[tuple] = []
    BATCH = 500

    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        with get_db() as db:
            for csv_row in reader:
                row = _row_for_db(csv_row)
                if row is None:
                    skipped += 1
                    continue
                batch.append(tuple(row[c] for c in cols))
                if len(batch) >= BATCH:
                    db.executemany(sql, batch)
                    inserted += len(batch)
                    batch.clear()
            if batch:
                db.executemany(sql, batch)
                inserted += len(batch)
            db.commit()

    log.info("stock_stat import: %d rows upserted, %d skipped, src=%s", inserted, skipped, path)
    return {"status": "ok", "path": path, "rows": inserted, "skipped": skipped}


@router.post("/import-stock-stat")
def api_import_stock_stat(path: Optional[str] = None):
    """Import the latest stock_stat CSV (or a specified path) into the stock_stat table."""
    try:
        return import_stock_stat_csv(path)
    except Exception as exc:
        log.error("stock_stat import failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"stock_stat import failed: {exc}")


@router.get("/recent")
def get_recent(limit: int = 50):
    """Recent observations for review."""
    with get_db() as db:
        db.execute("""
            SELECT id, obs_date, ticker, t_signal, sequence_label,
                   k_signal_match, entry_quality, k_fired,
                   score_before, score_at, score_delta,
                   result_outcome, result_10d, notes
            FROM chart_observations
            ORDER BY obs_date DESC, id DESC
            LIMIT ?
        """, (limit,))
        data = db.fetchall()
    return {"observations": data}

"""
Paper Portfolio API — Sachoki Screener
FastAPI endpoints for paper trading tracker

Endpoints:
  POST /portfolio/entry        — add new signal entries
  POST /portfolio/entry-price  — set next-day open price (entry)
  POST /portfolio/daily-prices — upsert daily OHLCV
  POST /portfolio/daily-check  — check TP/SL/exit for open positions
  GET  /portfolio              — list all entries (filterable)
  GET  /portfolio/stats        — performance summary
  GET  /portfolio/open         — open positions only
  GET  /portfolio/export       — closed trades for replay analytics
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import date, timedelta

from db import get_db, USE_PG

router = APIRouter(prefix="/portfolio", tags=["paper_portfolio"])

# ── Models ──────────────────────────────────────────────────────────────────

class SignalEntry(BaseModel):
    signal_date:       date
    ticker:            str
    exchange:          str            = "NQ"
    ultra_score:       Optional[int]  = None
    ultra_band:        Optional[str]  = None
    ultra_priority:    Optional[str]  = None
    beta_score:        Optional[int]  = None
    beta_zone:         Optional[str]  = None
    tz_sig:            Optional[str]  = None
    turbo_score:       Optional[int]  = None
    rtb_total:         Optional[int]  = None
    rtb_phase:         Optional[str]  = None
    sweet_spot:        bool           = False
    tier:              str            = "TIER1"
    signal_reasons:    Optional[str]  = None
    signal_price:      Optional[float]= None
    signal_change_pct: Optional[float]= None
    hold_days:         int            = 10

class EntryPrice(BaseModel):
    ticker:     str
    entry_date: date
    open_price: float

class DailyPrice(BaseModel):
    ticker:     str
    price_date: date
    open:       float
    high:       float
    low:        float
    close:      float
    volume:     Optional[int] = None

# ── Routes ──────────────────────────────────────────────────────────────────

@router.post("/entry")
def add_entries(entries: list[SignalEntry]):
    """Add new daily signal picks to portfolio."""
    return _insert_entries([e.model_dump() for e in entries])


# ── Helpers ─────────────────────────────────────────────────────────────────

def _insert_entries(rows: list[dict]) -> dict:
    """Insert raw signal-entry dicts into paper_portfolio. Skips duplicates."""
    inserted = 0
    skipped  = 0
    with get_db() as db:
        for e in rows:
            sig_date = e["signal_date"]
            db.execute(
                "SELECT id FROM paper_portfolio WHERE signal_date=? AND ticker=?",
                (str(sig_date), e["ticker"])
            )
            if db.fetchone():
                skipped += 1
                continue

            hold = int(e.get("hold_days") or 10)
            sd   = sig_date if isinstance(sig_date, date) else date.fromisoformat(str(sig_date))
            max_ex = sd + timedelta(days=hold + 2)
            db.execute("""
                INSERT INTO paper_portfolio
                (signal_date, ticker, exchange, ultra_score, ultra_band, ultra_priority,
                 beta_score, beta_zone, tz_sig, turbo_score, rtb_total, rtb_phase,
                 sweet_spot, tier, signal_reasons, signal_price, signal_change_pct,
                 hold_days, max_exit_date, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING')
            """, (
                str(sd), e["ticker"], e.get("exchange", "NQ"),
                e.get("ultra_score"), e.get("ultra_band"), e.get("ultra_priority"),
                e.get("beta_score"), e.get("beta_zone"), e.get("tz_sig"),
                e.get("turbo_score"), e.get("rtb_total"), e.get("rtb_phase"),
                bool(e.get("sweet_spot", False)), e.get("tier", "TIER1"),
                e.get("signal_reasons"),
                e.get("signal_price"), e.get("signal_change_pct"),
                hold, str(max_ex)
            ))
            inserted += 1
        db.commit()
    return {"inserted": inserted, "skipped": skipped}


def _profile_to_exchange(profile_name: str | None) -> str:
    pn = (profile_name or "").lower()
    return "NQ" if "nasdaq" in pn else "SP500"


@router.post("/scan-and-add")
def scan_and_add(
    universe: str = "sp500",
    tf: str = "1d",
    nasdaq_batch: str = "",
    tier1_score_min: int = 88,
    tier1_change_max: float = 5.0,
    tier1_top: int = 5,
    tier2_score_min: int = 64,
    tier2_change_max: float = 10.0,
    tier2_top: int = 10,
):
    """
    Pull cached ULTRA results and add today's TIER1/TIER2 picks to portfolio.

    TIER1: ultra_score >= 88, |change_pct| <= 5%, no cap reason, top 5 by score.
    TIER2: 64 <= ultra_score < 88, |change_pct| <= 10%,
           beta_zone in (WATCH, BUILDING, SHORT_WATCH), top 10 by beta_score.
    """
    try:
        from ultra_orchestrator import get_ultra_results
    except Exception as exc:
        raise HTTPException(500, f"ultra_orchestrator import failed: {exc}")

    payload = get_ultra_results(universe=universe, tf=tf, nasdaq_batch=nasdaq_batch)
    rows = payload.get("results") or []
    if not rows:
        return {"inserted": 0, "skipped": 0, "tier1": 0, "tier2": 0,
                "warning": "No ULTRA results cached. Run ULTRA scan first."}

    sig_date = str(date.today())
    valid_zones = {"WATCH", "BUILDING", "SHORT_WATCH"}

    def _row_to_entry(r: dict, tier: str) -> dict:
        beta_zone = str(r.get("beta_zone") or "")
        hold = 20 if beta_zone == "OPTIMAL" else 10
        return {
            "signal_date":       sig_date,
            "ticker":            str(r.get("ticker") or "").upper(),
            "exchange":          _profile_to_exchange(r.get("profile_name")),
            "ultra_score":       r.get("ultra_score"),
            "ultra_band":        r.get("ultra_score_band_v2") or r.get("ultra_score_band"),
            "ultra_priority":    r.get("ultra_score_priority"),
            "beta_score":        r.get("beta_score"),
            "beta_zone":         beta_zone or None,
            "tz_sig":            r.get("tz_sig"),
            "turbo_score":       r.get("turbo_score"),
            "rtb_total":         r.get("rtb_total"),
            "rtb_phase":         r.get("rtb_phase"),
            "sweet_spot":        bool(r.get("sweet_spot_active", False)),
            "tier":              tier,
            "signal_reasons":    r.get("ultra_score_reasons"),
            "signal_price":      r.get("last_price"),
            "signal_change_pct": r.get("change_pct"),
            "hold_days":         hold,
        }

    # TIER 1: high score, low daily move, no cap penalty
    t1 = [
        r for r in rows
        if (r.get("ultra_score") or 0) >= tier1_score_min
        and abs(float(r.get("change_pct") or 0)) <= tier1_change_max
        and not (r.get("ultra_score_cap_reason") or "").strip()
        and r.get("tz_sig") and str(r.get("tz_sig")).startswith("T")
    ]
    t1.sort(key=lambda r: (r.get("ultra_score") or 0), reverse=True)
    t1_picks = t1[:tier1_top]

    # TIER 2: mid score, valid beta zone
    t1_set = {p["ticker"] for p in t1_picks}
    t2 = [
        r for r in rows
        if tier2_score_min <= (r.get("ultra_score") or 0) < tier1_score_min
        and abs(float(r.get("change_pct") or 0)) <= tier2_change_max
        and str(r.get("beta_zone") or "") in valid_zones
        and r.get("tz_sig") and str(r.get("tz_sig")).startswith("T")
        and r.get("ticker") not in t1_set
    ]
    t2.sort(key=lambda r: (r.get("beta_score") or 0, r.get("ultra_score") or 0),
            reverse=True)
    t2_picks = t2[:tier2_top]

    entries = [_row_to_entry(r, "TIER1") for r in t1_picks] \
            + [_row_to_entry(r, "TIER2") for r in t2_picks]

    if not entries:
        return {"inserted": 0, "skipped": 0, "tier1": 0, "tier2": 0,
                "warning": "No tickers matched TIER1/TIER2 criteria."}

    res = _insert_entries(entries)
    res["tier1"] = len(t1_picks)
    res["tier2"] = len(t2_picks)
    res["signal_date"] = sig_date
    return res


@router.post("/entry-price")
def set_entry_price(prices: list[EntryPrice]):
    """Set next-day market open price. Calculates TP/SL and opens position."""
    updated = 0
    with get_db() as db:
        for p in prices:
            tp_par  = round(p.open_price * 1.20, 4)
            tp_wide = round(p.open_price * 1.15, 4)
            sl      = round(p.open_price * 0.95, 4)
            db.execute("""
                UPDATE paper_portfolio
                SET entry_date=?, entry_price=?,
                    tp_parabolic=?, tp_wide=?, sl_price=?,
                    status='OPEN', updated_at=CURRENT_TIMESTAMP
                WHERE ticker=? AND status='PENDING'
                  AND signal_date = (
                    SELECT MAX(signal_date) FROM paper_portfolio
                    WHERE ticker=? AND status='PENDING'
                  )
            """, (str(p.entry_date), p.open_price, tp_par, tp_wide, sl,
                  p.ticker, p.ticker))
            if USE_PG:
                updated += db._cur.rowcount
            else:
                updated += db._last.rowcount
        db.commit()
    return {"updated": updated}


@router.post("/daily-prices")
def upsert_daily_prices(prices: list[DailyPrice]):
    """Upsert daily OHLCV data for TP/SL monitoring."""
    with get_db() as db:
        for p in prices:
            db.execute("""
                INSERT INTO paper_daily_prices
                    (ticker, price_date, open, high, low, close, volume)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT (ticker, price_date)
                DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high,
                              low=EXCLUDED.low, close=EXCLUDED.close,
                              volume=EXCLUDED.volume
            """, (p.ticker, str(p.price_date),
                  p.open, p.high, p.low, p.close, p.volume))
        db.commit()
    return {"upserted": len(prices)}


@router.post("/daily-check")
def daily_check():
    """
    Run TP/SL/hold-end check for all OPEN positions.
    Call once per day after market close.
    """
    with get_db() as db:
        db.execute("""
            SELECT pp.id, pp.entry_price, pp.tp_parabolic, pp.tp_wide,
                   pp.sl_price, pp.max_exit_date,
                   pp.exit_date_p, pp.exit_date_w,
                   p.high as d_high, p.low as d_low, p.close as d_close,
                   p.price_date
            FROM paper_portfolio pp
            JOIN paper_daily_prices p ON p.ticker = pp.ticker
            WHERE pp.status = 'OPEN'
              AND p.price_date > pp.entry_date
              AND (pp.exit_date_p IS NULL OR pp.exit_date_w IS NULL)
            ORDER BY pp.id, p.price_date
        """)
        rows = db.fetchall()

    updates: dict = {}
    for r in rows:
        pid   = r['id']
        d     = r['price_date']
        high  = float(r['d_high'])
        low   = float(r['d_low'])
        close = float(r['d_close'])
        max_d = str(r['max_exit_date'])

        if pid not in updates:
            updates[pid] = {'p': None, 'w': None}

        tp_p = float(r['tp_parabolic']) if r['tp_parabolic'] else None
        tp_w = float(r['tp_wide'])      if r['tp_wide']      else None
        sl   = float(r['sl_price'])     if r['sl_price']     else None
        ep   = float(r['entry_price'])  if r['entry_price']  else None

        if tp_p and sl and ep and updates[pid]['p'] is None:
            if high >= tp_p:
                updates[pid]['p'] = (d, tp_p, 'TP',       round((tp_p  - ep)/ep*100, 4))
            elif low <= sl:
                updates[pid]['p'] = (d, sl,   'SL',       round((sl    - ep)/ep*100, 4))
            elif str(d) >= max_d:
                updates[pid]['p'] = (d, close, 'HOLD_END', round((close - ep)/ep*100, 4))

        if tp_w and sl and ep and updates[pid]['w'] is None:
            if high >= tp_w:
                updates[pid]['w'] = (d, tp_w, 'TP',       round((tp_w  - ep)/ep*100, 4))
            elif low <= sl:
                updates[pid]['w'] = (d, sl,   'SL',       round((sl    - ep)/ep*100, 4))
            elif str(d) >= max_d:
                updates[pid]['w'] = (d, close, 'HOLD_END', round((close - ep)/ep*100, 4))

    closed = 0
    with get_db() as db:
        for pid, ex in updates.items():
            if ex['p']:
                d, pr, rs, ret = ex['p']
                db.execute("""
                    UPDATE paper_portfolio
                    SET exit_date_p=?, exit_price_p=?, exit_reason_p=?,
                        realized_return_p=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (str(d), pr, rs, ret, pid))
            if ex['w']:
                d, pr, rs, ret = ex['w']
                db.execute("""
                    UPDATE paper_portfolio
                    SET exit_date_w=?, exit_price_w=?, exit_reason_w=?,
                        realized_return_w=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (str(d), pr, rs, ret, pid))
            if ex['p'] and ex['w']:
                db.execute(
                    "UPDATE paper_portfolio SET status='CLOSED',"
                    " updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
                closed += 1
        db.commit()

    return {"checked": len(updates), "closed": closed}


@router.get("/open")
def get_open():
    """All open positions."""
    with get_db() as db:
        db.execute("""
            SELECT ticker, exchange, signal_date, entry_date, entry_price,
                   tp_parabolic, tp_wide, sl_price, max_exit_date,
                   ultra_score, ultra_band, beta_zone, tz_sig, tier, sweet_spot
            FROM paper_portfolio
            WHERE status = 'OPEN'
            ORDER BY entry_date DESC, ultra_score DESC
        """)
        data = db.fetchall()
    return {"count": len(data), "positions": data}


@router.get("/stats")
def get_stats(days: int = 90):
    """Performance summary — both strategies + breakdown by zone/signal/tier."""
    cutoff = str(date.today() - timedelta(days=days))
    with get_db() as db:
        db.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN status='CLOSED' THEN 1 END) as closed,
                COUNT(CASE WHEN status='OPEN'   THEN 1 END) as open,
                ROUND(AVG(realized_return_p), 2) as avg_ret_p,
                ROUND(AVG(realized_return_w), 2) as avg_ret_w,
                COUNT(CASE WHEN exit_reason_p='TP' THEN 1 END) as tp_count_p,
                COUNT(CASE WHEN exit_reason_p='SL' THEN 1 END) as sl_count_p,
                COUNT(realized_return_p) as closed_p,
                COUNT(CASE WHEN exit_reason_w='TP' THEN 1 END) as tp_count_w,
                COUNT(CASE WHEN exit_reason_w='SL' THEN 1 END) as sl_count_w,
                COUNT(realized_return_w) as closed_w
            FROM paper_portfolio
            WHERE signal_date >= ?
        """, (cutoff,))
        row = db.fetchone() or {}

        # compute rates in Python (avoids NULLIF / cast differences)
        def pct(num, den):
            return round(100.0 * num / den, 1) if den else None

        overall = {
            "total":     row.get("total"),
            "closed":    row.get("closed"),
            "open":      row.get("open"),
            "avg_ret_p": row.get("avg_ret_p"),
            "avg_ret_w": row.get("avg_ret_w"),
            "tp_rate_p": pct(row.get("tp_count_p", 0), row.get("closed_p", 0)),
            "sl_rate_p": pct(row.get("sl_count_p", 0), row.get("closed_p", 0)),
            "tp_rate_w": pct(row.get("tp_count_w", 0), row.get("closed_w", 0)),
            "sl_rate_w": pct(row.get("sl_count_w", 0), row.get("closed_w", 0)),
        }

        db.execute("""
            SELECT beta_zone, COUNT(*) as n,
                   ROUND(AVG(realized_return_p), 2) as avg_p,
                   ROUND(AVG(realized_return_w), 2) as avg_w,
                   COUNT(CASE WHEN exit_reason_p='TP' THEN 1 END) as tp_count,
                   COUNT(realized_return_p) as closed_p
            FROM paper_portfolio
            WHERE status='CLOSED' AND signal_date >= ?
            GROUP BY beta_zone ORDER BY avg_p DESC
        """, (cutoff,))
        by_zone = [
            {**r, "tp_rate_p": pct(r.get("tp_count", 0), r.get("closed_p", 0))}
            for r in db.fetchall()
        ]

        db.execute("""
            SELECT tz_sig, COUNT(*) as n,
                   ROUND(AVG(realized_return_p), 2) as avg_p,
                   ROUND(AVG(realized_return_w), 2) as avg_w
            FROM paper_portfolio
            WHERE status='CLOSED' AND signal_date >= ?
            GROUP BY tz_sig ORDER BY avg_p DESC
        """, (cutoff,))
        by_signal = db.fetchall()

        db.execute("""
            SELECT tier, COUNT(*) as n,
                   ROUND(AVG(realized_return_p), 2) as avg_p,
                   ROUND(AVG(realized_return_w), 2) as avg_w
            FROM paper_portfolio
            WHERE status='CLOSED' AND signal_date >= ?
            GROUP BY tier ORDER BY avg_p DESC
        """, (cutoff,))
        by_tier = db.fetchall()

    return {
        "period_days": days,
        "overall":    overall,
        "by_zone":    by_zone,
        "by_signal":  by_signal,
        "by_tier":    by_tier,
    }


@router.get("/export")
def export_for_replay(days: int = 90):
    """Export closed trades compatible with replay analytics."""
    cutoff = str(date.today() - timedelta(days=days))
    with get_db() as db:
        db.execute("""
            SELECT signal_date, ticker, exchange, beta_zone, beta_score, tz_sig,
                   ultra_score, ultra_band, tier, sweet_spot,
                   turbo_score, rtb_total, rtb_phase,
                   entry_price, hold_days,
                   realized_return_p, exit_reason_p,
                   realized_return_w, exit_reason_w
            FROM paper_portfolio
            WHERE status='CLOSED' AND signal_date >= ?
            ORDER BY signal_date
        """, (cutoff,))
        data = db.fetchall()
    return {"count": len(data), "trades": data}


@router.get("/")
def get_portfolio(
    status:    Optional[str] = None,
    beta_zone: Optional[str] = None,
    tz_sig:    Optional[str] = None,
    tier:      Optional[str] = None,
    exchange:  Optional[str] = None,
    days:      int           = 30,
):
    """List portfolio entries with optional filters."""
    cutoff = str(date.today() - timedelta(days=days))
    conditions = ["signal_date >= ?"]
    params: list = [cutoff]
    if status:    conditions.append("status=?");    params.append(status)
    if beta_zone: conditions.append("beta_zone=?"); params.append(beta_zone)
    if tz_sig:    conditions.append("tz_sig=?");    params.append(tz_sig)
    if tier:      conditions.append("tier=?");      params.append(tier)
    if exchange:  conditions.append("exchange=?");  params.append(exchange)

    sql = ("SELECT * FROM paper_portfolio WHERE "
           + " AND ".join(conditions)
           + " ORDER BY signal_date DESC, ultra_score DESC")
    with get_db() as db:
        db.execute(sql, params)
        data = db.fetchall()
    return {"count": len(data), "entries": data}

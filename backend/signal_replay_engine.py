"""
signal_replay_engine.py — orchestrator for the Signal Replay / Research engine.

Hybrid storage pipeline:
  1. Create signal_replay_runs row (status='running').
  2. Resolve universe tickers.
  3. Parallel ticker fetch+extract (ThreadPoolExecutor, 8 workers).
  4. Accumulate events + outcomes in memory with Python-assigned IDs.
  5. Write events.parquet + outcomes.parquet (zstd) to run directory on disk.
  6. Aggregate statistics from Python lists (signal, pattern, filter-impact, combo).
  7. Write signal_stats.parquet, pattern_stats.parquet, filter_impact.parquet.
  8. Write research_bundle.json (combined stats for quick analytics loading).
  9. Register all artifacts in replay_artifacts DB table.
 10. Mark run completed.

Concurrency:
  - One run at a time (_state["running"] is the lock).
  - FastAPI BackgroundTasks invokes run_signal_replay(run_id, payload).
  - HTTP polls get_state() for live progress.

Memory note:
  All events + outcomes stay in RAM until step 5. For large date-range runs this
  can be several hundred MB. If that becomes an issue, flush events incrementally
  using pyarrow.parquet.ParquetWriter in append mode.
"""
from __future__ import annotations
import json
import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any

from db import get_db, USE_PG

log = logging.getLogger(__name__)

# Module-level state (single concurrent run)
_state: dict[str, Any] = {
    "running":              False,
    "run_id":               None,
    "status":               "idle",
    "mode":                 None,
    "universe":             None,
    "current_date":         None,
    "days_total":           0,
    "days_completed":       0,
    "symbols_total":        0,
    "symbols_completed":    0,
    "events_found":         0,
    "outcomes_computed":    0,
    "statistics_rows":      0,
    "pattern_rows":         0,
    "filter_impact_rows":   0,
    "combo_rows":           0,
    "unique_symbols":       0,
    "unique_symbol_dates":  0,
    "unique_tz_events":     0,
    "unique_combo_events":  0,
    "started_at":           None,
    "elapsed_secs":         0,
    "error":                None,
    "stop_requested":       False,
    "pause_requested":      False,
    "lookback_bars":        500,
    "context_quality":      None,
    "run_warnings":         [],
}


def get_state() -> dict:
    return dict(_state)


def _set(**kv) -> None:
    _state.update(kv)


def request_stop() -> None:
    _state["stop_requested"] = True


def request_pause() -> None:
    _state["pause_requested"] = True


def request_resume() -> None:
    _state["pause_requested"] = False


def _ph() -> str:
    return "%s" if USE_PG else "?"


# ─── Context quality ─────────────────────────────────────────────────────────

_ALLOWED_LOOKBACK = {30, 100, 250, 500, 1000}

# Bars needed beyond context window to compute outcomes + EMA warmup
_OUTCOME_FORWARD_BARS = 22   # max forward horizon used by compute_outcomes
_WARMUP_BARS          = 200  # EMA200 needs ~200 bars of warmup
_BUFFER_BARS          = 20   # small safety buffer


def _context_quality(lookback_bars: int) -> str:
    """Classify statistical reliability based on the user's context window choice.

    30-bar mode is PARTIAL (not LIMITED): the engine fetches a wider window
    so EMA50/89/200 and outcomes have full coverage; only the statistical
    sample size is smaller.
    """
    if lookback_bars >= 250:
        return "FULL"
    if lookback_bars >= 30:
        return "PARTIAL"
    return "LIMITED"


def _compute_fetch_bars(context_lookback_bars: int) -> int:
    """Total bars to fetch from the API for a given context window.

    Separates the user's context window (for signal detection + quality label)
    from the raw bar count needed to compute outcomes and indicator warmup.
    For 30-bar mode: fetch 272 bars so EMA200 and 22-bar outcomes both work.
    """
    return context_lookback_bars + _OUTCOME_FORWARD_BARS + _WARMUP_BARS + _BUFFER_BARS


def _apply_context_quality(rows: list[dict], cq: str) -> list[dict]:
    """Tag each row with context_quality; cap confidence to MEDIUM for LIMITED runs."""
    for row in rows:
        row["context_quality"] = cq
        if cq == "LIMITED" and row.get("confidence_label") == "HIGH":
            row["confidence_label"] = "MEDIUM"
    return rows


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _insert_run(payload: dict) -> int:
    cols = [
        "status", "mode", "universe", "timeframe", "as_of_date",
        "start_date", "end_date", "event_scope", "min_price",
        "min_volume", "min_dollar_volume", "benchmark_symbol",
        "settings_json", "storage_mode",
    ]
    vals = [
        "running", payload["mode"], payload["universe"], "1d",
        payload.get("as_of_date"), payload.get("start_date"),
        payload.get("end_date"), payload.get("event_scope", "all_signals"),
        payload.get("min_price"), payload.get("min_volume"),
        payload.get("min_dollar_volume"), payload.get("benchmark_symbol", "QQQ"),
        json.dumps(payload, default=str), "parquet",
    ]
    ph = _ph()
    sql = (f"INSERT INTO signal_replay_runs ({', '.join(cols)}) "
           f"VALUES ({', '.join([ph]*len(cols))}) "
           f"{'RETURNING id' if USE_PG else ''}")
    with get_db() as db:
        if USE_PG:
            db.execute(sql, vals)
            row = db.fetchone()
            run_id = row["id"] if isinstance(row, dict) else row[0]
        else:
            cur = db.execute(sql, vals)
            run_id = cur.lastrowid
        db.commit()
    return int(run_id)


def _update_run(run_id: int, **fields) -> None:
    if not fields:
        return
    ph = _ph()
    sets = ", ".join(f"{k}={ph}" for k in fields.keys())
    now_expr = "NOW()" if USE_PG else "datetime('now')"
    sets += f", updated_at={now_expr}"
    sql = f"UPDATE signal_replay_runs SET {sets} WHERE id={ph}"
    vals = list(fields.values()) + [run_id]
    try:
        with get_db() as db:
            db.execute(sql, vals)
            db.commit()
    except Exception as exc:
        log.warning("signal_replay_runs update failed: %s", exc)


def _finalize_finished_at(run_id: int) -> None:
    ph = _ph()
    expr = "NOW()" if USE_PG else "datetime('now')"
    sql = f"UPDATE signal_replay_runs SET finished_at={expr} WHERE id={ph}"
    try:
        with get_db() as db:
            db.execute(sql, [run_id])
            db.commit()
    except Exception:
        pass


def _get_run_row(run_id: int) -> dict | None:
    ph = _ph()
    try:
        with get_db() as db:
            db.execute(f"SELECT * FROM signal_replay_runs WHERE id={ph}", [run_id])
            row = db.fetchone()
        return row
    except Exception:
        return None


# ─── Date list resolution ─────────────────────────────────────────────────────

def _normalize_date(d: Any) -> str | None:
    if d is None:
        return None
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    s = str(d)[:10]
    return s if s else None


def _resolve_scan_dates(payload: dict, sample_bars: list[dict]) -> list[str]:
    mode = payload["mode"]
    today_str = date.today().strftime("%Y-%m-%d")

    if mode == "single_day":
        return [_normalize_date(payload["as_of_date"])]

    all_bar_dates = sorted(set(
        d for b in sample_bars
        if (d := _normalize_date(b.get("date")))
    ))

    if mode == "last_n_days":
        n = max(1, int(payload.get("lookback_days") or 20))
        return all_bar_dates[-n:] if len(all_bar_dates) >= n else all_bar_dates

    if mode == "ytd":
        start = f"{date.today().year}-01-01"
        return [d for d in all_bar_dates if start <= d <= today_str]

    # date_range
    start = _normalize_date(payload.get("start_date"))
    end   = _normalize_date(payload.get("end_date"))
    if not start or not end:
        return []
    return [d for d in all_bar_dates if start <= d <= end]


# ─── Per-ticker worker (pure compute, no DB) ──────────────────────────────────

_WORKERS = 8


def _process_ticker_for_replay(
    ticker: str,
    *,
    scan_dates: list[str],
    is_gt5: bool,
    min_price: float | None,
    min_volume: int | None,
    min_dollar_volume: float | None,
    fetch_bars: int,
    run_id: int,
    universe: str,
    spy_bars: list[dict],
    qqq_bars: list[dict],
) -> tuple[list[dict], list[tuple[int, list[dict]]], dict]:
    """Fetch bars, extract events, compute outcomes for one ticker. No DB access.

    fetch_bars is the total bar count requested from the API — always large enough
    to include outcome forward bars + EMA warmup, regardless of context_lookback_bars.

    Returns (event_rows, [(event_offset_within_returned_list, outcomes), ...], counters).
    """
    from main import api_bar_signals
    from signal_event_extractor import extract_events
    from signal_outcome_engine import compute_outcomes

    counters = {
        "unique_symbol_dates": set(),
        "tz_events":           0,
        "combo_events":        0,
    }

    try:
        bars = api_bar_signals(ticker, "1d", fetch_bars)
    except Exception as fetch_err:
        log.debug("signal_replay: bar fetch failed for %s: %s", ticker, fetch_err)
        return ([], [], counters)

    if not bars or len(bars) < 60:
        return ([], [], counters)

    date_to_idx: dict[str, int] = {}
    for i, b in enumerate(bars):
        d = _normalize_date(b.get("date"))
        if d:
            date_to_idx[d] = i

    event_rows: list[dict] = []
    outcome_offsets: list[tuple[int, list[dict]]] = []

    for scan_date in scan_dates:
        idx = date_to_idx.get(scan_date)
        if idx is None or idx < 30:
            continue

        if is_gt5:
            close = bars[idx].get("close")
            if close is None or close < 5:
                continue

        bar_close  = float(bars[idx].get("close")  or 0)
        bar_volume = float(bars[idx].get("volume") or 0)
        if min_price is not None and bar_close < min_price:
            continue
        if min_volume is not None and bar_volume < min_volume:
            continue
        if min_dollar_volume is not None and (bar_close * bar_volume) < min_dollar_volume:
            continue

        events = extract_events(
            bars, idx,
            ticker=ticker, universe=universe, replay_run_id=run_id,
        )
        if not events:
            continue

        future_bars = bars[idx + 1 : idx + 22]
        spy_future  = _future_window(spy_bars, scan_date, 22)
        qqq_future  = _future_window(qqq_bars, scan_date, 22)

        base_offset = len(event_rows)
        for ev in events:
            event_rows.append(ev)
            counters["unique_symbol_dates"].add((ticker, scan_date))
            ev_sig  = ev.get("event_signal") or ""
            sig_fam = ev.get("event_signal_family") or ""
            if ev_sig.startswith(("T", "Z")):
                counters["tz_events"] += 1
            elif sig_fam in ("COMBO", "L", "F", "G", "B", "EMA"):
                counters["combo_events"] += 1

        for i, ev in enumerate(events):
            ocs = compute_outcomes(
                ev, future_bars,
                spy_future=spy_future, qqq_future=qqq_future,
                replay_run_id=run_id,
            )
            outcome_offsets.append((base_offset + i, ocs))

    return (event_rows, outcome_offsets, counters)


# ─── Main run loop ────────────────────────────────────────────────────────────

def run_signal_replay(run_id: int, payload: dict) -> None:
    """Background entrypoint. Updates _state + DB as it goes."""
    from scanner import get_universe_tickers
    from main import api_bar_signals
    from signal_statistics_engine import build_signal_statistics
    from signal_pattern_engine import build_pattern_statistics
    from signal_filter_impact_engine import build_filter_impact_statistics
    from signal_combo_engine import build_combo_statistics
    from replay_storage import (
        run_dir, write_parquet, write_json, register_artifact,
    )

    _set(running=True, run_id=run_id, status="running",
         mode=payload.get("mode"), universe=payload.get("universe"),
         current_date=None,
         days_total=0, days_completed=0,
         symbols_total=0, symbols_completed=0,
         events_found=0, outcomes_computed=0, statistics_rows=0,
         pattern_rows=0, filter_impact_rows=0, combo_rows=0,
         unique_symbols=0, unique_symbol_dates=0,
         unique_tz_events=0, unique_combo_events=0,
         lookback_bars=int(payload.get("lookback_bars") or 500),
         context_quality=None,
         run_warnings=[],
         started_at=time.time(), elapsed_secs=0, error=None)
    t0 = time.time()

    try:
        universe = payload["universe"]
        is_gt5 = universe == "nasdaq_gt5"
        scanner_universe = "nasdaq" if is_gt5 else universe
        tickers = list(get_universe_tickers(scanner_universe))
        log.info("signal_replay: %s universe → %d tickers", universe, len(tickers))
        _set(symbols_total=len(tickers))

        _lb = int(payload.get("lookback_bars") or 500)
        lookback_bars = _lb if _lb in _ALLOWED_LOOKBACK else 500
        fetch_bars    = _compute_fetch_bars(lookback_bars)
        cq = _context_quality(lookback_bars)
        _set(context_quality=cq, lookback_bars=lookback_bars)

        run_warnings: list[str] = []
        if lookback_bars < 100:
            run_warnings.append(
                "Limited context: EMA50/EMA89/EMA200 and long-sequence analytics "
                "may be unreliable or unavailable."
            )
        if lookback_bars == 30:
            run_warnings.append(
                "This replay uses only 30 bars of context (fast scan / debug mode). "
                "Outcomes and indicators are computed from a wider fetch window "
                f"({fetch_bars} bars total). Not for full statistical validation."
            )
        min_price         = payload.get("min_price")
        min_volume        = payload.get("min_volume")
        min_dollar_volume = payload.get("min_dollar_volume")

        try:
            spy_bars = api_bar_signals("SPY", "1d", fetch_bars)
        except Exception:
            spy_bars = []
        try:
            qqq_bars = api_bar_signals("QQQ", "1d", fetch_bars)
        except Exception:
            qqq_bars = []

        sample_bars = spy_bars or qqq_bars or []
        scan_dates  = _resolve_scan_dates(payload, sample_bars)
        _set(days_total=len(scan_dates))

        # ── Parallel phase: fetch + extract + compute outcomes ────────────────
        events_total: int               = 0
        all_events: list[dict]          = []
        all_outcomes: list[dict]        = []
        unique_symbols_set:      set[str]   = set()
        unique_symbol_dates_set: set[tuple] = set()
        unique_tz_events_count:  int        = 0
        unique_combo_events_count: int      = 0

        worker_kwargs = dict(
            scan_dates=scan_dates, is_gt5=is_gt5,
            min_price=min_price, min_volume=min_volume,
            min_dollar_volume=min_dollar_volume,
            fetch_bars=fetch_bars, run_id=run_id,
            universe=universe, spy_bars=spy_bars, qqq_bars=qqq_bars,
        )
        completed_symbols = 0
        log.info("signal_replay: starting parallel processing (%d workers)", _WORKERS)

        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            futures = {
                pool.submit(_process_ticker_for_replay, t, **worker_kwargs): t
                for t in tickers
            }

            for fut in as_completed(futures):
                while _state.get("pause_requested"):
                    _set(elapsed_secs=round(time.time() - t0, 1))
                    time.sleep(0.5)

                if _state.get("stop_requested"):
                    log.info("signal_replay: stop requested; cancelling pending futures")
                    for f in futures:
                        if not f.done():
                            f.cancel()
                    break

                ticker = futures[fut]
                try:
                    event_rows, outcome_offsets, counters = fut.result()
                except Exception as exc:
                    log.debug("signal_replay: %s worker raised: %s", ticker, exc)
                    event_rows, outcome_offsets, counters = [], [], {
                        "unique_symbol_dates": set(),
                        "tz_events": 0, "combo_events": 0,
                    }

                if event_rows:
                    # Assign sequential Python IDs (1-indexed, unique within this run)
                    base_id = events_total + 1
                    ev_ids  = list(range(base_id, base_id + len(event_rows)))
                    for i, ev in enumerate(event_rows):
                        all_events.append({**ev, "id": ev_ids[i], "context_quality": cq})
                    for offset, ocs in outcome_offsets:
                        if offset >= len(ev_ids):
                            continue
                        ev_id = ev_ids[offset]
                        for oc in ocs:
                            oc["signal_event_id"] = ev_id
                            all_outcomes.append(oc)
                    events_total += len(event_rows)
                    unique_symbols_set.add(ticker)

                unique_symbol_dates_set.update(counters.get("unique_symbol_dates") or set())
                unique_tz_events_count    += counters.get("tz_events", 0)
                unique_combo_events_count += counters.get("combo_events", 0)

                completed_symbols += 1
                _set(symbols_completed=completed_symbols,
                     events_found=events_total,
                     unique_symbols=len(unique_symbols_set),
                     unique_symbol_dates=len(unique_symbol_dates_set),
                     unique_tz_events=unique_tz_events_count,
                     unique_combo_events=unique_combo_events_count,
                     elapsed_secs=round(time.time() - t0, 1))

        outcomes_total = len(all_outcomes)
        _set(outcomes_computed=outcomes_total, days_completed=len(scan_dates),
             elapsed_secs=round(time.time() - t0, 1))

        # ── Write heavy artifacts to disk ─────────────────────────────────────
        rdir = run_dir(run_id)
        rdir.mkdir(parents=True, exist_ok=True)

        events_path = rdir / "events.parquet"
        n_events = write_parquet(events_path, all_events)
        log.info("signal_replay[%d]: wrote %d events → %s", run_id, n_events, events_path)

        outcomes_path = rdir / "outcomes.parquet"
        n_outcomes = write_parquet(outcomes_path, all_outcomes)
        log.info("signal_replay[%d]: wrote %d outcomes → %s", run_id, n_outcomes, outcomes_path)

        # ── Build stats from in-memory lists (existing engines unchanged) ─────
        events_for_stats = [{
            "id": ev["id"],
            "event_signal":        ev.get("event_signal"),
            "event_signal_family": ev.get("event_signal_family"),
            "event_signal_type":   ev.get("event_signal_type"),
            "event_direction":     ev.get("event_direction"),
            "role":                ev.get("role"),
            "matched_status":      ev.get("matched_status"),
            "score_bucket":        ev.get("score_bucket"),
            "symbol":              ev.get("symbol"),
            "scan_date":           ev.get("scan_date"),
        } for ev in all_events]

        stats_rows = build_signal_statistics(
            events_for_stats, all_outcomes, replay_run_id=run_id,
        )
        _set(statistics_rows=len(stats_rows), elapsed_secs=round(time.time() - t0, 1))

        events_for_patterns = [{
            "id":             ev["id"],
            "event_signal":   ev.get("event_signal"),
            "sequence_2bar":  ev.get("sequence_2bar"),
            "sequence_3bar":  ev.get("sequence_3bar"),
            "sequence_4bar":  ev.get("sequence_4bar"),
            "sequence_5bar":  ev.get("sequence_5bar"),
            "sequence_7bar":  ev.get("sequence_7bar"),
            "sequence_10bar": ev.get("sequence_10bar"),
        } for ev in all_events]

        pattern_rows = build_pattern_statistics(
            events_for_patterns, all_outcomes, replay_run_id=run_id,
        )
        _set(pattern_rows=len(pattern_rows), elapsed_secs=round(time.time() - t0, 1))

        events_for_filters = [{
            "id":                        ev["id"],
            "event_signal":              ev.get("event_signal"),
            "event_signal_family":       ev.get("event_signal_family"),
            "ema50_state":               ev.get("ema50_state"),
            "volume_bucket":             ev.get("volume_bucket"),
            "abr_category":              ev.get("abr_category"),
            "candle_color":              ev.get("candle_color"),
            "price_pos_20bar_bucket":    ev.get("price_pos_20bar_bucket"),
            "score_bucket":              ev.get("score_bucket"),
            "had_t_last_3d":             ev.get("had_t_last_3d"),
            "had_z_last_3d":             ev.get("had_z_last_3d"),
            "had_wlnbb_l_last_5d":       ev.get("had_wlnbb_l_last_5d"),
            "had_ema50_reclaim_last_5d": ev.get("had_ema50_reclaim_last_5d"),
            "had_volume_burst_last_5d":  ev.get("had_volume_burst_last_5d"),
        } for ev in all_events]

        filter_rows = build_filter_impact_statistics(
            events_for_filters, all_outcomes, replay_run_id=run_id,
        )
        _set(filter_impact_rows=len(filter_rows), elapsed_secs=round(time.time() - t0, 1))

        events_for_combos = [{
            "id":                      ev["id"],
            "event_signal":            ev.get("event_signal"),
            "event_signal_family":     ev.get("event_signal_family"),
            "sequence_4bar":           ev.get("sequence_4bar"),
            "abr_category":            ev.get("abr_category"),
            "ema50_state":             ev.get("ema50_state"),
            "had_wlnbb_l_last_5d":     ev.get("had_wlnbb_l_last_5d"),
            "price_pos_20bar_bucket":  ev.get("price_pos_20bar_bucket"),
            "score_bucket":            ev.get("score_bucket"),
            "volume_bucket":           ev.get("volume_bucket"),
            "candle_color":            ev.get("candle_color"),
            "symbol":                  ev.get("symbol"),
            "scan_date":               ev.get("scan_date"),
        } for ev in all_events]

        combo_rows = build_combo_statistics(
            events_for_combos, all_outcomes, replay_run_id=run_id,
        )
        _set(combo_rows=len(combo_rows), elapsed_secs=round(time.time() - t0, 1))

        # ── Tag all stat rows with context_quality; cap confidence for LIMITED ─
        all_sig_stats = _apply_context_quality(stats_rows + combo_rows, cq)
        pattern_rows  = _apply_context_quality(pattern_rows, cq)
        filter_rows   = _apply_context_quality(filter_rows, cq)

        # ── Write stats to parquet ────────────────────────────────────────────
        signal_stats_path = rdir / "signal_stats.parquet"
        n_sig_stats = write_parquet(signal_stats_path, all_sig_stats)

        pattern_stats_path = rdir / "pattern_stats.parquet"
        n_pattern = write_parquet(pattern_stats_path, pattern_rows)

        filter_impact_path = rdir / "filter_impact.parquet"
        n_filter = write_parquet(filter_impact_path, filter_rows)

        # ── Context limitations metadata ──────────────────────────────────────
        context_limitations = {
            "context_lookback_bars":  lookback_bars,
            "fetch_bars":             fetch_bars,
            "outcome_forward_bars":   _OUTCOME_FORWARD_BARS,
            "warmup_bars":            _WARMUP_BARS,
            "context_quality":        cq,
            "warnings":               run_warnings,
        }
        context_limitations_json = json.dumps(context_limitations)

        # ── Research bundle (combined JSON for quick analytics loading) ───────
        research_bundle = {
            "run_id":                    run_id,
            "generated_at":             datetime.utcnow().isoformat() + "Z",
            "lookback_bars":             lookback_bars,
            "fetch_bars":                fetch_bars,
            "context_quality":           cq,
            "context_limitations":       context_limitations,
            "run_warnings":              run_warnings,
            "signal_statistics":         all_sig_stats,
            "pattern_statistics":        pattern_rows,
            "filter_impact_statistics":  filter_rows,
        }
        rb_path = rdir / "research_bundle.json"
        write_json(rb_path, research_bundle)

        # ── Artifact status summary ───────────────────────────────────────────
        artifact_status = {
            "events":          {"rows": n_events,    "status": "ok" if n_events > 0    else "empty"},
            "outcomes":        {"rows": n_outcomes,  "status": "ok" if n_outcomes > 0  else "empty"},
            "signal_stats":    {"rows": n_sig_stats, "status": "ok" if n_sig_stats > 0 else "empty"},
            "pattern_stats":   {"rows": n_pattern,   "status": "ok" if n_pattern > 0   else "empty"},
            "filter_impact":   {"rows": n_filter,    "status": "ok" if n_filter > 0    else "empty"},
            "research_bundle": {"rows": 1,           "status": "ok"},
        }
        artifact_status_json = json.dumps(artifact_status)

        # ── run.json metadata snapshot ────────────────────────────────────────
        final_status = "stopped" if _state.get("stop_requested") else "completed"
        _update_run(
            run_id,
            status=final_status,
            total_days=len(scan_dates),
            days_completed=len(scan_dates),
            total_symbols=len(tickers),
            symbols_completed=completed_symbols,
            total_events=events_total,
            total_outcomes=outcomes_total,
            total_statistics_rows=n_sig_stats,
            fetch_bars=fetch_bars,
            outcome_forward_bars=_OUTCOME_FORWARD_BARS,
            warmup_bars=_WARMUP_BARS,
            artifact_status_json=artifact_status_json,
            context_limitations_json=context_limitations_json,
        )
        _finalize_finished_at(run_id)

        run_snapshot = _get_run_row(run_id)
        write_json(rdir / "run.json", run_snapshot)

        # ── Register artifacts in DB ──────────────────────────────────────────
        with get_db() as db:
            register_artifact(db, run_id, "events",         events_path,        n_events)
            register_artifact(db, run_id, "outcomes",       outcomes_path,      n_outcomes)
            register_artifact(db, run_id, "signal_stats",   signal_stats_path,  n_sig_stats)
            register_artifact(db, run_id, "pattern_stats",  pattern_stats_path, n_pattern)
            register_artifact(db, run_id, "filter_impact",  filter_impact_path, n_filter)
            register_artifact(db, run_id, "research_bundle", rb_path,           1, fmt="json")
            db.commit()

        _set(
            status=final_status, running=False,
            stop_requested=False, pause_requested=False,
            unique_symbols=len(unique_symbols_set),
            unique_symbol_dates=len(unique_symbol_dates_set),
            unique_tz_events=unique_tz_events_count,
            unique_combo_events=unique_combo_events_count,
            elapsed_secs=round(time.time() - t0, 1),
        )
        log.info(
            "signal_replay[%d] %s — %d events, %d outcomes, %d sig stats, %d patterns",
            run_id, final_status, n_events, n_outcomes, n_sig_stats, n_pattern,
        )

    except Exception as exc:
        tb = traceback.format_exc()
        log.error("signal_replay failed: %s\n%s", exc, tb)
        _set(status="failed", running=False, error=str(exc),
             stop_requested=False, pause_requested=False,
             elapsed_secs=round(time.time() - t0, 1))
        _update_run(run_id, status="failed", error_message=str(exc))
        _finalize_finished_at(run_id)


def _future_window(bars: list[dict], scan_date: str, n: int) -> list[dict]:
    if not bars:
        return []
    for i, b in enumerate(bars):
        d = _normalize_date(b.get("date"))
        if d and d > scan_date:
            return bars[i : i + n]
    return []

"""
main.py — FastAPI app + APScheduler + all API routes.
"""
from __future__ import annotations
import os
import sys
import logging

# Ensure backend/ directory is on sys.path so sub-packages (analyzers/) are importable
# regardless of which directory uvicorn is launched from.
_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
import concurrent.futures
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from data import fetch_ohlcv
from signal_engine import compute_signals
from wlnbb_engine import compute_wlnbb, score_last_bar, score_bars, l_signal_label
from predictor import predict_next
from l_sequence_predictor import predict_l_next
from stats_engine import compute_tz_l_matrix
from canonical_scoring_engine import compute_canonical_score, get_scoring_metadata, SCORING_ENGINE_NAME, SCORING_ENGINE_VERSION
from ultra_score import compute_ultra_score as _compute_ultra_score
from ultra_signal_parser import parse_stock_stat_signals as _parse_ultra_signals
from scanner import (
    run_scan, get_results, get_last_scan_time,
    get_scan_progress,
    save_watchlist, load_watchlist,
    save_settings, load_settings,
    run_combo_scan, get_combo_results, get_last_combo_scan_time,
    get_combo_scan_progress,
)
from combo_engine import compute_combo, last_n_active, COMBO_LABELS
from pump_finder import find_pump_combos, save_pump_combos, get_pump_combos
from paper_portfolio_migration import ensure_paper_portfolio_tables
from paper_portfolio_api import router as portfolio_router
from chart_obs_migration import ensure_chart_obs_tables
from chart_obs_api_v2 import router as chart_obs_router
from signal_replay_migration import ensure_signal_replay_tables
from signal_replay_routes import router as signal_replay_router
from ultra_pump_migration import ensure_ultra_pump_tables
from ultra_pump_routes import router as ultra_pump_router
from dashboard_routes import router as dashboard_router
from ultra_scan_migration import ensure_ultra_scan_tables

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    try:
        ensure_paper_portfolio_tables()
        ensure_chart_obs_tables()
        ensure_signal_replay_tables()
        ensure_ultra_pump_tables()
        ensure_ultra_scan_tables()
        # Pre-warm memory cache from DB so dashboard/ultra tab show data immediately
        try:
            from ultra_orchestrator import load_latest_ultra_scan_from_db
            for _tf in ("1d", "4h"):
                for _uni in ("sp500", "nasdaq"):
                    try:
                        load_latest_ultra_scan_from_db(_uni, _tf)
                    except Exception:
                        pass
        except Exception as _exc:
            log.warning("DB pre-warm failed (non-fatal): %s", _exc)
        scheduler = BackgroundScheduler(timezone="America/New_York")
        def _scheduled_scan():
            if not get_scan_progress().get("running"):
                run_scan("1d")

        scheduler.add_job(
            _scheduled_scan,
            CronTrigger(hour="9,12,15", minute="30"),
            id="daily_scan",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        log.info("Scheduler started")
    except Exception as exc:
        log.warning("Scheduler failed to start: %s", exc)

    yield

    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="TZ Signal Dashboard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(portfolio_router)
app.include_router(chart_obs_router)
app.include_router(signal_replay_router)
app.include_router(ultra_pump_router)
app.include_router(dashboard_router)


def _normalise_date(idx) -> list[str]:
    try:
        return list(idx.strftime("%Y-%m-%d"))
    except AttributeError:
        return [str(v)[:10] for v in idx]


def _df_to_records(df) -> list[dict]:
    dates = _normalise_date(df.index)
    df = df.copy()
    df.index = dates
    df.index.name = "date"
    records = df.reset_index()
    first = records.columns[0]
    if first != "date":
        records = records.rename(columns={first: "date"})
    records["date"] = records["date"].astype(str).str[:10]
    for col in ["sig_id", "bc", "zc"]:
        if col in records.columns:
            records[col] = records[col].astype(int)
    for col in ["is_bull", "is_bear"]:
        if col in records.columns:
            records[col] = records[col].astype(bool)
    bool_cols = [c for c in records.columns
                 if records[c].dtype == object and c not in ("date", "sig_name", "l_combo", "vol_bucket", "candle_dir")]
    for col in records.columns:
        if col in ("date", "sig_name", "l_combo", "vol_bucket", "candle_dir"):
            continue
        try:
            if str(records[col].dtype) == "bool":
                records[col] = records[col].astype(bool)
        except Exception:
            pass
    return records.to_dict(orient="records")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "tz-signal-dashboard", "version": "2.1"}


_ticker_info_cache: dict = {}

@app.get("/api/ticker-info/{ticker}")
def api_ticker_info(ticker: str):
    t = ticker.upper()
    if t in _ticker_info_cache:
        return _ticker_info_cache[t]
    try:
        import yfinance as yf
        info = yf.Ticker(t).info or {}
        result = {
            "ticker":  t,
            "name":    info.get("longName") or info.get("shortName") or t,
            "sector":  info.get("sector") or "",
            "industry": info.get("industry") or "",
        }
    except Exception:
        result = {"ticker": t, "name": t, "sector": "", "industry": ""}
    _ticker_info_cache[t] = result
    return result


@app.post("/api/ticker-info-batch")
def api_ticker_info_batch(body: dict):
    """Batch sector/name lookup for up to 200 tickers. Returns {ticker: {sector,...}}."""
    tickers = [str(t).upper() for t in (body.get("tickers") or [])[:200]]
    result: dict = {}

    need_fetch: list[str] = []
    for t in tickers:
        if t in _ticker_info_cache:
            result[t] = _ticker_info_cache[t]
        else:
            need_fetch.append(t)

    if need_fetch:
        def _fetch_one(t: str):
            try:
                import yfinance as yf
                info = yf.Ticker(t).info or {}
                r = {
                    "ticker": t,
                    "name":   info.get("longName") or info.get("shortName") or t,
                    "sector": info.get("sector") or "",
                    "industry": info.get("industry") or "",
                }
            except Exception:
                r = {"ticker": t, "name": t, "sector": "", "industry": ""}
            _ticker_info_cache[t] = r
            return t, r

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(need_fetch)))
        futures = {pool.submit(_fetch_one, t): t for t in need_fetch}
        done, _ = concurrent.futures.wait(futures, timeout=15)
        pool.shutdown(wait=False)

        for fut in done:
            try:
                t, r = fut.result()
                result[t] = r
            except Exception:
                pass

        for t in need_fetch:
            if t not in result:
                result[t] = {"ticker": t, "name": t, "sector": "", "industry": ""}

    return result


@app.get("/api/signals/{ticker}")
def api_signals(ticker: str, tf: str = "1d", bars: int = 150):
    try:
        df    = fetch_ohlcv(ticker, interval=tf, bars=bars)
        sigs  = compute_signals(df)
        wlnbb = compute_wlnbb(df)
        scores = score_bars(sigs["sig_id"], wlnbb)
        out   = df.join(sigs).join(wlnbb).join(scores)
        return _df_to_records(out)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/wlnbb/{ticker}")
def api_wlnbb(ticker: str, tf: str = "1d", bars: int = 150):
    try:
        df    = fetch_ohlcv(ticker, interval=tf, bars=bars)
        wlnbb = compute_wlnbb(df)
        out   = df.join(wlnbb)
        return _df_to_records(out)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/watchlist")
def api_watchlist(
    tickers: str = Query(..., description="Comma-separated tickers"),
    tf: str = "1d",
):
    result = []
    for raw in tickers.split(","):
        ticker = raw.strip().upper()
        if not ticker:
            continue
        try:
            df   = fetch_ohlcv(ticker, interval=tf, bars=10)
            sigs = compute_signals(df)
            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else last
            pct  = round(
                (float(last["close"]) - float(prev["close"]))
                / float(prev["close"]) * 100, 2
            )
            sig = sigs.iloc[-1]

            try:
                wlnbb = compute_wlnbb(df)
                bull_score, bear_score = score_last_bar(int(sig["sig_id"]), wlnbb)
                last_w = wlnbb.iloc[-1]
                l_sig       = l_signal_label(last_w)
                vol_bucket  = str(last_w.get("vol_bucket", ""))
                candle_dir  = str(last_w.get("candle_dir", ""))
                l_combo     = str(last_w.get("l_combo", "NONE"))
                blue        = bool(last_w.get("BLUE", False))
                cci_ready   = bool(last_w.get("CCI_READY", False))
                pre_pump    = bool(last_w.get("PRE_PUMP", False))
            except Exception:
                bull_score, bear_score = 0, 0
                l_sig = vol_bucket = candle_dir = ""
                l_combo = "NONE"
                blue = cci_ready = pre_pump = False

            result.append({
                "ticker":      ticker,
                "price":       round(float(last["close"]), 2),
                "change_pct":  pct,
                "sig_id":      int(sig["sig_id"]),
                "sig_name":    str(sig["sig_name"]),
                "is_bull":     bool(sig["is_bull"]),
                "is_bear":     bool(sig["is_bear"]),
                "bull_score":  bull_score,
                "bear_score":  bear_score,
                "l_signal":    l_sig,
                "vol_bucket":  vol_bucket,
                "candle_dir":  candle_dir,
                "l_combo":     l_combo,
                "blue":        blue,
                "cci_ready":   cci_ready,
                "pre_pump":    pre_pump,
            })
        except Exception as exc:
            result.append({"ticker": ticker, "error": str(exc)})
    return result


@app.get("/api/watchlist/saved")
def api_watchlist_saved():
    return {"tickers": load_watchlist()}


@app.post("/api/watchlist/save")
def api_watchlist_save(body: dict):
    tickers = body.get("tickers", [])
    save_watchlist(tickers)
    return {"status": "ok", "count": len(tickers)}


@app.get("/api/predict/{ticker}")
def api_predict(ticker: str, tf: str = "1d"):
    try:
        from predictor import compute_tz_stats, compute_tz_matrix
        df    = fetch_ohlcv(ticker, interval=tf, bars=5000)
        sigs  = compute_signals(df)
        full  = df.join(sigs)
        tz    = predict_next(full)
        tz_stats  = compute_tz_stats(full)
        tz_matrix = compute_tz_matrix(full)

        wlnbb = compute_wlnbb(df)
        full_w = full.join(wlnbb)
        l_preds = predict_l_next(full_w)

        return {**tz, **l_preds, "tz_stats": tz_stats, "tz_matrix": tz_matrix}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/l-predict/{ticker}")
def api_l_predict(ticker: str, tf: str = "1d"):
    try:
        df    = fetch_ohlcv(ticker, interval=tf, bars=5000)
        wlnbb = compute_wlnbb(df)
        return predict_l_next(wlnbb)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/pooled-predict/{ticker}")
def api_pooled_predict(ticker: str, tf: str = "1d", universe: str = "sp500"):
    try:
        from pooled_stats import get_pooled_predict, get_pooled_tz_freq, get_pooled_tz_matrix
        df    = fetch_ohlcv(ticker, interval=tf, bars=5000)
        sigs  = compute_signals(df)
        wlnbb = compute_wlnbb(df)

        sig_ids  = sigs["sig_id"].to_numpy()
        l_combos = wlnbb["l_combo"].values

        sig_seq_3 = tuple(int(s) for s in sig_ids[-3:])
        sig_seq_2 = tuple(int(s) for s in sig_ids[-2:])
        l_seq_3   = tuple(str(l) for l in l_combos[-3:])
        l_seq_2   = tuple(str(l) for l in l_combos[-2:])

        result = get_pooled_predict(sig_seq_3, sig_seq_2, l_seq_3, l_seq_2,
                                    universe=universe, interval=tf)
        result["bench_tz_stats"]  = get_pooled_tz_freq(universe, tf)
        result["bench_tz_matrix"] = get_pooled_tz_matrix(universe, tf)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/pooled-stats/build")
def api_pooled_stats_build(
    background_tasks: BackgroundTasks,
    universe: str = "sp500",
    interval: str = "1d",
    max_tickers: int = 2000,
):
    from pooled_stats import build_pooled_stats, get_pooled_state
    if get_pooled_state().get("running"):
        raise HTTPException(status_code=409, detail="Pooled stats build already running")
    background_tasks.add_task(build_pooled_stats, universe, interval, 6, max_tickers)
    return {"ok": True, "universe": universe, "interval": interval}


@app.get("/api/pooled-stats/status")
def api_pooled_stats_status(universe: str = "sp500", interval: str = "1d"):
    from pooled_stats import get_pooled_status, get_pooled_state
    data  = get_pooled_status(universe, interval)
    state = get_pooled_state()
    return {"data": data, "job": state}


@app.get("/api/tz-l-stats/{ticker}")
def api_tz_l_stats(ticker: str, tf: str = "1d"):
    try:
        def _matrix(sym):
            d = fetch_ohlcv(sym, interval=tf, bars=5000)
            return compute_tz_l_matrix(compute_signals(d).join(compute_wlnbb(d)))

        matrix = _matrix(ticker.upper())
        try:    bench_spy = _matrix("SPY")
        except: bench_spy = None
        try:    bench_qqq = _matrix("QQQ")
        except: bench_qqq = None

        return {"matrix": matrix, "bench_spy": bench_spy, "bench_qqq": bench_qqq}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/scan/results")
def api_scan_results(
    tf: str = "1d",
    limit: int = 100,
    tab: str = "all",
    min_score: int = 0,
):
    try:
        results   = get_results(interval=tf, limit=limit, min_bull=min_score, tab=tab)
        last_time = get_last_scan_time(tf)
        return {"results": results, "last_scan": last_time}
    except Exception as exc:
        log.exception("scan/results error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/scan/trigger")
def api_scan_trigger(background_tasks: BackgroundTasks, tf: str = "1d"):
    if get_scan_progress().get("running"):
        raise HTTPException(status_code=409, detail="Scan already running")
    background_tasks.add_task(run_scan, tf)
    return {"status": "scan started"}


@app.get("/api/scan/status")
def api_scan_status():
    return get_scan_progress()


@app.get("/api/combined-scan")
def api_combined_scan(
    tf: str = "1d",
    min_score: int = Query(4, ge=0, le=10),
    tab: str = "bull",
    limit: int = 100,
):
    try:
        results   = get_results(interval=tf, limit=limit, min_bull=min_score, tab=tab)
        last_time = get_last_scan_time(tf)
        return {"results": results, "last_scan": last_time}
    except Exception as exc:
        log.exception("combined-scan error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/pump-combos")
def api_pump_combos(
    threshold: float = 2.0,
    window: int = 20,
    combo_len: int = 3,
    limit: int = 50,
):
    combos = get_pump_combos(
        threshold=threshold, window=window, combo_len=combo_len, limit=limit
    )
    return {"combos": combos, "count": len(combos)}


@app.post("/api/pump-combos/trigger")
def api_pump_trigger(
    background_tasks: BackgroundTasks,
    threshold: float = 2.0,
    window: int = 20,
    combo_len: int = 3,
):
    from scanner import get_tickers

    def _run():
        tickers = get_tickers()
        df = find_pump_combos(
            tickers,
            pump_threshold=threshold,
            pump_window=window,
            combo_len=combo_len,
        )
        save_pump_combos(df, threshold=threshold, window=window, combo_len=combo_len)
        log.info("Pump combo mining done: %d combos", len(df))

    background_tasks.add_task(_run)
    return {"status": "started", "estimated_minutes": 15}


@app.get("/api/combo-scan")
def api_combo_scan(
    signal: str = "all",
    limit: int = 200,
):
    try:
        results   = get_combo_results(signal_filter=signal, limit=limit)
        last_time = get_last_combo_scan_time()
        return {"results": results, "last_scan": last_time}
    except Exception as exc:
        log.exception("combo-scan error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/combo-scan/trigger")
def api_combo_scan_trigger(
    background_tasks: BackgroundTasks,
    tf: str = "1d",
    n_bars: int = 3,
):
    if get_combo_scan_progress().get("running"):
        raise HTTPException(status_code=409, detail="Combo scan already running")
    background_tasks.add_task(run_combo_scan, tf, n_bars)
    return {"status": "combo scan started"}


@app.get("/api/combo-scan/status")
def api_combo_scan_status():
    return get_combo_scan_progress()


@app.get("/api/combo-scan/debug/{ticker}")
def api_combo_scan_debug(ticker: str, tf: str = "1d", rows: int = 7, n_bars: int = 3):
    import yfinance as yf
    try:
        df = yf.Ticker(ticker.upper()).history(period="90d", interval=tf, auto_adjust=True)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data for {ticker}")
        df.columns = [c.lower() for c in df.columns]
        combo = compute_combo(df)
        active = last_n_active(combo, n_bars)

        tail = combo.tail(rows)
        signal_cols = list(COMBO_LABELS.keys())

        bar_rows = []
        for date, row in tail.iterrows():
            fired = [COMBO_LABELS[c] for c in signal_cols if row.get(c, False)]
            bar_rows.append({
                "date":    str(date.date()) if hasattr(date, "date") else str(date),
                "signals": fired,
            })

        active_labels = [COMBO_LABELS[k] for k, v in active.items() if v]

        return {
            "ticker":       ticker.upper(),
            "n_bars":       n_bars,
            "active":       active_labels,
            "bars":         bar_rows,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/power-scan")
def api_power_scan(limit: int = 200):
    from power_engine import get_power_results, get_last_power_scan_time
    results   = get_power_results(limit=limit)
    last_time = get_last_power_scan_time()
    return {"results": results, "last_scan": last_time}


@app.post("/api/power-scan/trigger")
def api_power_scan_trigger(
    background_tasks: BackgroundTasks,
    tf: str = "1d",
    n_bars: int = 3,
):
    from power_engine import run_power_scan, get_power_scan_progress
    if get_power_scan_progress().get("running"):
        raise HTTPException(status_code=409, detail="Power scan already running")
    background_tasks.add_task(run_power_scan, tf, n_bars)
    return {"status": "power scan started"}


@app.get("/api/power-scan/status")
def api_power_scan_status():
    from power_engine import get_power_scan_progress
    return get_power_scan_progress()


@app.get("/api/turbo-scan")
def api_turbo_scan(
    limit: int = 10000,
    min_score: float = 0,
    direction: str = "bull",
    tf: str = "1d",
    universe: str = "sp500",
    price_min: float = 0,
    price_max: float = 1e9,
    rsi_min: float = 0,
    rsi_max: float = 100,
    cci_min: float = -9999,
    cci_max: float = 9999,
    vol_min: float = 0,
    vol_max: float = 0,
):
    try:
        from turbo_engine import get_turbo_results, get_last_turbo_scan_time
        results   = get_turbo_results(limit=limit, min_score=min_score, direction=direction,
                                      tf=tf, universe=universe,
                                      price_min=price_min, price_max=price_max,
                                      rsi_min=rsi_min, rsi_max=rsi_max,
                                      cci_min=cci_min, cci_max=cci_max,
                                      vol_min=vol_min, vol_max=vol_max)
        last_time = get_last_turbo_scan_time(tf=tf, universe=universe)

        # Enrich split-universe rows with lifecycle metadata + cross-filter to live universe
        meta: dict = {}
        if universe == "split":
            try:
                from split_universe import split_service, normalize_split_symbol
                sresult = split_service.get_split_universe_result()
                smeta   = {r["ticker"]: r for r in sresult.rows}
                live_tickers = frozenset(sresult.tickers)

                # Cross-filter: only show tickers in the current live split universe
                results = [r for r in results
                           if normalize_split_symbol(r.get("ticker", "")) in live_tickers]

                for r in results:
                    s = smeta.get(normalize_split_symbol(r.get("ticker", "")))
                    if s:
                        r["split_date"]            = s["split_date"]
                        r["split_ratio"]           = s["ratio_str"]
                        r["split_status"]          = s.get("split_status", "")
                        r["split_days_offset"]     = s.get("days_offset", 0)
                        r["split_phase"]           = s.get("phase", "")
                        r["split_wave"]            = s.get("wave", "")
                        r["split_watch_until"]     = s.get("watch_until", "")
                        r["split_next_wave_label"] = s.get("next_wave_label", "")
                        r["split_next_wave_start"] = s.get("next_wave_start_date", "")
                        r["split_next_wave_end"]   = s.get("next_wave_end_date", "")
                        r["split_heat_score"]      = s.get("heat_score", 0)
                        r["split_notes"]           = s.get("notes", "")
                        r["split_watch_days"]      = s.get("watch_days", 60)
                meta["split_count"]      = len(live_tickers)
                meta["split_source"]     = sresult.source
                meta["split_cache_key"]  = sresult.cache_key
                meta["split_generated_at"] = sresult.generated_at
            except Exception as exc:
                log.warning("split metadata enrich failed: %s", exc)

        # Enrich all rows with profile playbook fields (additive context only,
        # does not modify canonical scoring columns)
        try:
            from profile_playbook import enrich_row_with_profile
            results = [enrich_row_with_profile(r, universe) for r in results]
        except Exception as exc:
            log.warning("profile playbook enrichment failed: %s", exc)

        # Enrich with BETA Score — needs canonical fields, so we compute them
        # on the fly from the existing signal flags in each turbo row.
        try:
            from beta_engine import calc_beta_score as _calc_beta
            for r in results:
                try:
                    canon = compute_canonical_score(r, universe)
                    # Synthesise VOL string from vol_spike flags
                    if r.get("vol_spike_20x"):   _vol = "20x"
                    elif r.get("vol_spike_10x"): _vol = "10x"
                    elif r.get("vol_spike_5x"):  _vol = "5x"
                    else:                         _vol = ""
                    _beta_row = dict(r,
                        ROCKET_SCORE=canon["ROCKET_SCORE"],
                        CLEAN_ENTRY_SCORE=canon["CLEAN_ENTRY_SCORE"],
                        FINAL_REGIME=canon["FINAL_REGIME"],
                        VOL=_vol,
                    )
                    _b = _calc_beta(_beta_row, [], universe)
                    r["beta_score"]    = _b["beta_score"]
                    r["beta_raw"]      = _b["beta_raw"]
                    r["beta_setup"]    = _b["beta_setup"]
                    r["beta_momentum"] = _b["beta_momentum"]
                    r["beta_excess"]   = _b["beta_excess"]
                    r["beta_zone"]     = _b["beta_zone"]
                    r["beta_auto_buy"] = _b["beta_auto_buy"]
                except Exception:
                    r["beta_score"] = 0
                    r["beta_zone"] = ""
                    r["beta_auto_buy"] = False
        except Exception as exc:
            log.warning("beta score enrichment failed: %s", exc)

        return {"results": results, "last_scan": last_time, "meta": meta}
    except Exception as exc:
        log.exception("turbo-scan error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/split-universe/audit")
def api_split_universe_audit(
    force_refresh: bool = False,
    tf: str = "1d",
):
    """Three-way audit: shared split universe vs stock_stat vs TZ Intelligence.

    Sources compared:
      A. shared  — split_universe_latest.csv (written at generation time);
                   falls back to live split_service if the file is missing.
      B. stock_stat — unique tickers in the WLNBB/TZ stock_stat CSV.
      C. intelligence — after the scanner fix, equal to stock_stat (no live
                   cross-filter; NO_EDGE tickers are returned, not dropped).

    Expected after fix:
      shared_count == stock_stat_count == intelligence_count
      all difference lists == []
      is_consistent == true
    """
    try:
        from split_universe import (
            split_service, normalize_split_symbol, SPLIT_UNIVERSE_CSV_PATH,
        )
        from tz_intelligence.scanner import _stat_path
        import csv as _csv_mod

        # ── A: shared split universe ──────────────────────────────────────────
        canonical_csv = SPLIT_UNIVERSE_CSV_PATH
        canonical_tickers: set = set()
        canonical_exists = os.path.exists(canonical_csv)
        canonical_generated_at = ""

        if canonical_exists:
            with open(canonical_csv, newline="", encoding="utf-8") as f:
                for row in _csv_mod.DictReader(f):
                    t = normalize_split_symbol(row.get("ticker", ""))
                    if t:
                        canonical_tickers.add(t)
                    if not canonical_generated_at:
                        canonical_generated_at = row.get("generated_at", "")

        # Always also fetch live result (for debug metadata + fallback)
        sresult = split_service.get_split_universe_result(force_refresh=force_refresh)
        live_set = frozenset(sresult.tickers)

        # Use canonical CSV if present, else fall back to live service
        shared_set = frozenset(canonical_tickers) if canonical_tickers else live_set

        # ── B: stock_stat unique tickers ──────────────────────────────────────
        stat_path = _stat_path("split", tf)
        stock_stat_tickers: set = set()
        csv_total_rows = 0
        if os.path.exists(stat_path):
            with open(stat_path, newline="", encoding="utf-8") as f:
                for row in _csv_mod.DictReader(f):
                    csv_total_rows += 1
                    t = normalize_split_symbol(row.get("ticker", ""))
                    if t:
                        stock_stat_tickers.add(t)

        # ── C: intelligence tickers ───────────────────────────────────────────
        # After the scanner fix: no live cross-filter and NO_EDGE tickers are
        # preserved, so the intelligence ticker set equals the stock_stat set.
        intelligence_tickers = set(stock_stat_tickers)

        # ── Differences ───────────────────────────────────────────────────────
        shared_not_in_stock_stat      = sorted(shared_set - stock_stat_tickers)
        stock_stat_not_in_shared      = sorted(stock_stat_tickers - shared_set)
        stock_stat_not_in_intelligence = sorted(stock_stat_tickers - intelligence_tickers)
        intelligence_not_in_stock_stat = sorted(intelligence_tickers - stock_stat_tickers)
        shared_not_in_intelligence    = sorted(shared_set - intelligence_tickers)
        intelligence_not_in_shared    = sorted(intelligence_tickers - shared_set)

        is_consistent = not any([
            shared_not_in_stock_stat,
            stock_stat_not_in_shared,
            stock_stat_not_in_intelligence,
            intelligence_not_in_stock_stat,
        ])

        return {
            # counts
            "shared_count":        len(shared_set),
            "stock_stat_count":    len(stock_stat_tickers),
            "intelligence_count":  len(intelligence_tickers),
            # difference lists
            "shared_not_in_stock_stat":       shared_not_in_stock_stat,
            "stock_stat_not_in_shared":       stock_stat_not_in_shared,
            "stock_stat_not_in_intelligence": stock_stat_not_in_intelligence,
            "intelligence_not_in_stock_stat": intelligence_not_in_stock_stat,
            "shared_not_in_intelligence":     shared_not_in_intelligence,
            "intelligence_not_in_shared":     intelligence_not_in_shared,
            # verdict
            "is_consistent":  is_consistent,
            # legacy fields (for UI backward-compat)
            "counts": {
                "live_split_universe":         len(live_set),
                "shared":                      len(shared_set),
                "stock_stat":                  len(stock_stat_tickers),
                "intelligence":                len(intelligence_tickers),
                "only_in_turbo":               len(shared_not_in_stock_stat),
                "only_in_wlnbb":               len(stock_stat_not_in_shared),
                "wlnbb_csv_total_rows":        csv_total_rows,
                "wlnbb_csv_unique_tickers":    len(stock_stat_tickers),
            },
            "debug": {
                "total_events":                sresult.total_events,
                "reverse_split_events":        sresult.reverse_split_events,
                "stock_like_events":           sresult.stock_like_events,
                "filtered_non_stock":          sresult.filtered_non_stock,
                "missing_symbol":              sresult.missing_symbol,
                "duplicate_symbols_removed":   sresult.duplicate_symbols_removed,
                "ratio_parse_failed_count":    sresult.ratio_parse_failed_count,
                "date_mode":                   sresult.date_mode,
                "start_date":                  sresult.start_date,
                "end_date":                    sresult.end_date,
                "source":                      sresult.source,
                "cache_key":                   sresult.cache_key,
                "generated_at":                sresult.generated_at,
                "canonical_csv_path":          canonical_csv,
                "canonical_csv_exists":        canonical_exists,
                "canonical_csv_generated_at":  canonical_generated_at,
                "stock_stat_csv_path":         stat_path,
                "stock_stat_csv_exists":       os.path.exists(stat_path),
                "excluded_examples":           sresult.excluded_examples[:10],
            },
        }
    except Exception as exc:
        log.exception("split-universe audit error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/turbo-scan/trigger")
def api_turbo_scan_trigger(
    background_tasks: BackgroundTasks,
    tf: str = "1d",
    universe: str = "sp500",
    lookback_n: int = 5,
    partial_day: bool = False,
    min_volume: float = 0,
    min_store_score: float = 5,
):
    from turbo_engine import run_turbo_scan, get_turbo_progress
    if get_turbo_progress().get("running"):
        raise HTTPException(status_code=409, detail="Scan already running")
    background_tasks.add_task(run_turbo_scan, tf, universe, 8, lookback_n, partial_day, min_volume, False, min_store_score)
    return {"status": "turbo scan started", "tf": tf, "universe": universe, "lookback_n": lookback_n, "partial_day": partial_day, "min_volume": min_volume, "min_store_score": min_store_score}


@app.get("/api/turbo-scan/status")
def api_turbo_scan_status():
    from turbo_engine import get_turbo_progress
    return get_turbo_progress()


@app.post("/api/turbo-scan/reset")
def api_turbo_scan_reset():
    import time as _t
    from turbo_engine import _turbo_state
    _turbo_state["running"] = False
    _turbo_state["completed_at"] = _t.time()
    _turbo_state["error"] = "Manually stopped"
    return {"status": "stopped"}


@app.get("/api/br-scan")
def api_br_scan(limit: int = 300, min_br: float = 0, entry: str = "all", tf: str = "1d"):
    from br_engine import get_br_results, get_last_br_scan_time
    results   = get_br_results(limit=limit, min_br=min_br, entry_filter=entry, tf=tf)
    last_time = get_last_br_scan_time(tf=tf)
    return {"results": results, "last_scan": last_time}


@app.post("/api/br-scan/trigger")
def api_br_scan_trigger(background_tasks: BackgroundTasks, tf: str = "1d"):
    from br_engine import run_br_scan, get_br_scan_progress
    if get_br_scan_progress().get("running"):
        raise HTTPException(status_code=409, detail="BR scan already running")
    background_tasks.add_task(run_br_scan, tf)
    return {"status": "br scan started"}


@app.get("/api/br-scan/status")
def api_br_scan_status():
    from br_engine import get_br_scan_progress
    return get_br_scan_progress()


@app.get("/api/settings")
def api_get_settings():
    return load_settings()


@app.post("/api/settings")
def api_save_settings(body: dict):
    save_settings(body)
    return {"status": "ok"}


@app.get("/api/config")
def api_config():
    from data_polygon import polygon_available
    return {"massive_api_ready": polygon_available()}


@app.get("/api/turbo-analyze/{ticker}")
def api_turbo_analyze(ticker: str, tf: str = "1d"):
    from turbo_engine import _scan_turbo_ticker
    result = _scan_turbo_ticker(ticker.upper(), tf)
    if result is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return result


@app.get("/api/signal-stats/{ticker}")
def api_signal_stats(
    ticker: str,
    tf: str = "1d",
    signals: str = "",
    combo: bool = False,
    min_n: int = 3,
):
    from signal_stats_engine import run_signal_stats, SIGNAL_LABELS
    sig_list = [s.strip() for s in signals.split(",") if s.strip()]
    if not sig_list:
        sig_list = list(SIGNAL_LABELS.keys())
    return run_signal_stats(ticker.upper(), tf, sig_list, combo=combo, min_n=min_n)


# ── Pooled signal stats (SP500 aggregate) ─────────────────────────────────────
_SS_POOLED: dict = {}  # key: f"{universe}_{tf}"

# ── Stock Stat scan state ─────────────────────────────────────────────────────
_stock_stat_state: dict = {
    "running": False, "done": 0, "total": 0,
    "error": None, "output_path": None, "output_size": 0,
    "tf": None, "universe": None, "elapsed": 0.0,
}


def _ss_pooled_worker(universe: str, tf: str, signals: list, max_tickers: int = 500):
    import threading
    key = f"{universe}_{tf}"
    _SS_POOLED[key] = {"status": "running", "done": 0, "total": 0, "results": {}, "error": None}
    try:
        from scanner import get_universe_tickers
        from signal_stats_engine import run_signal_stats, SIGNAL_LABELS
        tickers = get_universe_tickers(universe)[:max_tickers]
        _SS_POOLED[key]["total"] = len(tickers)
        if not signals:
            signals = list(SIGNAL_LABELS.keys())

        # Aggregation: signal -> list of per-ticker stat dicts
        agg: dict = {s: [] for s in signals}

        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _one(t):
            try:
                return run_signal_stats(t, tf, signals, combo=False, min_n=1)
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(_one, t): t for t in tickers}
            for fut in as_completed(futs):
                res = fut.result()
                if res and "results" in res:
                    for s, st in res["results"].items():
                        if s in agg and st.get("n", 0) > 0:
                            agg[s].append(st)
                _SS_POOLED[key]["done"] += 1

        # Weighted-average aggregation
        pooled = {}
        for sig, stats_list in agg.items():
            if not stats_list:
                continue
            total_n = sum(s["n"] for s in stats_list)
            if total_n < 5:
                continue
            def _wavg(field):
                return sum(s.get(field, 0) * s["n"] for s in stats_list) / total_n
            pooled[sig] = {
                "n":          total_n,
                "tickers":    len(stats_list),
                "bull_rate":  round(_wavg("bull_rate"), 3),
                "avg_1bar":   round(_wavg("avg_1bar"), 2),
                "avg_3bar":   round(_wavg("avg_3bar"), 2),
                "avg_5bar":   round(_wavg("avg_5bar"), 2),
                "mae_3":      round(_wavg("mae_3"), 2),
                "false_rate": round(_wavg("false_rate"), 3),
            }

        _SS_POOLED[key].update({
            "status":   "done",
            "results":  pooled,
            "labels":   {k: SIGNAL_LABELS.get(k, k) for k in pooled},
            "universe": universe,
            "tf":       tf,
        })
    except Exception as exc:
        _SS_POOLED[key].update({"status": "error", "error": str(exc)})


@app.post("/api/signal-stats/pooled/build")
def api_ss_pooled_build(
    background_tasks: BackgroundTasks,
    tf: str = "1d",
    universe: str = "sp500",
    signals: str = "",
    max_tickers: int = 500,
):
    key = f"{universe}_{tf}"
    if _SS_POOLED.get(key, {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="Build already running")
    sig_list = [s.strip() for s in signals.split(",") if s.strip()]
    background_tasks.add_task(_ss_pooled_worker, universe, tf, sig_list, max_tickers)
    return {"ok": True, "universe": universe, "tf": tf}


@app.get("/api/signal-stats/pooled/status")
def api_ss_pooled_status(tf: str = "1d", universe: str = "sp500"):
    key = f"{universe}_{tf}"
    return _SS_POOLED.get(key, {"status": "idle"})


@app.get("/api/signal-correlation")
def api_signal_correlation(tf: str = "1d", universe: str = "sp500", min_pct: int = 15):
    from turbo_engine import get_turbo_results, _TURBO_COLS, _init_db, _db
    import numpy as np

    # Try requested universe first; fall back to latest scan for this tf regardless of universe
    rows = get_turbo_results(limit=5000, min_score=0, direction="all", tf=tf, universe=universe)
    if not rows:
        _init_db()
        con = _db()
        try:
            row = con.execute(
                "SELECT id, universe FROM turbo_scan_runs WHERE tf=? ORDER BY id DESC LIMIT 1", (tf,)
            ).fetchone()
        finally:
            con.close()
        if row:
            rows = get_turbo_results(limit=5000, min_score=0, direction="all", tf=tf, universe=row["universe"])

    if not rows:
        return {"n_tickers": 0, "signal_counts": {}, "pairs": []}

    # Boolean signal columns only
    bool_cols = [c for c in _TURBO_COLS if c not in {
        "turbo_score", "turbo_score_n3", "turbo_score_n5", "turbo_score_n10",
        "rsi", "cci", "avg_vol", "tz_sig", "vol_bucket", "sig_ages", "data_source", "tz_state",
        "any_f",  # derived aggregate (any F1-F11) — always redundant with individual F signals
    }]

    n = len(rows)
    mat = {c: np.array([int(bool(r.get(c, 0))) for r in rows], dtype=np.int8) for c in bool_cols}
    counts = {c: int(mat[c].sum()) for c in bool_cols}

    pairs = []
    cols_with_signals = [c for c in bool_cols if counts[c] > 0]
    for i, a in enumerate(cols_with_signals):
        for b in cols_with_signals[i+1:]:
            both = int((mat[a] & mat[b]).sum())
            if both == 0:
                continue
            ca, cb = counts[a], counts[b]
            pct_a = round(both / ca * 100) if ca else 0
            pct_b = round(both / cb * 100) if cb else 0
            max_pct = max(pct_a, pct_b)
            if max_pct >= min_pct:
                pairs.append({"sig_a": a, "sig_b": b, "both": both,
                               "a_count": ca, "b_count": cb,
                               "pct_a": pct_a, "pct_b": pct_b, "max_pct": max_pct})

    pairs.sort(key=lambda x: -x["max_pct"])

    # Compute top-C signal for each A-B pair (ABC chain)
    for p in pairs:
        ab_mask = mat[p["sig_a"]] & mat[p["sig_b"]]
        ab_n = p["both"]
        best_c, best_pct = None, 0
        for c in cols_with_signals:
            if c == p["sig_a"] or c == p["sig_b"]:
                continue
            cnt = int((ab_mask & mat[c]).sum())
            if cnt > 0:
                pct = round(cnt / ab_n * 100)
                if pct > best_pct:
                    best_pct, best_c = pct, c
        p["top_c"] = best_c
        p["pct_c"] = best_pct

    return {"n_tickers": n, "signal_counts": counts, "pairs": pairs}


@app.get("/api/admin/scan-history")
def api_admin_scan_history():
    from turbo_engine import _db, _init_db
    _init_db()
    con = _db()
    try:
        rows = con.execute("""
            SELECT id, tf, universe, started_at, completed_at, result_count
            FROM turbo_scan_runs ORDER BY id DESC LIMIT 20
        """).fetchall()
        return [{"id": r["id"], "tf": r["tf"], "universe": r["universe"],
                 "started_at": r["started_at"], "completed_at": r["completed_at"],
                 "result_count": r["result_count"]}
                for r in rows]
    finally:
        con.close()


@app.post("/api/admin/scan-start")
def api_admin_scan_start(background_tasks: BackgroundTasks, tf: str = "1d", universe: str = "sp500", min_store_score: float = 5):
    from turbo_engine import run_turbo_scan, get_turbo_progress
    if get_turbo_progress().get("running"):
        raise HTTPException(status_code=409, detail="Scan already running")
    background_tasks.add_task(run_turbo_scan, tf, universe, 8, 5, False, 0, False, min_store_score)
    return {"ok": True, "tf": tf, "universe": universe, "min_store_score": min_store_score}


@app.get("/api/bar_signals/{ticker}")
def api_bar_signals(ticker: str, tf: str = "1d", bars: int = 150, universe: str = "sp500"):
    """Per-bar signal matrix for SuperChart view."""
    import pandas as pd
    import numpy as np
    from signal_engine import compute_g_signals, compute_b_signals
    from f_engine import compute_f_signals
    from fly_engine import compute_fly_series
    from vabs_engine import compute_vabs
    from wick_engine import compute_wick
    from ultra_engine import compute_260308_l88, compute_ultra_v2
    from turbo_engine import _calc_turbo_score
    from combo_engine import compute_tz_state
    try:
        from profile_playbook import compute_profile_playbook_for_row as _pf_compute
        _pf_ok = True
    except Exception:
        _pf_ok = False

    try:
        df = fetch_ohlcv(ticker, interval=tf, bars=bars)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    _EDF = pd.DataFrame()

    try:
        sig_df = compute_signals(df)
    except Exception:
        sig_df = _EDF
    try:
        wlnbb = compute_wlnbb(df)
    except Exception:
        wlnbb = _EDF
    try:
        f_sigs = compute_f_signals(df)
    except Exception:
        f_sigs = _EDF
    try:
        fly_sigs = compute_fly_series(df)
    except Exception:
        fly_sigs = _EDF
    try:
        g_sigs = compute_g_signals(df)
    except Exception:
        g_sigs = _EDF
    try:
        b_sigs = compute_b_signals(df)
    except Exception:
        b_sigs = _EDF
    try:
        combo_df = compute_combo(df)
    except Exception:
        combo_df = _EDF
    try:
        vabs = compute_vabs(df)
    except Exception:
        vabs = _EDF
    try:
        wick = compute_wick(df)
    except Exception:
        wick = _EDF
    try:
        ultra260 = compute_260308_l88(df)
    except Exception:
        ultra260 = _EDF
    try:
        ultraV2 = compute_ultra_v2(df)
    except Exception:
        ultraV2 = _EDF
    try:
        tz_state_ser = compute_tz_state(df)
    except Exception:
        tz_state_ser = pd.Series(0, index=df.index, dtype=np.int8)
    tz_state_prev = tz_state_ser.shift(1, fill_value=0).astype(int)

    # GOG engine — provides SETUP, GOG_TIER, CONTEXT, GOG_SCORE per bar
    try:
        from gog_engine import compute_gog_signals as _compute_gog
        gog_result = _compute_gog(df, wlnbb, sig_df, f_sigs, vabs, ultra260, ultraV2, combo_df)
        gog_setup_ser   = gog_result.get("SETUP",    pd.Series("",  index=df.index))
        gog_tier_ser    = gog_result.get("GOG_TIER", pd.Series("",  index=df.index))
        gog_context_ser = gog_result.get("CONTEXT",  pd.Series("",  index=df.index))
        gog_score_ser   = gog_result.get("GOG_SCORE",pd.Series(0.0, index=df.index))
        _gog_ok = True
    except Exception:
        gog_setup_ser   = pd.Series("",  index=df.index)
        gog_tier_ser    = pd.Series("",  index=df.index)
        gog_context_ser = pd.Series("",  index=df.index)
        gog_score_ser   = pd.Series(0.0, index=df.index)
        _gog_ok = False

    # seq_bcont vectorized from bc column
    try:
        _bc = sig_df["bc"].fillna(0).astype(int) if not sig_df.empty else pd.Series(0, index=df.index)
        _bc_p1 = _bc.shift(1, fill_value=0).astype(int)
        _bc_p2 = _bc.shift(2, fill_value=0).astype(int)
        seq_bcont_ser = (
            (_bc_p2.isin([5, 3, 6, 4, 7]) & (_bc == 1)) |
            (_bc_p1.isin([9, 10, 11])      & (_bc.isin([1, 2]))) |
            (_bc_p1.isin([1, 4, 9])        & (_bc == 2))
        ).astype(int)
    except Exception:
        seq_bcont_ser = pd.Series(0, index=df.index)

    # VA — volume ATR crossover (vol/sma20 crosses above 2.0)
    try:
        _avg20 = df["volume"].rolling(20, min_periods=1).mean()
        _vr    = (df["volume"] / _avg20.replace(0, np.nan)).fillna(0)
        va_ser = ((_vr > 2.0) & (_vr.shift(1, fill_value=0) <= 2.0)).astype(int)
    except Exception:
        va_ser = pd.Series(0, index=df.index)

    # Vol spike ratio (current bar vs previous bar)
    vol_prev  = df["volume"].shift(1)
    vol_ratio = (df["volume"] / vol_prev.replace(0, np.nan)).fillna(0)

    isIntraday = tf in ("4h", "1h", "30m", "15m")

    # RTB v4 — per-bar sequential state
    try:
        from rtb_engine import calc_rtb_v4 as _rtb_v4
        _rtb_ok = True
    except Exception:
        _rtb_ok = False
    _rtb_prev_phase    = "0"
    _rtb_prev_age      = 0
    _rtb_soft_streak   = 0
    _rtb_pending_phase = ""
    _rtb_pending_count = 0
    _rtb_history: list = []   # chronological sig_rows (oldest first)

    # BETA Score engine
    try:
        from beta_engine import calc_beta_score as _calc_beta_score
        _beta_ok = True
    except Exception:
        _beta_ok = False
    _beta_history: list = []   # chronological T/Z dicts (oldest first)
    _ultra_score_history: list = []   # chronological ultra_score floats (oldest first)

    # Per-bar rolling history for bear-to-bull sequence scoring (most-recent-first)
    _pf_bar_history: list = []  # list of Set[str], [1_bar_ago, 2_bars_ago, ...]

    result = []

    for i in range(len(df)):
        row = df.iloc[i]
        ts  = df.index[i]
        date_val = int(ts.timestamp()) if isIntraday else str(ts)[:10]

        def _b(frame, col):
            if frame is None or frame.empty or col not in frame.columns:
                return False
            return bool(frame.iloc[i][col])

        # T/Z signal name
        tz = ""
        if not sig_df.empty and "sig_id" in sig_df.columns:
            if int(sig_df.iloc[i]["sig_id"]) > 0:
                tz = str(sig_df.iloc[i].get("sig_name", ""))

        # L / FRI / BLUE / BO / BX / BE / RL / RH / CCI signals
        l_map = [
            ("L34", "L34"), ("L43", "L43"), ("L64", "L64"), ("L22", "L22"),
            ("L555", "L555"), ("ONLY_L2L4", "L2L4"),
            ("FRI34", "FRI34"), ("FRI43", "FRI43"), ("FRI64", "FRI64"),
            ("BLUE", "BL"), ("CCI_READY", "CCI"),
            ("CCI_0_RETEST_OK", "CCI0R"), ("CCI_BLUE_TURN", "CCIB"),
            ("BO_UP", "BO↑"), ("BO_DN", "BO↓"),
            ("BX_UP", "BX↑"), ("BX_DN", "BX↓"),
            ("BE_UP", "BE↑"), ("BE_DN", "BE↓"),
            ("FUCHSIA_RL", "RL"), ("FUCHSIA_RH", "RH"), ("PRE_PUMP", "PP"),
        ]
        l_list = [lbl for col, lbl in l_map if _b(wlnbb, col)]

        # F signals
        f_list = [f"F{n}" for n in range(1, 12) if _b(f_sigs, f"f{n}")]

        # FLY — show strongest only
        fly_list = []
        for col, lbl in [("fly_abcd", "FLY"), ("fly_cd", "FLY-CD"),
                          ("fly_bd", "FLY-BD"), ("fly_ad", "FLY-AD")]:
            if _b(fly_sigs, col):
                fly_list.append(lbl)
                break

        # G signals
        g_list = [f"G{n}" for n in [1, 2, 4, 6, 11] if _b(g_sigs, f"g{n}")]

        # B signals
        b_list = [f"B{n}" for n in range(1, 12) if _b(b_sigs, f"b{n}")]

        # Combo signals
        combo_map = [
            ("rocket", "ROCKET"), ("buy_2809", "BUY"), ("sig3g", "3G"),
            ("bb_brk", "BB↑"), ("atr_brk", "ATR↑"), ("rtv", "RTV"),
            ("preup3", "P3"), ("preup2", "P2"), ("preup50", "P50"), ("preup89", "P89"),
            ("hilo_buy", "HILO↑"), ("hilo_sell", "HILO↓"),
            ("bias_up", "↑BIAS"), ("bias_down", "↓BIAS"),
            ("cons_atr", "CONS"), ("um_2809", "UM"), ("svs_2809", "SVS"),
            ("conso_2809", "CONSO"),
        ]
        combo_list = [lbl for col, lbl in combo_map if _b(combo_df, col)]

        # Vol spike
        vr = float(vol_ratio.iloc[i])
        vol_list = []
        if vr >= 20: vol_list.append("20×")
        elif vr >= 10: vol_list.append("10×")
        elif vr >= 5:  vol_list.append("5×")

        # VABS signals
        vabs_map = [
            ("best_sig", "BEST★"), ("strong_sig", "STRONG"),
            ("vbo_up", "VBO↑"), ("vbo_dn", "VBO↓"),
            ("ns", "NS"), ("nd", "ND"), ("sc", "SC"), ("bc", "BC"),
            ("sq", "SQ"), ("abs_sig", "ABS"), ("climb_sig", "CLM"), ("load_sig", "LOAD"),
        ]
        vabs_list = [lbl for col, lbl in vabs_map if _b(vabs, col)]

        # Wick signals
        wick_map = [
            ("WICK_BULL_CONFIRM", "WC↑"), ("WICK_BEAR_CONFIRM", "WC↓"),
            ("WICK_BULL_PATTERN", "WP↑"), ("WICK_BEAR_PATTERN", "WP↓"),
        ]
        wick_list = [lbl for col, lbl in wick_map if _b(wick, col)]

        # ULTRA v2 signals
        ultra_map = [
            ("best_long", "BEST↑"), ("fbo_bull", "FBO↑"), ("fbo_bear", "FBO↓"),
            ("eb_bull", "EB↑"), ("eb_bear", "EB↓"),
            ("bf_buy", "4BF"), ("bf_sell", "4BF↓"),
            ("ultra_3up", "3↑"),
        ]
        ultra_list = [lbl for col, lbl in ultra_map if _b(ultraV2, col)]
        if _b(ultra260, "sig_l88"):       ultra_list.append("L88")
        elif _b(ultra260, "sig_260308"):  ultra_list.append("260308")

        # Turbo score per bar — same formula as Turbo scanner
        tz_s = ""
        if not sig_df.empty and "sig_id" in sig_df.columns:
            tz_s = str(sig_df.iloc[i].get("sig_name", ""))
        is_bull_bar = not sig_df.empty and bool(sig_df.iloc[i].get("is_bull", False))
        sig_row = {
            # Backbone
            "conso_2809":  _b(combo_df, "conso_2809"),
            "tz_bull":     is_bull_bar,
            "bf_buy":      _b(ultraV2, "bf_buy"),
            # Volume / accum
            "abs_sig":     _b(vabs, "abs_sig"),
            "climb_sig":   _b(vabs, "climb_sig"),
            "load_sig":    _b(vabs, "load_sig"),
            "vbo_up":      _b(vabs, "vbo_up"),
            "ns":          _b(vabs, "ns"),
            "sq":          _b(vabs, "sq"),
            "sc":          _b(vabs, "sc"),
            "svs_2809":    _b(combo_df, "svs_2809"),
            "um_2809":     _b(combo_df, "um_2809"),
            "sig_l88":     _b(ultra260, "sig_l88"),
            "sig_260308":  _b(ultra260, "sig_260308"),
            # Breakout
            "fbo_bull":    _b(ultraV2, "fbo_bull"),
            "eb_bull":     _b(ultraV2, "eb_bull"),
            "ultra_3up":   _b(ultraV2, "ultra_3up"),
            "bo_up":       _b(wlnbb, "BO_UP"),
            "bx_up":       _b(wlnbb, "BX_UP"),
            "be_up":       _b(wlnbb, "BE_UP"),
            # Combo / momentum (+ stateful: cd/ca/cw/seq_bcont/va)
            "rocket":      _b(combo_df, "rocket"),
            "buy_2809":    _b(combo_df, "buy_2809"),
            "sig3g":       _b(combo_df, "sig3g"),
            "rtv":         _b(combo_df, "rtv"),
            "hilo_buy":    _b(combo_df, "hilo_buy"),
            "atr_brk":     _b(combo_df, "atr_brk"),
            "bb_brk":      _b(combo_df, "bb_brk"),
            "seq_bcont":   bool(seq_bcont_ser.iloc[i]),
            "va":          bool(va_ser.iloc[i]),
            # cd/ca/cw from tz_state + any B signal
            "cd": bool(int(tz_state_ser.iloc[i]) == 3 and
                       any(_b(b_sigs, f"b{n}") for n in range(1, 12))),
            "ca": bool(int(tz_state_ser.iloc[i]) == 2 and
                       any(_b(b_sigs, f"b{n}") for n in range(1, 12))),
            "cw": bool(int(tz_state_ser.iloc[i]) == 1 and
                       any(_b(b_sigs, f"b{n}") for n in range(1, 12))),
            # L-structure / trend
            "tz_sig":        tz_s,
            "tz_bull_flip":  bool(int(tz_state_ser.iloc[i]) == 3 and
                                  int(tz_state_prev.iloc[i]) != 3),
            "tz_attempt":    bool(int(tz_state_ser.iloc[i]) == 2 and
                                  int(tz_state_prev.iloc[i]) != 2),
            "tz_weak_bull":  bool(int(tz_state_ser.iloc[i]) == 1 and
                                  int(tz_state_prev.iloc[i]) == 0 and
                                  float(df["close"].iloc[i]) > float(df["open"].iloc[i])),
            "fri34":       _b(wlnbb, "FRI34"),
            "fri43":       _b(wlnbb, "FRI43"),
            "l34":         _b(wlnbb, "L34"),
            "l43":         _b(wlnbb, "L43"),
            "blue":        _b(wlnbb, "BLUE"),
            "cci_ready":   _b(wlnbb, "CCI_READY"),
            "fuchsia_rl":  _b(wlnbb, "FUCHSIA_RL"),
            # EMA cross / preup
            "preup89":     _b(combo_df, "preup89"),
            "preup3":      _b(combo_df, "preup3"),
            "preup2":      _b(combo_df, "preup2"),
            # G signals
            "g1":  _b(g_sigs, "g1"),
            "g2":  _b(g_sigs, "g2"),
            "g4":  _b(g_sigs, "g4"),
            "g6":  _b(g_sigs, "g6"),
            "g11": _b(g_sigs, "g11"),
            # Wick context
            "x2g_wick":  _b(wick, "x2g_wick"),
            "x2_wick":   _b(wick, "x2_wick"),
            "x1g_wick":  _b(wick, "x1g_wick"),
            "x1_wick":   _b(wick, "x1_wick"),
            "x3_wick":   _b(wick, "x3_wick"),
            "wick_bull": _b(wick, "WICK_BULL_CONFIRM"),
            # FLY context
            "fly_abcd": _b(fly_sigs, "fly_abcd"),
            "fly_cd":   _b(fly_sigs, "fly_cd"),
            "fly_bd":   _b(fly_sigs, "fly_bd"),
            "fly_ad":   _b(fly_sigs, "fly_ad"),
            # Vol spike context
            "vol_spike_10x": float(vol_ratio.iloc[i]) >= 10,
            # ── Additional flags for canonical sub-score computation ────────
            # F-signal entries (CLEAN_ENTRY_SCORE + BETA Score)
            "f1":  _b(f_sigs, "f1"),
            "f2":  _b(f_sigs, "f2"),
            "f3":  _b(f_sigs, "f3"),
            "f4":  _b(f_sigs, "f4"),
            "f5":  _b(f_sigs, "f5"),
            "f6":  _b(f_sigs, "f6"),
            "f7":  _b(f_sigs, "f7"),
            "f8":  _b(f_sigs, "f8"),
            "f9":  _b(f_sigs, "f9"),
            "f10": _b(f_sigs, "f10"),
            "f11": _b(f_sigs, "f11"),
            # B-signal breakout confirms (CLEAN_ENTRY_SCORE)
            "b6":  _b(b_sigs, "b6"),
            "b8":  _b(b_sigs, "b8"),
            # Bear / risk signals (HARD_BEAR_SCORE)
            "fbo_bear":   _b(ultraV2, "fbo_bear"),
            "eb_bear":    _b(ultraV2, "eb_bear"),
            "bo_dn":      _b(wlnbb, "BO_DN"),
            "bx_dn":      _b(wlnbb, "BX_DN"),
            "fuchsia_rh": _b(wlnbb, "FUCHSIA_RH"),
        }
        # ── Canonical scoring — single call for all score columns ──────────
        canonical = compute_canonical_score(sig_row)
        turbo_score_val = canonical["turbo_score"]

        vol_bkt = ""
        if not wlnbb.empty and "vol_bucket" in wlnbb.columns:
            vol_bkt = str(wlnbb.iloc[i]["vol_bucket"])

        # RSI and CCI (from wlnbb)
        rsi_val = None
        cci_val = None
        if not wlnbb.empty:
            if "rsi" in wlnbb.columns:
                rsi_val = float(wlnbb.iloc[i]["rsi"])
            if "cci_sma" in wlnbb.columns:
                cci_val = float(wlnbb.iloc[i]["cci_sma"])

        # Profile enrichment per bar — unified function with rolling history
        _pf_result: dict = {}
        if _pf_ok:
            try:
                _bar_proxy = {
                    "close": float(row["close"]),
                    "combo": combo_list, "vabs": vabs_list,
                    "l": l_list, "f": f_list, "fly": fly_list,
                    "g": g_list, "b": b_list, "ultra": ultra_list,
                    "vol": vol_list, "wick": wick_list, "tz": tz_s,
                }
                _pf_result = _pf_compute(
                    _bar_proxy, universe, history_context=_pf_bar_history[:5]
                )
                _pf_bar_history.insert(0, set(_pf_result["active_signals"]))
                if len(_pf_bar_history) > 5:
                    _pf_bar_history.pop()
            except Exception:
                pass

        # RTB v4 per-bar
        rtb_phase_val      = ""
        rtb_total_val      = 0.0
        rtb_transition_val = ""
        rtb_build_val      = 0.0
        rtb_turn_val       = 0.0
        rtb_ready_val      = 0.0
        rtb_late_val       = 0.0
        rtb_bonus3_val     = 0.0
        dbg_context_ready_val        = False
        dbg_t4_ctx_val               = False
        dbg_t6_ctx_val               = False
        dbg_t4t6_activation_plus_val = False
        dbg_launch_cluster_count_val = 0
        dbg_pending_phase_val        = ""
        dbg_pending_phase_count_val  = 0
        if _rtb_ok:
            try:
                _sr = dict(sig_row,
                           close=float(row["close"]),
                           open=float(row["open"]),
                           high=float(row["high"]),
                           vol_bucket=vol_bkt)
                # history: most-recent-first (history[0] = 1 bar ago)
                _hist = list(reversed(_rtb_history[-5:]))
                _res  = _rtb_v4(_sr, _hist, _rtb_prev_phase, _rtb_prev_age,
                                _rtb_soft_streak, _rtb_pending_phase, _rtb_pending_count)
                rtb_phase_val      = _res["rtb_phase"]
                rtb_total_val      = round(float(_res["rtb_total"]), 1)
                rtb_transition_val = _res["rtb_transition"]
                rtb_build_val      = round(float(_res["rtb_build"]),  1)
                rtb_turn_val       = round(float(_res["rtb_turn"]),   1)
                rtb_ready_val      = round(float(_res["rtb_ready"]),  1)
                rtb_late_val       = round(float(_res["rtb_late"]),   1)
                rtb_bonus3_val     = round(float(_res["rtb_bonus3"]), 1)
                dbg_context_ready_val        = bool(_res["dbg_context_ready"])
                dbg_t4_ctx_val               = bool(_res["dbg_t4_ctx"])
                dbg_t6_ctx_val               = bool(_res["dbg_t6_ctx"])
                dbg_t4t6_activation_plus_val = bool(_res["dbg_t4t6_activation_plus"])
                dbg_launch_cluster_count_val = int(_res["dbg_launch_cluster_count"])
                dbg_pending_phase_val        = _res["dbg_pending_phase"]
                dbg_pending_phase_count_val  = int(_res["dbg_pending_phase_count"])
                _rtb_prev_phase    = rtb_phase_val
                _rtb_prev_age      = _res["rtb_phase_age"]
                _rtb_soft_streak   = _res["_soft_streak"]
                _rtb_pending_phase = _res["_pending_phase"]
                _rtb_pending_count = _res["_pending_phase_count"]
                _rtb_history.append(_sr)
            except Exception:
                pass

        # BETA Score — called after RTB + profile playbook are resolved
        _beta_result = {"beta_score": 0, "beta_raw": 0, "beta_setup": 0,
                        "beta_momentum": 0, "beta_excess": 0,
                        "beta_zone": "NEUTRAL", "beta_auto_buy": False}
        if _beta_ok:
            try:
                _beta_row = dict(sig_row,
                                 rtb_total=rtb_total_val,
                                 rtb_phase=rtb_phase_val,
                                 sweet_spot_active=int(_pf_result.get("sweet_spot_active", False)),
                                 bear_to_bull_confirmed=int(_pf_result.get("bear_to_bull_confirmed", 0)),
                                 profile_category=_pf_result.get("profile_category", "WATCH"),
                                 profile_score=_pf_result.get("profile_score", 0),
                                 CLEAN_ENTRY_SCORE=canonical["CLEAN_ENTRY_SCORE"],
                                 ROCKET_SCORE=canonical["ROCKET_SCORE"],
                                 FINAL_REGIME=canonical["FINAL_REGIME"],
                                 rsi=rsi_val or 50.0,
                                 VOL=" ".join(vol_list),
                                 T=tz_s if tz_s.startswith("T") else "",
                                 Z=tz_s if tz_s.startswith("Z") else "")
                _beta_hist = list(reversed(_beta_history[-5:]))
                # rolling_score_max_5d: max ultra_score over past 5 bars (excludes current)
                _rolling_score_max = max(_ultra_score_history[-5:]) if _ultra_score_history else 0.0
                _beta_row["turbo_score"] = turbo_score_val
                _beta_result = _calc_beta_score(_beta_row, _beta_hist, universe,
                                                rolling_score_max=_rolling_score_max)
                _beta_history.append({"T": _beta_row["T"], "Z": _beta_row["Z"]})
                if len(_beta_history) > 10:
                    _beta_history.pop(0)
            except Exception:
                pass

        # GOG / SETUP / CONTEXT per bar
        _gog_tier_val  = str(gog_tier_ser.iloc[i]    or "")
        _gog_score_val = float(gog_score_ser.iloc[i]) if not pd.isna(gog_score_ser.iloc[i]) else 0.0
        _setup_str     = str(gog_setup_ser.iloc[i]   or "")
        _ctx_str       = str(gog_context_ser.iloc[i] or "")
        setup_list   = [t for t in _setup_str.split()  if t]
        context_list = [t for t in _ctx_str.split()    if t]

        result.append({
            "date":       date_val,
            "open":       float(row["open"]),
            "high":       float(row["high"]),
            "low":        float(row["low"]),
            "close":      float(row["close"]),
            "volume":     float(row["volume"]),
            "vol_bucket": vol_bkt,
            "tz":        tz,
            "l":         l_list,
            "f":         f_list,
            "fly":       fly_list,
            "g":         g_list,
            "b":         b_list,
            "combo":     combo_list,
            "vol":       vol_list,
            "vabs":      vabs_list,
            "wick":      wick_list,
            "ultra":          ultra_list,
            "setup":          setup_list,
            "gog_tier":       _gog_tier_val,
            "context":        context_list,
            "gog_score":      _gog_score_val,
            "gog1": 1 if _gog_tier_val.startswith("G1") else 0,
            "gog2": 1 if _gog_tier_val.startswith("G2") else 0,
            "gog3": 1 if _gog_tier_val.startswith("G3") else 0,
            "signal_score": _gog_score_val,
            "turbo_score":           turbo_score_val,
            # ── Canonical score columns — uppercase (stock_stat CSV / replay engine) ──
            "FINAL_BULL_SCORE":      canonical["FINAL_BULL_SCORE"],
            "ROCKET_SCORE":          canonical["ROCKET_SCORE"],
            "CLEAN_ENTRY_SCORE":     canonical["CLEAN_ENTRY_SCORE"],
            "SHAKEOUT_ABSORB_SCORE": canonical["SHAKEOUT_ABSORB_SCORE"],
            "EXTRA_BULL_SCORE":      canonical["EXTRA_BULL_SCORE"],
            "EXPERIMENTAL_SCORE":    canonical["EXPERIMENTAL_SCORE"],
            "REBOUND_SQUEEZE_SCORE": canonical["REBOUND_SQUEEZE_SCORE"],
            "HARD_BEAR_SCORE":       canonical["HARD_BEAR_SCORE"],
            "VOLATILITY_RISK_SCORE": canonical["VOLATILITY_RISK_SCORE"],
            "HAS_ELITE_MODEL":       canonical["HAS_ELITE_MODEL"],
            "HAS_REBOUND_MODEL":     canonical["HAS_REBOUND_MODEL"],
            "HAS_STRONG_BULL_MODEL": canonical["HAS_STRONG_BULL_MODEL"],
            "FINAL_REGIME":          canonical["FINAL_REGIME"],
            "FINAL_SCORE_BUCKET":    canonical["FINAL_SCORE_BUCKET"],
            # ── Lowercase aliases — required by SuperchartPanel.jsx CSV export ──
            # JavaScript key access is case-sensitive; b.final_bull_score !== b.FINAL_BULL_SCORE
            "final_bull_score":      canonical["FINAL_BULL_SCORE"],
            "rocket_score":          canonical["ROCKET_SCORE"],
            "clean_entry_score":     canonical["CLEAN_ENTRY_SCORE"],
            "shakeout_absorb_score": canonical["SHAKEOUT_ABSORB_SCORE"],
            "extra_bull_score":      canonical["EXTRA_BULL_SCORE"],
            "experimental_score":    canonical["EXPERIMENTAL_SCORE"],
            "rebound_squeeze_score": canonical["REBOUND_SQUEEZE_SCORE"],
            "hard_bear_score":       canonical["HARD_BEAR_SCORE"],
            "volatility_risk_score": canonical["VOLATILITY_RISK_SCORE"],
            "has_elite_model":       canonical["HAS_ELITE_MODEL"],
            "has_rebound_model":     canonical["HAS_REBOUND_MODEL"],
            "has_strong_bull_model": canonical["HAS_STRONG_BULL_MODEL"],
            "final_regime":          canonical["FINAL_REGIME"],
            "final_score_bucket":    canonical["FINAL_SCORE_BUCKET"],
            "rtb_phase":      rtb_phase_val,
            "rtb_total":      rtb_total_val,
            "rtb_transition": rtb_transition_val,
            "rtb_build":      rtb_build_val,
            "rtb_turn":       rtb_turn_val,
            "rtb_ready":      rtb_ready_val,
            "rtb_late":       rtb_late_val,
            "rtb_bonus3":     rtb_bonus3_val,
            "dbg_context_ready":        dbg_context_ready_val,
            "dbg_t4_ctx":               dbg_t4_ctx_val,
            "dbg_t6_ctx":               dbg_t6_ctx_val,
            "dbg_t4t6_activation_plus": dbg_t4t6_activation_plus_val,
            "dbg_launch_cluster_count": dbg_launch_cluster_count_val,
            "dbg_pending_phase":        dbg_pending_phase_val,
            "dbg_pending_phase_count":  dbg_pending_phase_count_val,
            # ── RSI / CCI (numeric values for SuperChart display) ──────────────
            "rsi":              rsi_val,
            "cci":              cci_val,
            # ── Profile playbook per-bar (all fields from unified function) ────
            "profile_playbook_version":  _pf_result.get("profile_playbook_version", ""),
            "profile_name":              _pf_result.get("profile_name", ""),
            "profile_score":             _pf_result.get("profile_score", 0),
            "profile_category":          _pf_result.get("profile_category", "WATCH"),
            "sweet_spot_active":         int(_pf_result.get("sweet_spot_active", False)),
            "late_warning":              int(_pf_result.get("late_warning", False)),
            "bear_context_last_3":       _pf_result.get("bear_context_last_3", 0),
            "bear_context_last_5":       _pf_result.get("bear_context_last_5", 0),
            "bull_confirm_now":          _pf_result.get("bull_confirm_now", 0),
            "bear_to_bull_confirmed":    _pf_result.get("bear_to_bull_confirmed", 0),
            "bear_to_bull_bars_ago":     _pf_result.get("bear_to_bull_bars_ago", 0),
            "bear_to_bull_bonus":        _pf_result.get("bear_to_bull_bonus", 0),
            "bear_to_bull_pairs":        _pf_result.get("bear_to_bull_pairs", []),
            "base_profile_score_without_btb": _pf_result.get("base_profile_score_without_btb", 0),
            "category_without_btb":      _pf_result.get("category_without_btb", "WATCH"),
            "category_with_btb":         _pf_result.get("category_with_btb", "WATCH"),
            "btb_category_upgrade":      _pf_result.get("btb_category_upgrade", 0),
            "btb_created_sweet_spot":    _pf_result.get("btb_created_sweet_spot", 0),
            "btb_late_clamped":          _pf_result.get("btb_late_clamped", 0),
            "btb_sweet_spot_allowed_profile": _pf_result.get("btb_sweet_spot_allowed_profile", 0),
            "active_signals":            _pf_result.get("active_signals", []),
            # ── BETA Score ────────────────────────────────────────────────────
            "beta_score":    _beta_result["beta_score"],
            "beta_raw":      _beta_result["beta_raw"],
            "beta_setup":    _beta_result["beta_setup"],
            "beta_momentum": _beta_result["beta_momentum"],
            "beta_excess":   _beta_result["beta_excess"],
            "beta_zone":     _beta_result["beta_zone"],
            "beta_auto_buy": _beta_result["beta_auto_buy"],
            # rolling_score_max_5d — populated below after ultra_score is computed
            "rolling_score_max_5d": (
                max(_ultra_score_history[-5:]) if _ultra_score_history else 0.0
            ),
        })

        # Track ultra_score for next bar's DECAY MEMORY BONUS (rolling_score_max_5d).
        # Also expose ultra_score / ultra_score_band on the per-bar dict so
        # downstream consumers (ULTRA Pump Research, stock_stat, replays) can
        # read the historical ULTRA scoring directly without recomputing.
        try:
            _us = _compute_ultra_score(result[-1])
            _us_val = float(_us.get("ultra_score", 0) or 0)
            result[-1]["ultra_score"]      = _us_val
            result[-1]["ultra_score_band"] = _us.get("ultra_score_band", "")
            _ultra_score_history.append(_us_val)
            if len(_ultra_score_history) > 10:
                _ultra_score_history.pop(0)
        except Exception:
            result[-1].setdefault("ultra_score", 0.0)
            result[-1].setdefault("ultra_score_band", "")
            _ultra_score_history.append(0.0)
            if len(_ultra_score_history) > 10:
                _ultra_score_history.pop(0)

    return result


# ── Stock Stat — bulk per-bar signal CSV for entire universe ──────────────────

def run_stock_stat(tf: str = "1d", universe: str = "sp500", bars: int = 60):
    import csv, time
    from scanner import get_universe_tickers

    t0 = time.time()
    _stock_stat_state.update(
        running=True, done=0, total=0, error=None,
        output_path=None, output_size=0, tf=tf, universe=universe, elapsed=0.0
    )
    _PREUP = {"P2", "P3", "P50", "P89"}

    try:
        tickers = get_universe_tickers(universe)
        _stock_stat_state["total"] = len(tickers)

        os.makedirs("stock_stat_output", exist_ok=True)
        out_path = f"stock_stat_output/stock_stat_{universe}_{tf}.csv"

        headers = [
            "ticker", "date", "open", "high", "low", "close", "volume",
            "vol_bucket", "turbo_score",
            # ── Canonical score columns ───────────────────────────────────────
            "FINAL_BULL_SCORE",
            "ROCKET_SCORE", "CLEAN_ENTRY_SCORE", "SHAKEOUT_ABSORB_SCORE",
            "EXTRA_BULL_SCORE", "EXPERIMENTAL_SCORE", "REBOUND_SQUEEZE_SCORE",
            "HARD_BEAR_SCORE", "VOLATILITY_RISK_SCORE",
            "HAS_ELITE_MODEL", "HAS_REBOUND_MODEL", "HAS_STRONG_BULL_MODEL",
            "FINAL_REGIME", "FINAL_SCORE_BUCKET",
            # ── RTB ───────────────────────────────────────────────────────────
            "rtb_phase", "rtb_total", "rtb_transition",
            "rtb_build", "rtb_turn", "rtb_ready", "rtb_late", "rtb_bonus3",
            "dbg_context_ready", "dbg_t4_ctx", "dbg_t6_ctx", "dbg_t4t6_activation_plus",
            "dbg_launch_cluster_count", "dbg_pending_phase", "dbg_pending_phase_count",
            "Z", "T", "L", "F", "FLY", "G", "B", "Combo", "ULT", "VOL", "VABS", "WICK",
            # ── Profile playbook (all from compute_profile_playbook_for_row) ──
            "profile_playbook_version",
            "profile_name", "profile_score", "profile_category",
            "sweet_spot_active", "late_warning",
            "bear_context_last_3", "bear_context_last_5",
            "bull_confirm_now", "bear_to_bull_confirmed",
            "bear_to_bull_bars_ago", "bear_to_bull_bonus", "bear_to_bull_pairs",
            "base_profile_score_without_btb", "category_without_btb", "category_with_btb",
            "btb_category_upgrade", "btb_created_sweet_spot",
            "btb_late_clamped", "btb_sweet_spot_allowed_profile",
            # ── ULTRA Score (independent additive ranking, no lookahead) ──────
            "ultra_score", "ultra_score_band", "ultra_score_reasons",
            "ultra_score_flags", "ultra_score_raw_before_penalty",
            "ultra_score_penalty_total",
            # ── ULTRA Score v2 calibration (replay-derived) ──────────────────
            "ultra_score_band_v2", "ultra_score_priority",
            "ultra_score_regime_bonus", "ultra_score_caps_applied",
            "ultra_score_cap_reason",
            # ── Additional fields used by ULTRA Replay combo analytics. Filled
            # opportunistically — empty when bar_signals doesn't expose them
            # (e.g. pullback_evidence_tier / abr_category / tz_intel_role
            # require running TZ Intelligence + Pullback / Rare miners which
            # would make Stock Stat much slower). Replay surfaces these as
            # 'missing dependency' rather than silent zero. ──
            "rs_strong", "rs",
            "tz_bull_flip", "tz_transition_present",
            "pullback_evidence_tier", "rare_evidence_tier",
            "tz_intel_role", "abr_category",
            # ── BETA Score ───────────────────────────────────────────────────
            "beta_score", "beta_raw", "beta_setup", "beta_momentum",
            "beta_excess", "beta_zone", "beta_auto_buy",
            # Decay-memory input — max ultra_score over past 5 bars (excludes current)
            "rolling_score_max_5d",
        ]

        def _j(lst): return " ".join(str(x) for x in lst) if lst else ""

        import sys as _sys

        # Audit counters for fail-loud check
        _audit = {
            "rows_total": 0,
            "rows_with_active_signals": 0,
            "rows_with_pf_score_gt_0": 0,
            "cat_dist": {},
            "rows_bear3": 0, "rows_bear5": 0,
            "rows_bull_now": 0, "rows_btb": 0,
            "btb_bonus_sum": 0.0,
            "sig_counts": {},
            "btb_pair_counts": {},
        }

        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            wr = csv.writer(fh)
            wr.writerow(headers)
            for idx, ticker in enumerate(tickers):
                try:
                    # Fetch ≥150 bars for warm-up; trim to requested window
                    effective_bars = max(bars, 150)
                    bd = api_bar_signals(ticker, tf, effective_bars)
                    if len(bd) > bars:
                        bd = bd[-bars:]
                    for b in bd:
                        tz = b.get("tz", "")
                        _audit["rows_total"] += 1
                        act = b.get("active_signals", [])
                        if act:
                            _audit["rows_with_active_signals"] += 1
                            for s in act:
                                _audit["sig_counts"][s] = _audit["sig_counts"].get(s, 0) + 1
                        pf_sc = b.get("profile_score", 0)
                        if pf_sc > 0:
                            _audit["rows_with_pf_score_gt_0"] += 1
                        cat = b.get("profile_category", "WATCH")
                        _audit["cat_dist"][cat] = _audit["cat_dist"].get(cat, 0) + 1
                        if b.get("bear_context_last_3"): _audit["rows_bear3"] += 1
                        if b.get("bear_context_last_5"): _audit["rows_bear5"] += 1
                        if b.get("bull_confirm_now"):     _audit["rows_bull_now"] += 1
                        if b.get("bear_to_bull_confirmed"):
                            _audit["rows_btb"] += 1
                            _audit["btb_bonus_sum"] += b.get("bear_to_bull_bonus", 0)
                            for p in b.get("bear_to_bull_pairs", []):
                                _audit["btb_pair_counts"][p] = _audit["btb_pair_counts"].get(p, 0) + 1
                        wr.writerow([
                            ticker,
                            b.get("date", ""),
                            round(b.get("open", 0), 4),
                            round(b.get("high", 0), 4),
                            round(b.get("low", 0), 4),
                            round(b.get("close", 0), 4),
                            round(b.get("volume", 0), 0),
                            b.get("vol_bucket", ""),
                            b.get("turbo_score", 0),
                            b.get("FINAL_BULL_SCORE", 0),
                            b.get("ROCKET_SCORE", 0),
                            b.get("CLEAN_ENTRY_SCORE", 0),
                            b.get("SHAKEOUT_ABSORB_SCORE", 0),
                            b.get("EXTRA_BULL_SCORE", 0),
                            b.get("EXPERIMENTAL_SCORE", 0),
                            b.get("REBOUND_SQUEEZE_SCORE", 0),
                            b.get("HARD_BEAR_SCORE", 0),
                            b.get("VOLATILITY_RISK_SCORE", 0),
                            b.get("HAS_ELITE_MODEL", 0),
                            b.get("HAS_REBOUND_MODEL", 0),
                            b.get("HAS_STRONG_BULL_MODEL", 0),
                            b.get("FINAL_REGIME", ""),
                            b.get("FINAL_SCORE_BUCKET", ""),
                            b.get("rtb_phase", ""),
                            b.get("rtb_total", 0),
                            b.get("rtb_transition", ""),
                            b.get("rtb_build", 0),
                            b.get("rtb_turn", 0),
                            b.get("rtb_ready", 0),
                            b.get("rtb_late", 0),
                            b.get("rtb_bonus3", 0),
                            1 if b.get("dbg_context_ready") else 0,
                            1 if b.get("dbg_t4_ctx") else 0,
                            1 if b.get("dbg_t6_ctx") else 0,
                            1 if b.get("dbg_t4t6_activation_plus") else 0,
                            b.get("dbg_launch_cluster_count", 0),
                            b.get("dbg_pending_phase", ""),
                            b.get("dbg_pending_phase_count", 0),
                            tz if tz.startswith("Z") else "",
                            tz if tz.startswith("T") else "",
                            _j(b.get("l", [])),
                            _j(b.get("f", [])),
                            _j(b.get("fly", [])),
                            _j(b.get("g", [])),
                            _j(b.get("b", [])),
                            _j([s for s in b.get("combo", []) if s not in _PREUP]),
                            _j(b.get("ultra", [])),
                            _j(b.get("vol", [])),
                            _j(b.get("vabs", [])),
                            _j(b.get("wick", [])),
                            # ── Profile playbook fields ────────────────────────
                            b.get("profile_playbook_version", ""),
                            b.get("profile_name", ""),
                            b.get("profile_score", 0),
                            b.get("profile_category", "WATCH"),
                            b.get("sweet_spot_active", 0),
                            b.get("late_warning", 0),
                            b.get("bear_context_last_3", 0),
                            b.get("bear_context_last_5", 0),
                            b.get("bull_confirm_now", 0),
                            b.get("bear_to_bull_confirmed", 0),
                            b.get("bear_to_bull_bars_ago", 0),
                            b.get("bear_to_bull_bonus", 0),
                            _j(b.get("bear_to_bull_pairs", [])),
                            b.get("base_profile_score_without_btb", 0),
                            b.get("category_without_btb", "WATCH"),
                            b.get("category_with_btb", "WATCH"),
                            b.get("btb_category_upgrade", 0),
                            b.get("btb_created_sweet_spot", 0),
                            b.get("btb_late_clamped", 0),
                            b.get("btb_sweet_spot_allowed_profile", 0),
                            # ── ULTRA Score (computed from current/past
                            # bar fields only — never reads forward returns).
                            # Adds 6 columns; missing input fields contribute 0.
                            *(lambda _r: (
                                _r["ultra_score"], _r["ultra_score_band"],
                                " ".join(_r["ultra_score_reasons"]),
                                " ".join(_r["ultra_score_flags"]),
                                _r["ultra_score_raw_before_penalty"],
                                _r["ultra_score_penalty_total"],
                                _r.get("ultra_score_band_v2", ""),
                                _r.get("ultra_score_priority", ""),
                                _r.get("ultra_score_regime_bonus", 0),
                                " ".join(_r.get("ultra_score_caps_applied", []) or []),
                                _r.get("ultra_score_cap_reason", ""),
                            ))(_compute_ultra_score(b)),
                            # ── Additional ULTRA Replay analytics fields ──
                            # rs_strong / tz_bull_flip derived from the same
                            # parser the score uses. pullback / rare / tz_intel
                            # / abr are passed through if upstream populated
                            # them, otherwise empty.
                            *(lambda _p: (
                                int(bool(_p["rs_strong"])),
                                int(bool(_p["rs"])),
                                int(bool(_p["tz_bull_flip"])),
                                int(bool(_p["tz_transition_present"])),
                                b.get("pullback_evidence_tier", "")
                                  or (b.get("pullback") or {}).get("evidence_tier", ""),
                                b.get("rare_evidence_tier", "")
                                  or (b.get("rare_reversal") or {}).get("evidence_tier", ""),
                                b.get("tz_intel_role", "")
                                  or (b.get("tz_intel") or {}).get("role", ""),
                                b.get("abr_category", "")
                                  or (b.get("abr") or {}).get("category", ""),
                            ))(_parse_ultra_signals(b)),
                            # ── BETA Score ─────────────────────────────────
                            b.get("beta_score", 0),
                            b.get("beta_raw", 0),
                            b.get("beta_setup", 0),
                            b.get("beta_momentum", 0),
                            b.get("beta_excess", 0),
                            b.get("beta_zone", ""),
                            1 if b.get("beta_auto_buy") else 0,
                            round(float(b.get("rolling_score_max_5d", 0) or 0), 1),
                        ])
                except Exception:
                    pass
                _stock_stat_state["done"] = idx + 1
                _stock_stat_state["elapsed"] = round(time.time() - t0, 1)

        # ── PROFILE_PLAYBOOK_AUDIT ────────────────────────────────────────────
        top20_sigs  = sorted(_audit["sig_counts"], key=lambda k: -_audit["sig_counts"][k])[:20]
        top20_pairs = sorted(_audit["btb_pair_counts"], key=lambda k: -_audit["btb_pair_counts"][k])[:20]
        avg_btb = (
            round(_audit["btb_bonus_sum"] / _audit["rows_btb"], 2)
            if _audit["rows_btb"] else 0
        )
        print(
            f"PROFILE_PLAYBOOK_AUDIT universe={universe} tf={tf}\n"
            f"  rows_total={_audit['rows_total']}\n"
            f"  rows_with_active_signals={_audit['rows_with_active_signals']}\n"
            f"  rows_with_pf_score_gt_0={_audit['rows_with_pf_score_gt_0']}\n"
            f"  category_distribution={_audit['cat_dist']}\n"
            f"  rows_bear_context_last_3={_audit['rows_bear3']}\n"
            f"  rows_bear_context_last_5={_audit['rows_bear5']}\n"
            f"  rows_bull_confirm_now={_audit['rows_bull_now']}\n"
            f"  rows_bear_to_bull_confirmed={_audit['rows_btb']}\n"
            f"  avg_bear_to_bull_bonus={avg_btb}\n"
            f"  top_20_extracted_signals={top20_sigs}\n"
            f"  top_20_bear_to_bull_pairs={top20_pairs}",
            file=_sys.stderr, flush=True,
        )
        # Fail-loud check
        if (_audit["rows_with_active_signals"] > 0
                and _audit["rows_with_pf_score_gt_0"] == 0):
            log.error(
                "PROFILE_PLAYBOOK_FAILURE: active signals found in %d rows "
                "but profile_score is zero for all rows. "
                "Check extraction/scoring integration.",
                _audit["rows_with_active_signals"],
            )

        # Config snapshot
        try:
            import json as _json
            from profile_playbook import get_playbook_config_snapshot
            snap_path = "stock_stat_output/profile_playbook_config_snapshot.json"
            with open(snap_path, "w", encoding="utf-8") as _sf:
                _json.dump(get_playbook_config_snapshot(), _sf, indent=2)
        except Exception as _snap_err:
            log.warning("Config snapshot failed: %s", _snap_err)

        fsize = os.path.getsize(out_path)
        _stock_stat_state.update(
            running=False, output_path=out_path,
            output_size=fsize, elapsed=round(time.time() - t0, 1),
            scoring_engine=SCORING_ENGINE_NAME,
            scoring_version=SCORING_ENGINE_VERSION,
            bars_used=bars,
        )
        try:
            from chart_obs_api_v2 import import_stock_stat_csv
            result = import_stock_stat_csv(out_path)
            log.info("Auto-imported stock_stat into DB: %s", result)
        except Exception as _imp_err:
            log.warning("stock_stat DB auto-import failed: %s", _imp_err)
    except Exception as e:
        _stock_stat_state.update(
            running=False, error=str(e),
            elapsed=round(time.time() - t0, 1)
        )


@app.post("/api/stock-stat/trigger")
def api_stock_stat_trigger(
    background_tasks: BackgroundTasks,
    tf: str = "1d", universe: str = "sp500", bars: int = 60,
):
    if _stock_stat_state["running"]:
        raise HTTPException(400, "Stock Stat scan already running")
    background_tasks.add_task(run_stock_stat, tf, universe, bars)
    return {"ok": True}


@app.get("/api/stock-stat/status")
def api_stock_stat_status():
    return _stock_stat_state


@app.get("/api/stock-stat/download")
def api_stock_stat_download():
    path = _stock_stat_state.get("output_path")
    if not path or not os.path.exists(path):
        raise HTTPException(404, "No output file — run a scan first")
    return FileResponse(path, media_type="text/csv", filename=os.path.basename(path))


# ── Sector Analysis ────────────────────────────────────────────────────────────
from sector_engine import (
    get_sector_overview,
    get_sector_detail,
    get_sector_rrg,
    get_sector_heatmap,
    get_macro_matrix,
)


def _sector_err(exc: Exception) -> dict:
    """Stable error envelope so sector endpoints never return raw 500 text."""
    return {
        "ok": False,
        "last_updated": round(__import__("time").time()),
        "data": None,
        "errors": [str(exc)],
    }


# Primary routes — /api/sectors/ (plural)
@app.get("/api/sectors/overview")
def api_sectors_overview():
    try:
        return get_sector_overview()
    except Exception as exc:
        return _sector_err(exc)


@app.get("/api/sectors/rrg")
def api_sectors_rrg(trail: int = 12):
    try:
        return get_sector_rrg(trail=trail)
    except Exception as exc:
        return _sector_err(exc)


@app.get("/api/sectors/heatmap")
def api_sectors_heatmap(metric: str = "return_1d"):
    try:
        return get_sector_heatmap(metric)
    except Exception as exc:
        return _sector_err(exc)


@app.get("/api/sectors/macro")
def api_sectors_macro():
    try:
        return get_macro_matrix()
    except Exception as exc:
        return _sector_err(exc)


# Must be registered AFTER the fixed-path routes above to avoid shadowing them
@app.get("/api/sectors/{etf}")
def api_sectors_detail(etf: str):
    try:
        return get_sector_detail(etf)
    except Exception as exc:
        return _sector_err(exc)


# Backward-compatible aliases — /api/sector/ (singular, kept for any existing callers)
@app.get("/api/sector/overview")
def api_sector_overview_alias():
    return api_sectors_overview()


@app.get("/api/sector/rrg")
def api_sector_rrg_alias(trail: int = 12):
    return api_sectors_rrg(trail=trail)


@app.get("/api/sector/heatmap")
def api_sector_heatmap_alias(metric: str = "return_1d"):
    return api_sectors_heatmap(metric=metric)


@app.get("/api/sector/detail/{ticker}")
def api_sector_detail_alias(ticker: str):
    return api_sectors_detail(ticker)


# ── Replay Analytics ──────────────────────────────────────────────────────────
import replay_engine as _re
import csv as _csv
import io as _io
from fastapi.responses import Response as _Response


@app.post("/api/replay/run")
def api_replay_run(background_tasks: BackgroundTasks, tf: str = "1d", universe: str = "sp500"):
    state = _re.get_state()
    if state.get("status") == "running":
        raise HTTPException(400, "Replay already running")
    background_tasks.add_task(_re.run_replay, tf, universe)
    return {"status": "started"}


@app.get("/api/replay/status")
def api_replay_status():
    return _re.get_state()


@app.get("/api/replay/reports")
def api_replay_reports():
    return {"reports": _re.get_report_list()}


@app.get("/api/replay/report/{name}")
def api_replay_report(name: str, page: int = 1, page_size: int = 500):
    data, err = _re.load_report(name, page, page_size)
    if err:
        raise HTTPException(404, err)
    return data


@app.get("/api/replay/export/{name}")
def api_replay_export(name: str):
    data, err = _re.load_report(name, 1, 999999)
    if err:
        raise HTTPException(404, err)
    rows = data.get("rows", [])
    if not rows:
        raise HTTPException(404, "No data for section")
    buf = _io.StringIO()
    w = _csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    return _Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="replay_{name}.csv"'},
    )


@app.get("/api/replay/export-all")
def api_replay_export_all():
    data = _re.export_zip()
    return _Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="replay_analytics.zip"'},
    )


# ── TZ/WLNBB Analyzer endpoints ──────────────────────────────────────────────

def _filter_nasdaq_batch(tickers: list, batch: str) -> list:
    """Filter NASDAQ tickers by alphabetical batch.
    batch='a_m' -> first letter A-M  (nasdaq)
    batch='n_z' -> first letter N-Z  (nasdaq)
    batch='a_f' -> first letter A-F  (nasdaq_gt5)
    batch='g_m' -> first letter G-M  (nasdaq_gt5)
    batch='n_s' -> first letter N-S  (nasdaq_gt5)
    batch='t_z' -> first letter T-Z  (nasdaq_gt5)
    batch='other' -> first letter non-alpha
    batch='' or 'all' -> no filter
    """
    if not batch or batch == "all":
        return tickers
    _RANGES = {
        "a_m": ("A", "M"), "n_z": ("N", "Z"),
        "a_f": ("A", "F"), "g_m": ("G", "M"),
        "n_s": ("N", "S"), "t_z": ("T", "Z"),
    }
    if batch in _RANGES:
        lo, hi = _RANGES[batch]
        return [t for t in tickers if t and t[0].upper().isalpha() and lo <= t[0].upper() <= hi]
    if batch == "other":
        return [t for t in tickers if not (t and t[0].upper().isalpha())]
    return tickers


def _tz_batch_stat_path(universe: str, tf: str, nasdaq_batch: str = "") -> str:
    """Return the canonical stock_stat CSV path for a given universe/tf/batch."""
    if nasdaq_batch and nasdaq_batch != "all":
        if universe == "nasdaq":
            return f"stock_stat_tz_wlnbb_nasdaq_{nasdaq_batch}_{tf}.csv"
        if universe == "nasdaq_gt5":
            return f"stock_stat_tz_wlnbb_nasdaq_gt5_{nasdaq_batch}_{tf}.csv"
    return f"stock_stat_tz_wlnbb_{universe}_{tf}.csv"


def _tz_batch_replay_path(universe: str, tf: str, nasdaq_batch: str = "") -> str:
    """Return the canonical replay ZIP path for a given universe/tf/batch."""
    if nasdaq_batch and nasdaq_batch != "all":
        if universe == "nasdaq":
            return f"replay_tz_wlnbb_nasdaq_{nasdaq_batch}_{tf}_analytics.zip"
        if universe == "nasdaq_gt5":
            return f"replay_tz_wlnbb_nasdaq_gt5_{nasdaq_batch}_{tf}_analytics.zip"
    return f"replay_tz_wlnbb_{universe}_{tf}_analytics.zip"


_tz_wlnbb_state: dict = {"running": False, "done": 0, "total": 0, "output": None, "error": None}


@app.get("/api/tz-wlnbb/scan")
def api_tz_wlnbb_scan(
    universe: str = "sp500",
    tf: str = "1d",
    min_price: float = 0,
    max_price: float = 1e9,
    min_volume: float = 0,
    signal_type: str = "all",
    signal_name: str = "",
    recent_window: int = 1,
    nasdaq_batch: str = "",
):
    """Return latest TZ/WLNBB signals from stock_stat CSV."""
    try:
        import csv as _csv
        stat_path = _tz_batch_stat_path(universe, tf, nasdaq_batch)
        if not os.path.exists(stat_path):
            # fallback to generic universe path
            stat_path = f"stock_stat_tz_wlnbb_{universe}_{tf}.csv"
        if not os.path.exists(stat_path):
            # last-resort fallback to old naming
            stat_path = f"stock_stat_tz_wlnbb_{tf}.csv"
        if not os.path.exists(stat_path):
            return {"results": [], "error": "No stock_stat_tz_wlnbb CSV found. Run generate-stock-stat first."}

        rows_by_ticker: dict = {}
        with open(stat_path, newline="", encoding="utf-8") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                if row.get("universe", "") != universe:
                    continue
                t = row.get("ticker", "")
                rows_by_ticker.setdefault(t, []).append(row)

        results = []
        for ticker, rows in rows_by_ticker.items():
            rows.sort(key=lambda x: x.get("date", ""))
            recent = rows[-recent_window:]
            for row in recent:
                try:
                    price = float(row.get("close", 0) or 0)
                    vol   = float(row.get("volume", 0) or 0)
                    if price < min_price or price > max_price:
                        continue
                    if min_volume > 0 and vol < min_volume:
                        continue
                    if signal_type not in ("all", ""):
                        has_sig = False
                        if signal_type == "T"     and row.get("t_signal"):          has_sig = True
                        if signal_type == "Z"     and row.get("z_signal"):          has_sig = True
                        if signal_type == "L"     and row.get("l_signal"):          has_sig = True
                        if signal_type == "PREUP" and row.get("preup_signal"):      has_sig = True
                        if signal_type == "PREDN" and row.get("predn_signal"):      has_sig = True
                        if signal_type == "Combo" and row.get("has_tz_l_combo") == "1": has_sig = True
                        if not has_sig:
                            continue
                    if signal_name:
                        if signal_name not in [
                            row.get("t_signal", ""), row.get("z_signal", ""),
                            row.get("l_signal", ""), row.get("preup_signal", ""),
                            row.get("predn_signal", ""),
                        ]:
                            continue
                    results.append(row)
                except Exception:
                    pass

        results.sort(key=lambda x: x.get("date", ""), reverse=True)
        return {"results": results[:2000]}
    except Exception as exc:
        log.exception("tz-wlnbb scan error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/tz-wlnbb/generate-stock-stat")
def api_tz_wlnbb_generate(
    background_tasks: BackgroundTasks,
    universe: str = "sp500",
    tf: str = "1d",
    bars: int = 500,
    nasdaq_batch: str = "",
):
    global _tz_wlnbb_state
    if _tz_wlnbb_state.get("running"):
        raise HTTPException(status_code=409, detail="Already running")
    if universe == "nasdaq" and not nasdaq_batch:
        nasdaq_batch = "a_m"  # safe default; full NASDAQ at once is too large
    if universe == "nasdaq" and nasdaq_batch == "all":
        raise HTTPException(
            status_code=400,
            detail="nasdaq_batch='all' is not allowed — NASDAQ is too large for a single run. "
                   "Use 'a_m' then 'n_z' separately.",
        )
    background_tasks.add_task(_run_tz_wlnbb_stock_stat, universe, tf, bars, nasdaq_batch)
    return {"status": "started", "nasdaq_batch": nasdaq_batch or None}


@app.post("/api/tz-wlnbb/stop")
def api_tz_wlnbb_stop():
    """Signal a running generate-stock-stat to stop after the current ticker."""
    _tz_wlnbb_state["stop_requested"] = True
    return {"ok": True, "message": "Stop requested"}


def _run_tz_wlnbb_stock_stat(universe: str, tf: str, bars: int, nasdaq_batch: str = ""):
    global _tz_wlnbb_state
    _tz_wlnbb_state = {
        "running": True, "done": 0, "total": 0, "output": None, "error": None,
        "stop_requested": False, "nasdaq_batch": nasdaq_batch or None,
    }
    try:
        from analyzers.tz_wlnbb.stock_stat import generate_stock_stat
        from scanner import get_universe_tickers

        # nasdaq_gt5 loads NASDAQ tickers and enforces close >= 5 during generation
        source_universe = "nasdaq" if universe == "nasdaq_gt5" else universe
        gen_min_price   = 5.0     if universe == "nasdaq_gt5" else 0.0

        if universe == "split":
            # Force-refresh the split service so we get the latest window,
            # write split_universe_latest.csv as the canonical reference, and
            # use exactly those tickers — no stale cache.
            from split_universe import split_service as _svc
            fresh = _svc.get_split_universe_result(force_refresh=True)
            tickers = list(fresh.tickers)
            log.info(
                "split universe: force-refreshed %d tickers "
                "(total_events=%d reverse=%d stock_like=%d filtered_non_stock=%d) "
                "for stock_stat generation",
                len(tickers), fresh.total_events, fresh.reverse_split_events,
                fresh.stock_like_events, fresh.filtered_non_stock,
            )
        else:
            try:
                tickers = get_universe_tickers(source_universe)
            except Exception:
                try:
                    from scanner import get_tickers
                    tickers = get_tickers() or []
                except Exception:
                    tickers = []

        if universe in ("nasdaq", "nasdaq_gt5") and nasdaq_batch and nasdaq_batch != "all":
            tickers = _filter_nasdaq_batch(tickers, nasdaq_batch)
            log.info("%s batch=%s: %d tickers after filter", universe, nasdaq_batch, len(tickers))
        if universe == "nasdaq_gt5":
            log.info("nasdaq_gt5: %d tickers loaded; price >= 5 filter will apply", len(tickers))

        _tz_wlnbb_state["total"] = len(tickers)

        # Prefer massive.com (fast, no rate-limits), fall back to yfinance
        from data_polygon import fetch_bars as _fetch_bars, polygon_available
        if polygon_available():
            def _fetch(ticker, interval, n_bars):
                # convert bars → calendar days (1.6× safety margin for weekends/holidays)
                days = max(int(n_bars * 1.6), 365)
                return _fetch_bars(ticker, interval=interval, days=days)
        else:
            from data import fetch_ohlcv as _fetch_yf
            def _fetch(ticker, interval, n_bars):
                return _fetch_yf(ticker, interval, n_bars)

        def _on_progress(done, total):
            _tz_wlnbb_state["done"] = done
            _tz_wlnbb_state["total"] = total

        def _should_stop():
            return bool(_tz_wlnbb_state.get("stop_requested"))

        out_path = _tz_batch_stat_path(universe, tf, nasdaq_batch)
        path, audit = generate_stock_stat(
            tickers, _fetch, universe=universe, tf=tf, bars=bars,
            min_price=gen_min_price,
            output_path=out_path,
            progress_callback=_on_progress,
            early_stop_fn=_should_stop,
        )
        _tz_wlnbb_state["output"] = path
        _tz_wlnbb_state["audit"] = audit
    except Exception as exc:
        log.exception("tz_wlnbb stock_stat generation failed")
        _tz_wlnbb_state["error"] = str(exc)
    finally:
        _tz_wlnbb_state["running"] = False


@app.get("/api/tz-wlnbb/status")
def api_tz_wlnbb_status():
    return _tz_wlnbb_state


@app.get("/api/tz-wlnbb/debug")
def api_tz_wlnbb_debug(ticker: str, date: str = "", tf: str = "1d", universe: str = "sp500"):
    """Return detailed signal breakdown for a specific ticker/date."""
    try:
        import csv as _csv
        stat_path = f"stock_stat_tz_wlnbb_{universe}_{tf}.csv"
        if not os.path.exists(stat_path):
            # fallback to old naming
            stat_path = f"stock_stat_tz_wlnbb_{tf}.csv"
        if not os.path.exists(stat_path):
            return {"error": "No stock_stat_tz_wlnbb CSV found."}

        with open(stat_path, newline="", encoding="utf-8") as f:
            reader = _csv.DictReader(f)
            rows = [r for r in reader if r.get("ticker", "").upper() == ticker.upper()]

        if not rows:
            return {"error": f"No data for {ticker}"}

        if date:
            rows = [r for r in rows if r.get("date", "") == date]
        else:
            rows = sorted(rows, key=lambda x: x.get("date", ""))[-1:]

        if not rows:
            return {"error": f"No data for {ticker} on {date}"}

        return {"ticker": ticker, "date": date, "rows": rows}
    except Exception as exc:
        log.exception("tz-wlnbb debug error")
        raise HTTPException(status_code=500, detail=str(exc))


# ── TZ/WLNBB Replay ──────────────────────────────────────────────────────────

_tz_replay_state: dict = {"running": False, "output": None, "error": None}


@app.post("/api/tz-wlnbb/replay")
def api_tz_wlnbb_replay(
    background_tasks: BackgroundTasks,
    universe: str = "sp500",
    tf: str = "1d",
    nasdaq_batch: str = "",
):
    global _tz_replay_state
    if _tz_replay_state.get("running"):
        raise HTTPException(status_code=409, detail="Replay already running")
    background_tasks.add_task(_run_tz_wlnbb_replay, universe, tf, nasdaq_batch)
    return {"status": "started", "nasdaq_batch": nasdaq_batch or None}


def _run_tz_wlnbb_replay(universe: str, tf: str, nasdaq_batch: str = ""):
    global _tz_replay_state
    _tz_replay_state = {"running": True, "output": None, "error": None, "nasdaq_batch": nasdaq_batch or None}
    try:
        import csv as _csv
        from analyzers.tz_wlnbb.replay import generate_replay_zip
        stat_path = _tz_batch_stat_path(universe, tf, nasdaq_batch)
        if not os.path.exists(stat_path):
            # fallback to old naming convention
            stat_path = f"stock_stat_tz_wlnbb_{universe}_{tf}.csv"
        if not os.path.exists(stat_path):
            # last-resort fallback
            stat_path = f"stock_stat_tz_wlnbb_{tf}.csv"
        if not os.path.exists(stat_path):
            _tz_replay_state["error"] = (
                f"{_tz_batch_stat_path(universe, tf, nasdaq_batch)} not found — "
                "run generate-stock-stat first"
            )
            return
        rows = []
        with open(stat_path, newline="", encoding="utf-8") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                rows.append(row)
        # Defensive ticker normalization: preserve string values like "NA",
        # coerce NaN-floats / missing to empty string.
        for row in rows:
            t = row.get("ticker")
            if t is None or (isinstance(t, float) and t != t):
                row["ticker"] = ""
            else:
                row["ticker"] = str(t)
        if not rows:
            _tz_replay_state["error"] = (
                f"TZ_WLNBB_ANALYZER_FAILURE: {stat_path} has zero data rows — "
                "run generate-stock-stat first and verify it completes successfully"
            )
            log.error(_tz_replay_state["error"])
            return
        ticker_count = len(set(r.get("ticker", "") for r in rows))
        log.info(
            "tz_wlnbb replay: loaded %d rows from %d tickers from %s (batch=%s)",
            len(rows), ticker_count, stat_path, nasdaq_batch or "none",
        )
        out = _tz_batch_replay_path(universe, tf, nasdaq_batch)
        generate_replay_zip(
            rows, output_path=out, universe=universe, tf=tf,
            ticker_count=ticker_count, nasdaq_batch=nasdaq_batch,
        )
        _tz_replay_state["output"] = out
    except Exception as exc:
        log.exception("tz_wlnbb replay failed")
        _tz_replay_state["error"] = str(exc)
    finally:
        _tz_replay_state["running"] = False


@app.get("/api/tz-wlnbb/replay/status")
def api_tz_wlnbb_replay_status():
    return _tz_replay_state


@app.get("/api/tz-wlnbb/download/{filename}")
def api_tz_wlnbb_download(filename: str):
    from fastapi.responses import FileResponse
    # Safety: only allow tz_wlnbb files
    if not (filename.startswith("replay_tz_wlnbb_") or filename.startswith("stock_stat_tz_wlnbb_")):
        raise HTTPException(status_code=403, detail="Not allowed")
    path = os.path.join(os.path.dirname(__file__), filename)
    if not os.path.exists(path):
        # Try current working directory
        path = filename
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=filename)


@app.get("/api/tz-intelligence/scan")
def api_tz_intelligence_scan(
    universe: str = "sp500",
    tf: str = "1d",
    nasdaq_batch: str = "",
    min_price: float = 0,
    max_price: float = 1e9,
    min_volume: float = 0,
    role_filter: str = "all",
    scan_mode: str = "latest",
    limit: int = 500,
    debug: bool = False,
):
    """Classify TZ/WLNBB bars using the Signal Intelligence matrix.

    scan_mode='latest'  — one result per ticker (most recent bar).
    scan_mode='history' — all historical classified events.
    """
    # nasdaq_gt5 always enforces close >= 5
    if universe == "nasdaq_gt5":
        min_price = max(min_price, 5.0)
    try:
        from tz_intelligence.scanner import run_intelligence_scan
        return run_intelligence_scan(
            universe=universe,
            tf=tf,
            nasdaq_batch=nasdaq_batch,
            min_price=min_price,
            max_price=max_price,
            min_volume=min_volume,
            role_filter=role_filter,
            scan_mode=scan_mode,
            limit=limit,
            debug=debug,
        )
    except Exception as exc:
        log.exception("tz-intelligence scan error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/pullback-miner/scan")
def api_pullback_miner_scan(
    universe:  str   = Query("sp500"),
    tf:        str   = Query("1d"),
    min_price: float = Query(0.0),
    max_price: float = Query(1e9),
    limit:     int   = Query(500),
):
    """
    Pullback Pattern Miner — Phase 1.

    Discovers 4-bar and 5-bar TZ/WLNBB pullback continuation patterns from
    the stock_stat CSV. Returns CONFIRMED_PULLBACK and ANECDOTAL_PULLBACK
    evidence tiers; top-3 per ticker sorted by tier then score.
    """
    try:
        from analyzers.pullback_miner.miner import run_pullback_scan
        return run_pullback_scan(
            universe=universe,
            tf=tf,
            min_price=min_price,
            max_price=max_price,
            limit=limit,
        )
    except Exception as exc:
        log.exception("pullback-miner scan error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/pullback-miner/report")
def api_pullback_miner_report():
    """
    Run pullback scan for SP500/1d and NASDAQ_GT5/1d and write output CSVs.
    Returns summary with output file paths, counts, and top-20 global patterns.
    """
    try:
        from analyzers.pullback_miner.miner import run_and_report
        result = run_and_report(
            universe_tf_pairs=[("sp500", "1d"), ("nasdaq_gt5", "1d")],
            out_dir=".",
        )
        return result
    except Exception as exc:
        log.exception("pullback-miner report error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/rare-reversal/scan")
def api_rare_reversal_scan(
    universe:  str   = Query("sp500"),
    tf:        str   = Query("1d"),
    min_price: float = Query(0.0),
    max_price: float = Query(1e9),
    limit:     int   = Query(200),
):
    """
    Mine rare bottom-reversal patterns from the stock_stat CSV.

    Extends each known 4-bar SEQ4 pattern left by 1–2 bars (ext5, ext6),
    measures bottom quality (sequence low vs 10/20-bar context), and returns
    evidence-tiered results (CONFIRMED_RARE, ANECDOTAL_RARE, FORMING_PATTERN).
    """
    try:
        from analyzers.rare_reversal.miner import run_rare_reversal_scan
        return run_rare_reversal_scan(
            universe=universe,
            tf=tf,
            min_price=min_price,
            max_price=max_price,
            limit=limit,
        )
    except Exception as exc:
        log.exception("rare-reversal scan error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/ultra-scan/trigger")
def api_ultra_scan_trigger(
    background_tasks: BackgroundTasks,
    universe:        str   = Query("sp500"),
    tf:              str   = Query("1d"),
    lookback_n:      int   = Query(5),
    partial_day:     bool  = Query(False),
    min_volume:      float = Query(0.0),
    min_store_score: float = Query(5.0),
    nasdaq_batch:    str   = Query(""),
):
    """ULTRA Stage 1: trigger a Turbo-only scan.

    Stage 2 enrichment (TZ/WLNBB stock_stat + TZ/WLNBB / TZ Intelligence /
    Pullback / Rare Reversal) is run separately via
    /api/ultra-scan/enrich for a chosen subset of tickers, so the heavy
    secondary modules never run for the full universe.

    Runs as a background task; poll /api/ultra-scan/status.
    """
    from ultra_orchestrator import get_ultra_status, run_ultra_scan_job
    if get_ultra_status().get("running"):
        raise HTTPException(status_code=409, detail="ULTRA scan already running")
    background_tasks.add_task(
        run_ultra_scan_job,
        universe=universe, tf=tf, lookback_n=lookback_n,
        partial_day=partial_day, min_volume=min_volume,
        min_store_score=min_store_score, nasdaq_batch=nasdaq_batch,
    )
    return {
        "status":       "ULTRA Stage 1 (Turbo) started",
        "stage":        "turbo",
        "universe":     universe,
        "tf":           tf,
        "nasdaq_batch": nasdaq_batch or None,
    }


class _UltraEnrichBody(BaseModel):
    universe:     str   = "sp500"
    tf:           str   = "1d"
    nasdaq_batch: str   = ""
    tickers:      list[str] = []
    direction:    str   = "all"   # accepted for future filtering; not enforced server-side
    min_price:    float = 0.0
    max_price:    float = 1e9
    min_volume:   float = 0.0
    stock_stat_bars: int = 500
    max_workers:  int   = 4


@app.post("/api/ultra-scan/enrich")
def api_ultra_scan_enrich(
    body: _UltraEnrichBody,
    background_tasks: BackgroundTasks,
):
    """ULTRA Stage 2: enrich a subset of tickers with TZ/WLNBB / TZ
    Intelligence / Pullback / Rare Reversal. Subset stock_stat is generated
    (or extracted from canonical) at an ULTRA-private path; the canonical
    stock_stat file is never overwritten."""
    from ultra_orchestrator import get_ultra_status, run_ultra_enrich_job
    if get_ultra_status().get("running"):
        raise HTTPException(status_code=409, detail="ULTRA scan/enrich already running")
    if not body.tickers:
        raise HTTPException(status_code=400, detail="tickers list is empty")
    background_tasks.add_task(
        run_ultra_enrich_job,
        tickers=body.tickers,
        universe=body.universe, tf=body.tf, nasdaq_batch=body.nasdaq_batch,
        min_price=body.min_price, max_price=body.max_price,
        min_volume=body.min_volume, stock_stat_bars=body.stock_stat_bars,
        max_workers=body.max_workers,
    )
    return {
        "status":   "ULTRA Stage 2 (enrich) started",
        "stage":    "enrich",
        "universe": body.universe,
        "tf":       body.tf,
        "tickers":  len(body.tickers),
    }


@app.get("/api/ultra-scan/enrich-status")
def api_ultra_scan_enrich_status():
    """Alias for /api/ultra-scan/status; convenient for the frontend's
    enrich-only progress polling."""
    from ultra_orchestrator import get_ultra_status
    return get_ultra_status()


@app.get("/api/ultra-scan/status")
def api_ultra_scan_status():
    from ultra_orchestrator import get_ultra_status
    return get_ultra_status()


@app.get("/api/ultra-scan/results")
def api_ultra_scan_results(
    universe:     str = Query("sp500"),
    tf:           str = Query("1d"),
    nasdaq_batch: str = Query(""),
):
    """Return the most recently merged ULTRA results for this (universe, tf,
    batch). Falls back to DB when memory cache is empty (survives restart)."""
    try:
        from ultra_orchestrator import get_ultra_results, load_latest_ultra_scan_from_db
        resp = get_ultra_results(universe=universe, tf=tf, nasdaq_batch=nasdaq_batch)
        # Memory cache miss — try loading persisted scan from DB
        if not resp.get("results"):
            try:
                if load_latest_ultra_scan_from_db(universe, tf, nasdaq_batch):
                    resp = get_ultra_results(universe=universe, tf=tf, nasdaq_batch=nasdaq_batch)
            except Exception as _db_exc:
                log.warning("ultra-scan/results DB fallback error: %s", _db_exc)
        return resp
    except Exception as exc:
        log.exception("ultra-scan/results error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/ultra-scan")
def api_ultra_scan(
    universe:     str = Query("sp500"),
    tf:           str = Query("1d"),
    nasdaq_batch: str = Query(""),
):
    """Convenience alias for /api/ultra-scan/results — returns the cached
    ULTRA response for this (universe, tf, batch). The main UX is
    trigger → status → results, like Turbo."""
    try:
        from ultra_orchestrator import get_ultra_results
        return get_ultra_results(universe=universe, tf=tf, nasdaq_batch=nasdaq_batch)
    except Exception as exc:
        log.exception("ultra-scan error")
        raise HTTPException(status_code=500, detail=str(exc))


# ─── Sequence scan ───────────────────────────────────────────────────────────
#
# Universe-wide N-bar T/Z sequence analyzer. Consumes the canonical
# stock_stat_tz_wlnbb / bulk Stock Stat CSV (no OHLCV re-fetch).
#
# State model intentionally mirrors Turbo / Stock Stat / Replay: a module-
# level dict for the live run + a per-cache-key dict for completed runs.
# We deliberately do NOT use SQLite here — the earlier SQLite path silently
# failed on Postgres (Railway), causing /status to fall through to "not_run"
# right after the worker finished. Plain in-memory keeps the screener in
# lockstep with the rest of the app and survives across status polls.

from datetime import datetime, timezone
from threading import Lock as _SeqLock
_seq_state: dict = {
    "running":      False,
    "cache_key":    None,
    "progress":     0,
    "total":        0,
    "started_at":   None,
    "completed_at": None,
    "error":        None,
    "params":       {},
    "stat_path":    None,
}
# Completed runs, keyed by cache_key. Holds the same shape /status expects
# plus 'results'. Capped to keep memory bounded.
_seq_results: dict[str, dict] = {}
_SEQ_RESULTS_CAP = 8
_seq_state_lock = _SeqLock()


def _seq_cache_key(universe: str, tf: str, seq_len: int, mode: str,
                   min_count: int, nasdaq_batch: str = "") -> str:
    parts = [universe, tf, str(seq_len), mode, str(min_count)]
    if nasdaq_batch:
        parts.append(nasdaq_batch)
    return "|".join(parts)


def _seq_store_completed(cache_key: str, payload: dict) -> None:
    """Save a completed run, evicting the oldest entry beyond the cap."""
    with _seq_state_lock:
        _seq_results[cache_key] = payload
        if len(_seq_results) > _SEQ_RESULTS_CAP:
            # Evict oldest by completed_at (or insertion order)
            try:
                victim = min(_seq_results.items(),
                             key=lambda kv: kv[1].get("completed_at") or "")[0]
                _seq_results.pop(victim, None)
            except Exception:
                # Fall back to popping the first key
                first_key = next(iter(_seq_results))
                _seq_results.pop(first_key, None)


def _run_sequence_scan_bg(cache_key: str, universe: str, tf: str,
                          seq_len: int, mode: str, min_count: int,
                          nasdaq_batch: str) -> None:
    """Background worker. Updates the live ``_seq_state`` for progress polls,
    and on completion stores the result in ``_seq_results[cache_key]`` so
    /status and /results can return it after the worker exits."""
    from sequence_engine import run_sequence_scan
    started = datetime.now(timezone.utc).isoformat()

    with _seq_state_lock:
        _seq_state.update({
            "running": True, "cache_key": cache_key,
            "progress": 0, "total": 0,
            "started_at": started, "completed_at": None, "error": None,
            "stat_path": None,
            "params": {
                "universe": universe, "tf": tf, "seq_len": seq_len,
                "mode": mode, "min_count": min_count,
                "nasdaq_batch": nasdaq_batch or None,
            },
        })

    def _on_progress(done: int, total: int) -> None:
        with _seq_state_lock:
            _seq_state["progress"] = done
            _seq_state["total"]    = total

    try:
        out = run_sequence_scan(
            universe=universe, tf=tf, seq_len=seq_len, mode=mode,
            min_count=min_count, nasdaq_batch=nasdaq_batch,
            progress_cb=_on_progress,
        )
    except Exception as exc:
        log.exception("sequence-scan worker crashed")
        out = {"status": "error", "error": str(exc), "results": []}

    completed = datetime.now(timezone.utc).isoformat()
    with _seq_state_lock:
        total = _seq_state["total"]
        done  = _seq_state["progress"] or total

    stat_path_used = out.get("stat_path") or (
        ", ".join(out.get("tried_paths") or []) or None
    )
    final_status = out.get("status") or "error"
    if final_status == "ok":
        final_status = "done"

    _seq_store_completed(cache_key, {
        "status":        final_status,
        "started_at":    started,
        "completed_at":  completed,
        "tickers_done":  done,
        "tickers_total": total,
        "error":         None if final_status == "done" else out.get("error"),
        "stat_path":     stat_path_used,
        "tried_paths":   out.get("tried_paths") or [],
        "results":       out.get("results") or [],
        "params": {
            "universe": universe, "tf": tf, "seq_len": seq_len,
            "mode": mode, "min_count": min_count,
            "nasdaq_batch": nasdaq_batch or None,
        },
    })

    with _seq_state_lock:
        _seq_state["running"]      = False
        _seq_state["completed_at"] = completed
        _seq_state["error"]        = out.get("error")
        _seq_state["stat_path"]    = stat_path_used


@app.post("/api/sequence-scan/trigger")
def api_sequence_scan_trigger(
    background_tasks: BackgroundTasks,
    universe:     str = Query("sp500"),
    tf:           str = Query("1d"),
    seq_len:      int = Query(4, ge=2, le=6),
    min_count:    int = Query(10, ge=1),
    mode:         str = Query("type"),
    nasdaq_batch: str = Query(""),
):
    """Start a universe-wide sequence scan over the existing TZ/WLNBB
    stock_stat / bulk Stock Stat CSV. Returns immediately; poll
    /api/sequence-scan/status for progress."""
    if mode not in ("type", "full"):
        raise HTTPException(400, "mode must be 'type' or 'full'")
    if _seq_state.get("running"):
        raise HTTPException(409, "Another sequence scan is already running")
    cache_key = _seq_cache_key(universe, tf, seq_len, mode, min_count, nasdaq_batch)
    background_tasks.add_task(
        _run_sequence_scan_bg, cache_key, universe, tf, seq_len, mode,
        min_count, nasdaq_batch,
    )
    return {"status": "started", "cache_key": cache_key}


@app.get("/api/sequence-scan/status")
def api_sequence_scan_status(
    universe:     str = Query("sp500"),
    tf:           str = Query("1d"),
    seq_len:      int = Query(4),
    min_count:    int = Query(10),
    mode:         str = Query("type"),
    nasdaq_batch: str = Query(""),
):
    """Live in-memory progress while a scan with this cache_key is running;
    cached completed-run state otherwise."""
    cache_key = _seq_cache_key(universe, tf, seq_len, mode, min_count, nasdaq_batch)
    with _seq_state_lock:
        live      = dict(_seq_state) if _seq_state.get("cache_key") == cache_key else None
        completed = dict(_seq_results.get(cache_key) or {}) or None
    if live and live.get("running"):
        total = max(int(live.get("total") or 1), 1)
        return {
            "status":     "running",
            "cache_key":  cache_key,
            "progress":   int(live.get("progress") or 0),
            "total":      int(live.get("total") or 0),
            "pct":        int((live.get("progress") or 0) / total * 100),
            "started_at": live.get("started_at"),
            "params":     live.get("params") or {},
        }
    if completed:
        total = max(int(completed.get("tickers_total") or 1), 1)
        return {
            "status":       completed.get("status") or "unknown",
            "cache_key":    cache_key,
            "progress":     int(completed.get("tickers_done") or 0),
            "total":        int(completed.get("tickers_total") or 0),
            "pct":          int((completed.get("tickers_done") or 0) / total * 100),
            "started_at":   completed.get("started_at"),
            "completed_at": completed.get("completed_at"),
            "error":        completed.get("error"),
            "stat_path":    completed.get("stat_path"),
            "tried_paths":  completed.get("tried_paths") or [],
            "params":       completed.get("params") or {},
        }
    return {"status": "not_run", "cache_key": cache_key,
            "progress": 0, "total": 0, "pct": 0}


@app.get("/api/sequence-scan/results")
def api_sequence_scan_results(
    universe:     str = Query("sp500"),
    tf:           str = Query("1d"),
    seq_len:      int = Query(4),
    min_count:    int = Query(10),
    mode:         str = Query("type"),
    nasdaq_batch: str = Query(""),
    limit:        int = Query(50, ge=1, le=10000),
    sort_by:      str = Query("score"),    # score|win_rate|count|ticker_count
):
    """Return cached top sequences for the given params."""
    cache_key = _seq_cache_key(universe, tf, seq_len, mode, min_count, nasdaq_batch)
    with _seq_state_lock:
        completed = dict(_seq_results.get(cache_key) or {})
    if not completed:
        return {"status": "not_run", "results": [], "total_sequences": 0,
                "cache_key": cache_key}
    results = list(completed.get("results") or [])

    sort_keys = {
        "win_rate":     lambda x: (-(x.get("win_rate")     or 0), -(x.get("count") or 0)),
        "count":        lambda x: (-(x.get("count")        or 0), -(x.get("win_rate") or 0)),
        "ticker_count": lambda x: (-(x.get("ticker_count") or 0), -(x.get("win_rate") or 0)),
        "score":        lambda x: (-(x.get("score")        or 0), -(x.get("count") or 0)),
        # Multi-horizon win-rate / avg-return sort options
        "win_rate_3d":  lambda x: (-(x.get("win_rate_3d")  or 0), -(x.get("count") or 0)),
        "win_rate_5d":  lambda x: (-(x.get("win_rate_5d")  or 0), -(x.get("count") or 0)),
        "win_rate_9d":  lambda x: (-(x.get("win_rate_9d")  or 0), -(x.get("count") or 0)),
        "avg_ret_1d":   lambda x: (-(x.get("avg_ret_1d")   or 0), -(x.get("count") or 0)),
        "avg_ret_3d":   lambda x: (-(x.get("avg_ret_3d")   or 0), -(x.get("count") or 0)),
        "avg_ret_5d":   lambda x: (-(x.get("avg_ret_5d")   or 0), -(x.get("count") or 0)),
        "avg_ret_9d":   lambda x: (-(x.get("avg_ret_9d")   or 0), -(x.get("count") or 0)),
    }
    results.sort(key=sort_keys.get(sort_by, sort_keys["score"]))

    return {
        "status":          completed.get("status") or "unknown",
        "cache_key":       cache_key,
        "completed_at":    completed.get("completed_at"),
        "results":         results[:limit],
        "total_sequences": len(results),
        "tickers_total":   int(completed.get("tickers_total") or 0),
        "stat_path":       completed.get("stat_path"),
        "tried_paths":     completed.get("tried_paths") or [],
        "error":           completed.get("error"),
        "params":          completed.get("params") or {},
    }


@app.get("/api/debug/compare-ultra-superchart")
def api_debug_compare_ultra_superchart(symbol: str, tf: str = "1d"):
    """
    Debug endpoint: compare the two pipelines side-by-side for a single symbol.

    Returns per-pipeline: bar count, last bar date, data source, key signal values,
    and turbo_score. Also returns a diff dict highlighting any mismatches.

    This endpoint is READ-ONLY and never modifies any scan state.
    """
    import traceback
    from data import fetch_ohlcv
    from signal_engine import compute_signals
    from wlnbb_engine import compute_wlnbb
    from canonical_scoring_engine import compute_canonical_score

    ticker = symbol.upper().strip()
    report: dict = {"symbol": ticker, "tf": tf, "pipelines": {}, "diff": {}}

    # ── Helper: compute last-bar signals from a DataFrame ─────────────────
    def _analyze_df(df, label: str, source: str) -> dict:
        try:
            sig_df = compute_signals(df)
        except Exception:
            sig_df = None
        try:
            wlnbb_df = compute_wlnbb(df)
        except Exception:
            wlnbb_df = None

        last_sig = sig_df.iloc[-1] if sig_df is not None and not sig_df.empty else {}
        last_w   = wlnbb_df.iloc[-1] if wlnbb_df is not None and not wlnbb_df.empty else {}

        tz_name = str(last_sig.get("sig_name", "")) if last_sig.get("is_bull") else ""
        l_sigs = []
        for col, lbl in [("L34","L34"),("L43","L43"),("L64","L64"),("L22","L22"),
                          ("FRI34","FRI34"),("FRI43","FRI43"),
                          ("BO_UP","BO↑"),("BX_UP","BX↑"),("BE_UP","BE↑"),
                          ("BLUE","BL"),("CCI_READY","CCI")]:
            if bool(last_w.get(col)):
                l_sigs.append(lbl)

        sig_row = {
            "tz_bull": bool(last_sig.get("is_bull")),
            "tz_sig":  tz_name,
            "conso_2809": bool(last_w.get("conso_2809") if hasattr(last_w,"get") else False),
            "bf_buy":  False,
            "rocket":  False,
            "bo_up":   bool(last_w.get("BO_UP")),
            "bx_up":   bool(last_w.get("BX_UP")),
            "be_up":   bool(last_w.get("BE_UP")),
            "l34":     bool(last_w.get("L34")),
            "fri34":   bool(last_w.get("FRI34")),
        }
        try:
            from combo_engine import compute_combo as _cc
            combo_df = _cc(df)
            if not combo_df.empty:
                lc = combo_df.iloc[-1]
                sig_row["conso_2809"] = bool(lc.get("conso_2809"))
                sig_row["rocket"]     = bool(lc.get("rocket"))
                sig_row["bf_buy"]     = bool(lc.get("buy_2809"))
        except Exception:
            pass

        try:
            canon = compute_canonical_score(sig_row)
            turbo_score = canon.get("turbo_score", 0)
        except Exception:
            turbo_score = None

        last_idx = df.index[-1]
        last_date = str(last_idx)[:10] if hasattr(last_idx, "__str__") else ""

        return {
            "label":       label,
            "source":      source,
            "bar_count":   len(df),
            "last_bar":    last_date,
            "first_bar":   str(df.index[0])[:10],
            "tz_signal":   tz_name or None,
            "l_signals":   l_sigs,
            "vol_bucket":  str(last_w.get("vol_bucket", "")) if hasattr(last_w,"get") else "",
            "turbo_score": turbo_score,
        }

    # ── Pipeline A: Superchart (yfinance, 150-bar default) ────────────────
    try:
        df_sc = fetch_ohlcv(ticker, interval=tf, bars=150)
        report["pipelines"]["superchart"] = _analyze_df(df_sc, "superchart", "yfinance")
    except Exception as exc:
        report["pipelines"]["superchart"] = {"error": str(exc)}

    # ── Pipeline B: Turbo-equivalent (Polygon → yfinance, 180-day window) ─
    try:
        df_turbo = None
        turbo_source = "yfinance"
        try:
            from data_polygon import fetch_bars, polygon_available
            if polygon_available():
                days = 400 if tf in ("1wk","1w") else 180 if tf == "1d" else 90
                df_turbo = fetch_bars(ticker, interval=tf, days=days)
                turbo_source = "polygon"
        except Exception:
            pass
        if df_turbo is None or df_turbo.empty:
            import yfinance as yf
            period = "5y" if tf in ("1wk","1w") else "180d" if tf == "1d" else "60d"
            raw = yf.Ticker(ticker).history(period=period, interval=tf, auto_adjust=True)
            raw.columns = [str(c).lower() for c in raw.columns]
            df_turbo = raw[["open","high","low","close","volume"]].dropna()
            turbo_source = "yfinance"
        report["pipelines"]["turbo"] = _analyze_df(df_turbo, "turbo", turbo_source)
    except Exception as exc:
        report["pipelines"]["turbo"] = {"error": str(exc), "traceback": traceback.format_exc()}

    # ── Diff ──────────────────────────────────────────────────────────────
    sc = report["pipelines"].get("superchart", {})
    tu = report["pipelines"].get("turbo", {})
    diff = {}
    if "error" not in sc and "error" not in tu:
        if sc.get("last_bar") != tu.get("last_bar"):
            diff["last_bar"] = {"superchart": sc["last_bar"], "turbo": tu["last_bar"],
                                "note": "Different latest bar — partial-day trim mismatch or source lag"}
        if sc.get("source") != tu.get("source"):
            diff["data_source"] = {"superchart": sc["source"], "turbo": tu["source"],
                                   "note": "Different OHLCV source — adjusted prices may differ"}
        sc_bars, tu_bars = sc.get("bar_count",0), tu.get("bar_count",0)
        if abs(sc_bars - tu_bars) > 5:
            diff["bar_count"] = {"superchart": sc_bars, "turbo": tu_bars,
                                 "note": "Different lookback windows — rolling indicators will diverge"}
        if sc.get("tz_signal") != tu.get("tz_signal"):
            diff["tz_signal"] = {"superchart": sc.get("tz_signal"), "turbo": tu.get("tz_signal"),
                                 "note": "T/Z signal differs on last bar"}
        sc_l = set(sc.get("l_signals", []))
        tu_l = set(tu.get("l_signals", []))
        if sc_l != tu_l:
            diff["l_signals"] = {
                "superchart_only": sorted(sc_l - tu_l),
                "turbo_only":      sorted(tu_l - sc_l),
                "note": "L/structure signals differ — likely due to bar-count or source mismatch",
            }
        sc_s = sc.get("turbo_score")
        tu_s = tu.get("turbo_score")
        if sc_s is not None and tu_s is not None and abs((sc_s or 0) - (tu_s or 0)) > 2:
            diff["turbo_score"] = {"superchart": sc_s, "turbo": tu_s,
                                   "delta": round((sc_s or 0) - (tu_s or 0), 1)}
    report["diff"] = diff
    report["mismatch_count"] = len(diff)
    report["root_causes"] = [v["note"] for v in diff.values() if isinstance(v, dict) and "note" in v]
    return report


_static = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static):
    app.mount("/", StaticFiles(directory=_static, html=True), name="static")
"""
engine_registry.py — Phase 8G: unified scanner pipeline.

Single entrypoint. Receives raw OHLCV, runs the shared indicator builder, then
runs every registered engine on the same dataframe, and emits a list of
normalized per-bar objects matching unified_schema.build_bar().

All consumers — scan latest, chart history, filters, exports — read this same
output. Engines are wrapped in try/except so an optional engine failure does
not crash the scan; the failure is recorded in bar.engine_debug.

No formula changes. Engines stay verbatim; this file is only orchestration.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import pandas as pd

from .indicator_builder import build_indicators
from .unified_schema import build_bar, ROW_ORDER

log = logging.getLogger("scanner.engine_registry")


# ── Lazy imports of engine compute functions ─────────────────────────────────
# Engines are imported inside run_engines() so that a missing/broken engine
# can be reported but doesn't break module import for unrelated callers.


def _safe_call(fn: Callable[..., pd.DataFrame], *args, **kwargs) -> pd.DataFrame | None:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning("engine %s raised: %s", getattr(fn, "__name__", fn), exc)
        return None


# ── Signal routing: per-engine column→(row, label) maps ──────────────────────
#
# Each engine returns a DataFrame of boolean columns. The mapping below tells
# the registry which display row each badge belongs to and what label to render.
# Routing matches the old SuperchartPanel.jsx behavior.

def _build_routing() -> dict[str, list[tuple[str, str, str]]]:
    """
    Returns a dict: engine_name -> list of (df_column, row_key, display_label).
    Imported lazily so a missing engine module doesn't break registry import.
    """
    routing: dict[str, list[tuple[str, str, str]]] = {}

    # TZ — chart_signal_engine.compute_signals already emits sig_name strings,
    # so it is handled separately in run_engines (see below).

    # WLNBB — handled separately because chart_wlnbb_engine returns label strings
    # rather than boolean columns per signal.

    try:
        from .chart_vabs_engine import VABS_SIG_COLS
        routing["vabs"] = [(c, "vabs", lbl) for c, lbl in VABS_SIG_COLS]
    except Exception as exc:
        log.warning("vabs routing unavailable: %s", exc)

    try:
        from .chart_wick_engine import WICK_SIG_COLS
        routing["wick"] = [(c, "wick", lbl) for c, lbl in WICK_SIG_COLS]
    except Exception as exc:
        log.warning("wick routing unavailable: %s", exc)

    try:
        from .chart_combo_engine import COMBO_SIG_COLS, COMBO_PREUP_COLS
        combo_rows: list[tuple[str, str, str]] = []
        for col, label in COMBO_SIG_COLS:
            row = "z" if col in COMBO_PREUP_COLS else "i"
            combo_rows.append((col, row, label))
        routing["combo"] = combo_rows
    except Exception as exc:
        log.warning("combo routing unavailable: %s", exc)

    try:
        from .chart_f_engine import F_SIG_COLS
        routing["f"] = [(c, "f", lbl) for c, lbl in F_SIG_COLS]
    except Exception as exc:
        log.warning("f routing unavailable: %s", exc)

    try:
        from .chart_fly_engine import FLY_SIG_COLS
        routing["fly"] = [(c, "fly", lbl) for c, lbl in FLY_SIG_COLS]
    except Exception as exc:
        log.warning("fly routing unavailable: %s", exc)

    try:
        from .chart_b_engine import B_SIG_COLS, G_SIG_COLS
        routing["b"] = [(c, "b", lbl) for c, lbl in B_SIG_COLS]
        routing["g"] = [(c, "g", lbl) for c, lbl in G_SIG_COLS]
    except Exception as exc:
        log.warning("b/g routing unavailable: %s", exc)

    try:
        from .chart_ultra_engine import ULT_SIG_COLS
        routing["ult"] = [(c, "ult", lbl) for c, lbl in ULT_SIG_COLS]
    except Exception as exc:
        log.warning("ult routing unavailable: %s", exc)

    try:
        from .chart_gog_engine import GOG_SETUP_COLS, GOG_TIER_COLS, GOG_CTX_COLS
        routing["gog_setup"] = [(c, "setup", lbl) for c, lbl in GOG_SETUP_COLS]
        routing["gog_tier"]  = [(c, "gog",   lbl) for c, lbl in GOG_TIER_COLS]
        routing["gog_ctx"]   = [(c, "ctx",   lbl) for c, lbl in GOG_CTX_COLS]
    except Exception as exc:
        log.warning("gog routing unavailable: %s", exc)

    return routing


# ── Main entrypoint ──────────────────────────────────────────────────────────

def run_engines(
    *,
    ticker: str,
    timeframe: str,
    df: pd.DataFrame,
) -> list[dict[str, Any]]:
    """
    Run the full registered engine pipeline on a single ticker's OHLCV.

    Returns a list of normalized per-bar dicts (one per row of df, in
    chronological order). The last item == latest bar.

    Failures in optional engines populate bar["engine_debug"]["engines_failed"]
    but never raise.
    """
    if df is None or df.empty:
        return []

    # 1. Shared indicator dataframe (computed once).
    try:
        idf = build_indicators(df)
    except Exception as exc:
        log.error("indicator_builder failed for %s: %s", ticker, exc)
        return []

    engines_ran: list[str] = []
    engines_failed: list[str] = []
    warnings: list[str] = []

    # 2. Run engines (lazy imports). Each returns a DataFrame aligned to idf.index.
    tz_df    = _run_tz(idf, engines_ran, engines_failed)
    wl_df    = _run_wlnbb(idf, engines_ran, engines_failed)
    vabs_df  = _run_vabs(idf, engines_ran, engines_failed)
    wick_df  = _run_wick(idf, engines_ran, engines_failed)
    combo_df = _run_combo(idf, engines_ran, engines_failed)
    f_df     = _run_engine(idf, engines_ran, engines_failed, "f",   _import_f)
    fly_df   = _run_engine(idf, engines_ran, engines_failed, "fly", _import_fly)
    b_df     = _run_engine(idf, engines_ran, engines_failed, "b",   _import_b)
    g_df     = _run_engine(idf, engines_ran, engines_failed, "g",   _import_g)
    u260_df  = _run_engine(idf, engines_ran, engines_failed, "ult260", _import_u260)
    uv2_df   = _run_engine(idf, engines_ran, engines_failed, "ult_v2", _import_uv2)

    # GOG depends on every prior engine's output. Run last; pass None safely.
    gog_df = _run_gog(idf, tz_df, wl_df, f_df, vabs_df, u260_df, uv2_df, combo_df,
                     engines_ran, engines_failed)

    # 2b. Split / reverse-split flags (per-ticker, same on every bar).
    split_flags = _resolve_split_flags(ticker, engines_ran, engines_failed)

    routing = _build_routing()

    # 3. Build per-bar normalized objects.
    bars: list[dict[str, Any]] = []
    for ts, row in idf.iterrows():
        date_str         = _ts_date(ts)
        display_date_str = _ts_display(ts, timeframe)
        dt_iso           = _ts_iso(ts)

        bar = build_bar(
            ticker=ticker,
            timeframe=timeframe,
            date=date_str,
            display_date=display_date_str,
            datetime_iso=dt_iso,
        )
        bar["engine_debug"]["engines_ran"]    = list(engines_ran)
        bar["engine_debug"]["engines_failed"] = list(engines_failed)
        bar["engine_debug"]["warnings"]       = list(warnings)

        # Split flags (constant per ticker for this scan)
        if split_flags is not None:
            bar["split"] = dict(split_flags)

        # OHLCV + indicators
        ohlcv = bar["ohlcv"]
        ohlcv["open"]   = _f(row.get("open"))
        ohlcv["high"]   = _f(row.get("high"))
        ohlcv["low"]    = _f(row.get("low"))
        ohlcv["close"]  = _f(row.get("close"))
        ohlcv["volume"] = _f(row.get("volume"))

        ind = bar["indicators"]
        ind["rsi"]          = _f(row.get("rsi"))
        ind["cci"]          = _f(row.get("cci"))
        ind["atr"]          = _f(row.get("atr"))
        ind["ema8"]         = _f(row.get("ema8"))
        ind["ema13"]        = _f(row.get("ema13"))
        ind["ema21"]        = _f(row.get("ema21"))
        ind["ema20"]        = _f(row.get("ema20")) if "ema20" in row else None
        ind["ema34"]        = _f(row.get("ema34"))
        ind["ema50"]        = _f(row.get("ema50"))
        ind["ema89"]        = _f(row.get("ema89"))
        ind["ema200"]       = _f(row.get("ema200"))
        ind["bb_upper"]     = _f(row.get("bb_upper"))
        ind["bb_mid"]       = _f(row.get("bb_basis"))
        ind["bb_lower"]     = _f(row.get("bb_lower"))
        ind["volume_ma"]    = _f(row.get("vol_ma"))
        ind["volume_z"]     = _f(row.get("vol_z"))
        ind["volume_ratio"] = _f(row.get("vol_ratio"))
        ind["body_pct"]     = _f(row.get("body_pct"))
        ind["upper_wick_pct"] = _f(row.get("up_pct"))
        ind["lower_wick_pct"] = _f(row.get("lo_pct"))

        # Signals — TZ
        if tz_df is not None and ts in tz_df.index:
            tz_row = tz_df.loc[ts]
            sig_name = tz_row.get("sig_name")
            if isinstance(sig_name, str) and sig_name and sig_name != "NONE":
                if sig_name.startswith("T"):
                    bar["signals"]["t"].append(sig_name)
                elif sig_name.startswith("Z"):
                    bar["signals"]["z"].append(sig_name)

        # Signals — WLNBB
        if wl_df is not None and ts in wl_df.index:
            wl_row = wl_df.loc[ts]
            l_combo = wl_row.get("l_combo")
            if isinstance(l_combo, str) and l_combo.strip():
                for label in l_combo.split():
                    _route_wlnbb_label(label, bar["signals"])

        # Signals — VABS / WICK / COMBO / F / FLY / B / G via routing table
        _apply_routing(vabs_df,  ts, routing.get("vabs",  []), bar)
        _apply_routing(wick_df,  ts, routing.get("wick",  []), bar)
        _apply_routing(combo_df, ts, routing.get("combo", []), bar)
        _apply_routing(f_df,     ts, routing.get("f",     []), bar)
        _apply_routing(fly_df,   ts, routing.get("fly",   []), bar)
        _apply_routing(b_df,     ts, routing.get("b",     []), bar)
        _apply_routing(g_df,     ts, routing.get("g",     []), bar)
        # ULT row — merge 260308/L88 (u260_df) + ULTRA v2 (uv2_df) columns
        if u260_df is not None and ts in u260_df.index:
            row = u260_df.loc[ts]
            for col, _row_key, label in routing.get("ult", []):
                if col in u260_df.columns and bool(row.get(col, False)):
                    bar["signals"]["ult"].append(label)
        if uv2_df is not None and ts in uv2_df.index:
            row = uv2_df.loc[ts]
            for col, _row_key, label in routing.get("ult", []):
                if col in uv2_df.columns and bool(row.get(col, False)):
                    bar["signals"]["ult"].append(label)
        # GOG -> SETUP, GOG, CTX rows
        _apply_routing(gog_df, ts, routing.get("gog_setup", []), bar)
        _apply_routing(gog_df, ts, routing.get("gog_tier",  []), bar)
        _apply_routing(gog_df, ts, routing.get("gog_ctx",   []), bar)

        # GOG_SCORE -> bar.scores (when available)
        if gog_df is not None and ts in gog_df.index:
            gog_row = gog_df.loc[ts]
            gs = gog_row.get("GOG_SCORE")
            if gs is not None and gs == gs:  # not NaN
                bar["scores"]["score_reason"] = f"GOG_TIER={gog_row.get('GOG_TIER','')}"
        # Tier-derived RTB phase + turbo_score alias
        try:
            from .chart_rtb_engine import fill_scores_from_bar
            fill_scores_from_bar(bar)
        except Exception as exc:
            log.warning("rtb fill failed: %s", exc)

        bars.append(bar)

    return bars


# ── Engine runners ───────────────────────────────────────────────────────────

def _run_tz(idf, ran, failed):
    try:
        from .chart_signal_engine import compute_signals as _compute_tz
        out = _compute_tz(idf)
        ran.append("tz")
        return out
    except Exception as exc:
        log.warning("tz engine failed: %s", exc)
        failed.append("tz")
        return None


def _run_wlnbb(idf, ran, failed):
    try:
        from .chart_wlnbb_engine import compute_wlnbb as _compute_wl
        out = _compute_wl(idf)
        ran.append("wlnbb")
        return out
    except Exception as exc:
        log.warning("wlnbb engine failed: %s", exc)
        failed.append("wlnbb")
        return None


def _run_vabs(idf, ran, failed):
    try:
        from .chart_vabs_engine import compute_vabs as _compute_vabs
        out = _compute_vabs(idf)
        ran.append("vabs")
        return out
    except Exception as exc:
        log.warning("vabs engine failed: %s", exc)
        failed.append("vabs")
        return None


def _run_wick(idf, ran, failed):
    try:
        from .chart_wick_engine import compute_wick as _compute_wick
        out = _compute_wick(idf)
        ran.append("wick")
        return out
    except Exception as exc:
        log.warning("wick engine failed: %s", exc)
        failed.append("wick")
        return None


def _resolve_split_flags(ticker, ran, failed):
    """
    Look up reverse-split lifecycle flags for the ticker. Network failure
    inside split_service returns a clean empty-split dict — never raises.
    """
    try:
        from .split_universe import get_split_flags_for_ticker
        out = get_split_flags_for_ticker(ticker)
        ran.append("split")
        return out
    except Exception as exc:
        log.warning("split lookup failed for %s: %s", ticker, exc)
        failed.append("split")
        return None


def _run_combo(idf, ran, failed):
    try:
        from .chart_combo_engine import compute_combo as _compute_combo
        out = _compute_combo(idf)
        ran.append("combo")
        return out
    except Exception as exc:
        log.warning("combo engine failed: %s", exc)
        failed.append("combo")
        return None


def _run_engine(idf, ran, failed, name, importer):
    """Generic engine runner — `importer` returns the compute fn."""
    try:
        fn = importer()
        out = fn(idf)
        ran.append(name)
        return out
    except Exception as exc:
        log.warning("%s engine failed: %s", name, exc)
        failed.append(name)
        return None


def _import_f():
    from .chart_f_engine import compute_f_signals
    return compute_f_signals


def _import_fly():
    from .chart_fly_engine import compute_fly_series
    return compute_fly_series


def _import_b():
    from .chart_b_engine import compute_b_signals
    return compute_b_signals


def _import_g():
    from .chart_b_engine import compute_g_signals
    return compute_g_signals


def _import_u260():
    from .chart_ultra_engine import compute_260308_l88
    return compute_260308_l88


def _import_uv2():
    from .chart_ultra_engine import compute_ultra_v2
    return compute_ultra_v2


def _run_gog(idf, tz_df, wl_df, f_df, vabs_df, u260_df, uv2_df, combo_df,
             ran, failed):
    """GOG depends on outputs of every prior engine."""
    try:
        from .chart_gog_engine import compute_gog_signals
        out = compute_gog_signals(idf, wl_df, tz_df, f_df, vabs_df,
                                  u260_df, uv2_df, combo_df)
        ran.append("gog")
        return out
    except Exception as exc:
        log.warning("gog engine failed: %s", exc)
        failed.append("gog")
        return None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _apply_routing(engine_df, ts, routes, bar):
    if engine_df is None or ts not in engine_df.index:
        return
    row = engine_df.loc[ts]
    for col, row_key, label in routes:
        if bool(row.get(col, False)):
            bar["signals"][row_key].append(label)


# WLNBB labels need to be split into L row vs CTX row vs F row depending on label.
_WL_F_LABELS    = frozenset({"FRI34", "FRI43", "FRI64"})
_WL_CTX_LABELS  = frozenset({"CCI", "CCIB", "CCI0R", "CCIR", "CCI✓"})

def _route_wlnbb_label(label: str, signals: dict[str, list[str]]) -> None:
    if label in _WL_F_LABELS:
        signals["f"].append(label)
    elif label in _WL_CTX_LABELS:
        signals["ctx"].append(label)
    else:
        signals["l"].append(label)


def _f(v):
    """Float-or-None coercion that NaN-safes."""
    try:
        if v is None:
            return None
        fv = float(v)
        if fv != fv:  # NaN
            return None
        return fv
    except (TypeError, ValueError):
        return None


def _ts_date(ts) -> str:
    try:
        return pd.Timestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)


def _ts_display(ts, timeframe: str) -> str:
    try:
        t = pd.Timestamp(ts)
        if timeframe and timeframe.endswith(("h", "m")):
            return t.strftime("%m-%d %H:%M")
        return t.strftime("%m-%d")
    except Exception:
        return str(ts)


def _ts_iso(ts) -> str:
    try:
        return pd.Timestamp(ts).isoformat()
    except Exception:
        return str(ts)


__all__ = ["run_engines"]

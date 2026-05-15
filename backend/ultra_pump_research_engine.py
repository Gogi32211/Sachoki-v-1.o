"""
ultra_pump_research_engine.py — Orchestrator for the ULTRA Pump Research Engine.

Daily 1D only. ULTRA is the primary research object — T/Z is context.

Phase 1: foundation
  - run metadata table (ultra_pump_runs)
  - background thread + progress state
  - pump episode detection (X2→X4 / X4+) with anchor deduplication
  - basic caught/missed classification using the ULTRA scanner output (when
    scanner snapshots are unavailable for historical dates this is a binary
    "scanner_score_ge_threshold" proxy that future phases will refine)
  - empty parquet artifacts written for every expected slot, with
    `reason_if_empty` populated when zero rows
"""
from __future__ import annotations
import hashlib
import json
import logging
import threading
import time
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Any

from db import get_db, USE_PG

log = logging.getLogger(__name__)

# Module-level state (single concurrent run, mirrors signal_replay_engine)
_state: dict[str, Any] = {
    "running":             False,
    "run_id":              None,
    "status":              "idle",
    "phase":               "idle",
    "phase_message":       "",
    "mode":                None,
    "universe":            None,
    "pump_target":         None,
    "symbols_total":       0,
    "symbols_completed":   0,
    "episodes_found":      0,
    "x2_to_x4_count":      0,
    "x4_plus_count":       0,
    "caught_count":        0,
    "missed_count":        0,
    "started_at":          None,
    "elapsed_secs":        0,
    "error":               None,
    "stop_requested":      False,
    "pause_requested":     False,
    "run_warnings":        [],
}

_lock = threading.Lock()

# ── Constants ────────────────────────────────────────────────────────────────

X2_TO_X4_MIN = 100.0   # max_gain_pct >= 100 and < 300
X4_PLUS_MIN  = 300.0   # max_gain_pct >= 300
DEFAULT_PUMP_HORIZON           = 60   # trading days
DEFAULT_PRE_PUMP_WINDOW_BARS   = 14
DEFAULT_SCANNER_WINDOW_BARS    = 14
DEFAULT_DETECTION_REFERENCE    = "before_first_x2_else_before_peak"
DEFAULT_LOOKBACK_BARS          = 500
DEFAULT_SPLIT_IMPACT_WINDOW    = 30

ALLOWED_HORIZONS               = {20, 30, 60, 90, 120}
ALLOWED_PRE_PUMP_WINDOW        = {5, 7, 10, 14, 20}
ALLOWED_SCANNER_WINDOW         = {3, 5, 7, 10, 14, 20}
ALLOWED_LOOKBACK_BARS          = {30, 100, 250, 500, 1000}
ALLOWED_SPLIT_IMPACT_WINDOW    = {10, 20, 30, 60}

EPISODE_DEDUP_DAYS = 20

# Required artifact slots — every run produces ALL of these (empty schema OK)
ARTIFACT_SLOTS_PARQUET = [
    "pump_episodes",
    "x2_to_x4_episodes",
    "x4_plus_episodes",
    "scanner_caught_pumps",
    "missed_pumps",
    "pre_pump_ultra_bars",
    "pre_pump_ultra_signals",
    "pre_pump_ultra_combinations",
    "missed_diagnostics",
    "ultra_pattern_stats",
    "ultra_pattern_lift_stats",
    "ultra_timing_stats",
    "baseline_windows",
    "baseline_ultra_patterns",
    "baseline_pattern_stats",
    "split_impact_stats",
    "split_related_pumps",
    "clean_non_split_pumps",
    "post_reverse_split_pumps",
]

ARTIFACT_SLOTS_JSON = [
    "run",
    "progress",
    "export_manifest",
    "warnings",
    "research_bundle",
    "ultra_recommendations",
]


# ── State helpers ────────────────────────────────────────────────────────────

def get_state() -> dict:
    return dict(_state)


def _set(**kv) -> None:
    with _lock:
        _state.update(kv)


def request_stop() -> None:
    _state["stop_requested"] = True


def request_pause() -> None:
    _state["pause_requested"] = True


def request_resume() -> None:
    _state["pause_requested"] = False


def _ph() -> str:
    return "%s" if USE_PG else "?"


# ── DB helpers ────────────────────────────────────────────────────────────────

def _insert_run(payload: dict) -> int:
    cols = [
        "status", "universe", "mode", "pump_target",
        "start_date", "end_date",
        "pump_horizon", "pre_pump_window_bars",
        "scanner_detection_window_bars", "detection_reference",
        "lookback_bars", "split_impact_window_days",
        "research_mode", "event_scope",
        "min_price", "min_volume", "min_dollar_volume",
        "benchmark_symbol", "settings_json", "storage_mode",
    ]
    vals = [
        "running",
        payload.get("universe", "all_us"),
        payload.get("mode", "date_range"),
        payload.get("pump_target", "X2_TO_X4"),
        payload.get("start_date"),
        payload.get("end_date"),
        int(payload.get("pump_horizon") or DEFAULT_PUMP_HORIZON),
        int(payload.get("pre_pump_window_bars") or DEFAULT_PRE_PUMP_WINDOW_BARS),
        int(payload.get("scanner_detection_window_bars") or DEFAULT_SCANNER_WINDOW_BARS),
        payload.get("detection_reference") or DEFAULT_DETECTION_REFERENCE,
        int(payload.get("lookback_bars") or DEFAULT_LOOKBACK_BARS),
        int(payload.get("split_impact_window_days") or DEFAULT_SPLIT_IMPACT_WINDOW),
        payload.get("research_mode", "standard"),
        payload.get("event_scope", "pumps_only"),
        payload.get("min_price"),
        payload.get("min_volume"),
        payload.get("min_dollar_volume"),
        payload.get("benchmark_symbol", "QQQ"),
        json.dumps(payload, default=str),
        "parquet",
    ]
    ph = _ph()
    sql = (f"INSERT INTO ultra_pump_runs ({', '.join(cols)}) "
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
    sql = f"UPDATE ultra_pump_runs SET {sets} WHERE id={ph}"
    vals = list(fields.values()) + [run_id]
    try:
        with get_db() as db:
            db.execute(sql, vals)
            db.commit()
    except Exception as exc:
        log.warning("ultra_pump_runs update failed: %s", exc)


def _finalize_finished_at(run_id: int) -> None:
    ph = _ph()
    expr = "NOW()" if USE_PG else "datetime('now')"
    try:
        with get_db() as db:
            db.execute(f"UPDATE ultra_pump_runs SET finished_at={expr} WHERE id={ph}", [run_id])
            db.commit()
    except Exception:
        pass


def _get_run_row(run_id: int) -> dict | None:
    ph = _ph()
    try:
        with get_db() as db:
            db.execute(f"SELECT * FROM ultra_pump_runs WHERE id={ph}", [run_id])
            row = db.fetchone()
        return row
    except Exception:
        return None


# ── Settings helpers ─────────────────────────────────────────────────────────

def _compute_fetch_bars(lookback_bars: int, pump_horizon: int) -> int:
    """Always fetch enough bars for warmup (EMA200) + lookback + forward horizon."""
    WARMUP = 200
    BUFFER = 25
    return max(60, lookback_bars) + int(pump_horizon) + WARMUP + BUFFER


def _normalize_date(d: Any) -> str | None:
    if d is None:
        return None
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    s = str(d)[:10]
    return s if s else None


def _derived_settings_hash(payload: dict) -> str:
    """Stable hash of settings that affect derived artifacts (patterns/lift/splits).

    Episodes only depend on a smaller subset, so changing eg split window
    doesn't invalidate episodes.
    """
    keys = [
        "pump_target", "pump_horizon", "pre_pump_window_bars",
        "scanner_detection_window_bars", "detection_reference",
        "split_impact_window_days", "event_scope", "research_mode",
        "min_price", "min_volume", "min_dollar_volume",
    ]
    canon = {k: payload.get(k) for k in keys}
    return hashlib.sha256(
        json.dumps(canon, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


# ── Pump episode detection ───────────────────────────────────────────────────

def _safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _detect_episodes_for_symbol(
    symbol: str,
    bars: list[dict],
    *,
    universe: str,
    pump_horizon: int,
    pre_pump_window: int,
    detection_window: int,
    pump_target: str,
    start_date: str | None,
    end_date: str | None,
) -> list[dict]:
    """
    Detect pump episodes for one symbol. A pump episode is anchored at the bar
    where the forward `pump_horizon`-bar window achieves max_gain_pct ≥ threshold.

    Threshold:
        X2_TO_X4 → 100 <= max_gain_pct < 300
        X4_PLUS  → max_gain_pct >= 300

    Anchor de-duplication: merge candidate anchors whose windows overlap within
    EPISODE_DEDUP_DAYS trading bars (keep the candidate with the largest
    max_gain_pct as the episode anchor; the merged set forms one episode).
    """
    if not bars or len(bars) < 5:
        return []

    n = len(bars)
    candidates: list[dict] = []
    # Iterate every bar that has at least one forward bar
    for i in range(0, n - 1):
        b = bars[i]
        dstr = _normalize_date(b.get("date"))
        if not dstr:
            continue
        if start_date and dstr < start_date:
            continue
        if end_date and dstr > end_date:
            continue

        anchor_close = _safe_float(b.get("close"))
        if anchor_close <= 0:
            continue

        forward = bars[i + 1: i + 1 + pump_horizon]
        if not forward:
            continue

        # Compute max_gain_pct and time-to-peak from forward window
        peak_high = 0.0
        peak_idx_rel = -1
        first_x2_idx_rel = -1
        for j, fb in enumerate(forward):
            h = _safe_float(fb.get("high"))
            if h > peak_high:
                peak_high = h
                peak_idx_rel = j
            if first_x2_idx_rel < 0 and h >= anchor_close * 2.0:
                first_x2_idx_rel = j

        max_gain_pct = ((peak_high - anchor_close) / anchor_close * 100.0) if peak_high > 0 else 0.0

        # Threshold check
        is_x2_to_x4 = (max_gain_pct >= X2_TO_X4_MIN and max_gain_pct < X4_PLUS_MIN)
        is_x4_plus  = (max_gain_pct >= X4_PLUS_MIN)
        if pump_target == "X2_TO_X4" and not is_x2_to_x4:
            continue
        if pump_target == "X4_PLUS" and not is_x4_plus:
            continue
        if pump_target == "BOTH" and not (is_x2_to_x4 or is_x4_plus):
            continue

        peak_bar = forward[peak_idx_rel] if peak_idx_rel >= 0 else None
        peak_date = _normalize_date(peak_bar.get("date")) if peak_bar else None
        first_x2_bar = forward[first_x2_idx_rel] if first_x2_idx_rel >= 0 else None
        first_x2_date = _normalize_date(first_x2_bar.get("date")) if first_x2_bar else None

        # Compute max drawdown before peak (from anchor to peak)
        pre_peak = forward[:peak_idx_rel + 1] if peak_idx_rel >= 0 else []
        max_dd_pct = 0.0
        running_high = anchor_close
        for fb in pre_peak:
            fh = _safe_float(fb.get("high"))
            fl = _safe_float(fb.get("low"))
            if fh > running_high:
                running_high = fh
            if running_high > 0:
                dd = (fl - running_high) / running_high * 100.0
                if dd < max_dd_pct:
                    max_dd_pct = dd

        candidates.append({
            "symbol": symbol,
            "universe": universe,
            "anchor_index": i,
            "anchor_date": dstr,
            "anchor_close": anchor_close,
            "peak_high": peak_high,
            "peak_date": peak_date,
            "peak_index_rel": peak_idx_rel,
            "first_x2_date": first_x2_date,
            "first_x2_index_rel": first_x2_idx_rel,
            "max_gain_pct": max_gain_pct,
            "max_drawdown_before_peak_pct": max_dd_pct,
            "days_to_peak": (peak_idx_rel + 1) if peak_idx_rel >= 0 else None,
            "days_to_first_x2": (first_x2_idx_rel + 1) if first_x2_idx_rel >= 0 else None,
            "category": "X4_PLUS" if is_x4_plus else "X2_TO_X4",
            "pump_horizon": pump_horizon,
        })

    if not candidates:
        return []

    # De-duplicate: walk candidates in order. If two anchors are within
    # EPISODE_DEDUP_DAYS bars of each other, merge — keep the one with larger gain.
    candidates.sort(key=lambda c: c["anchor_index"])
    merged: list[dict] = []
    for c in candidates:
        if merged and (c["anchor_index"] - merged[-1]["anchor_index"]) <= EPISODE_DEDUP_DAYS:
            # Merge into last; keep larger gain
            if c["max_gain_pct"] > merged[-1]["max_gain_pct"]:
                merged[-1] = c
            continue
        merged.append(c)

    # Attach episode_id and pre_pump_window dates
    out: list[dict] = []
    for k, ep in enumerate(merged, start=1):
        i = ep["anchor_index"]
        pre_start = max(0, i - pre_pump_window + 1)
        pre_bars = bars[pre_start: i + 1]
        if pre_bars:
            ep["pre_pump_window_start_date"] = _normalize_date(pre_bars[0].get("date"))
            ep["pre_pump_window_end_date"]   = _normalize_date(pre_bars[-1].get("date"))
        else:
            ep["pre_pump_window_start_date"] = None
            ep["pre_pump_window_end_date"]   = None
        ep["pre_pump_window_bars"]   = len(pre_bars)
        ep["scanner_detection_window_bars"] = detection_window
        ep["episode_id"] = f"{symbol}:{ep['anchor_date']}:{ep['category']}"
        out.append(ep)
    return out


# ── Caught/missed classification (basic) ─────────────────────────────────────

def _classify_caught_or_missed(
    episode: dict,
    bars: list[dict],
    *,
    detection_window: int,
) -> dict:
    """
    Basic classification: examine the scanner detection window (bars before
    the detection reference) and tag the episode as CAUGHT if at any bar in
    that window the ULTRA score on the bar would have been >= 50 (a proxy that
    Phase 2 replaces with real scanner snapshots).

    Returns the episode dict augmented with:
      caught_status: 'CAUGHT' | 'MISSED' | 'UNKNOWN'
      caught_bar_offset_from_anchor: int | None
      strongest_pre_pump_score: float | None
      data_quality.missing_fields: list[str]
    """
    out = dict(episode)
    out["caught_status"] = "UNKNOWN"
    out["caught_bar_offset_from_anchor"] = None
    out["strongest_pre_pump_score"] = None
    out["data_quality_missing_fields"] = ["ultra_score_history"]

    # Determine detection reference index in the symbol's bar array
    i = episode["anchor_index"]
    # default: use anchor bar as the detection reference
    ref_idx = i

    # if before_first_x2 and first_x2 exists, use that; else before_peak
    ref_mode = episode.get("detection_reference") or DEFAULT_DETECTION_REFERENCE
    if ref_mode in ("before_first_x2_else_before_peak", "before_first_x2"):
        if episode.get("first_x2_index_rel", -1) >= 0:
            ref_idx = i + 1 + episode["first_x2_index_rel"]
    if ref_mode == "before_peak":
        if episode.get("peak_index_rel", -1) >= 0:
            ref_idx = i + 1 + episode["peak_index_rel"]

    window_start = max(0, ref_idx - detection_window)
    window_bars = bars[window_start:ref_idx]
    if not window_bars:
        return out

    # In Phase 1 we don't yet have historical ULTRA score snapshots. Mark UNKNOWN.
    # Phase 2 fills in via _extract_ultra_context().
    return out


# ── Empty-artifact helpers ───────────────────────────────────────────────────

def _write_empty_artifact(path: Path, schema_columns: list[str], reason: str) -> None:
    """Write an empty parquet with the given schema so DuckDB queries don't fail."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [pa.field(c, pa.string()) for c in schema_columns]
    table = pa.Table.from_arrays([pa.array([], type=pa.string()) for _ in schema_columns],
                                 schema=pa.schema(fields))
    pq.write_table(table, str(path), compression="zstd")


_SCHEMA_PUMP_EPISODES = [
    "episode_id", "symbol", "universe", "category",
    "anchor_date", "anchor_close",
    "peak_date", "peak_high", "max_gain_pct",
    "first_x2_date", "days_to_peak", "days_to_first_x2",
    "max_drawdown_before_peak_pct",
    "pre_pump_window_start_date", "pre_pump_window_end_date",
    "pre_pump_window_bars", "scanner_detection_window_bars",
    "pump_horizon", "caught_status",
]

_SCHEMA_CAUGHT = [
    "episode_id", "symbol", "anchor_date", "caught_bar_offset_from_anchor",
    "strongest_pre_pump_score",
]

_SCHEMA_MISSED = [
    "episode_id", "symbol", "anchor_date", "missed_reason_primary",
    "missed_reason_secondary",
]

_SCHEMA_PRE_PUMP_BARS = [
    "episode_id", "symbol", "bar_date", "bars_before_anchor",
    "close", "volume",
]

_SCHEMA_PRE_PUMP_SIGNALS = [
    "episode_id", "symbol", "bar_date", "signal_name", "signal_value",
]

_SCHEMA_PRE_PUMP_COMBOS = [
    "episode_id", "symbol", "bar_date", "combo_key", "combo_components",
]

_SCHEMA_MISSED_DIAG = [
    "episode_id", "symbol", "missed_reason_primary", "missed_reason_secondary",
    "missed_diagnostics_json", "would_have_score", "would_have_category",
    "best_pre_pump_ultra_pattern", "strongest_pre_pump_signal",
    "filter_that_blocked_it", "recommended_fix",
]

_SCHEMA_PATTERN_STATS = [
    "pattern_key", "pattern_type", "pump_count", "pump_episode_coverage_pct",
    "baseline_count", "baseline_frequency_pct", "lift_vs_baseline",
    "odds_ratio", "precision", "recall", "false_positive_rate",
    "median_future_gain", "median_days_to_peak", "median_drawdown_before_peak",
]

_SCHEMA_PATTERN_LIFT = [
    "pattern_key", "lift_all", "lift_clean_non_split", "lift_split_related",
    "lift_post_reverse_split",
]

_SCHEMA_TIMING = [
    "pattern_key", "timing_bucket", "count", "median_future_gain",
]

_SCHEMA_BASELINE_WINDOWS = [
    "symbol", "window_end_date", "is_pump", "max_gain_pct",
]

_SCHEMA_SPLIT_IMPACT = [
    "pattern_key", "split_status", "count", "lift", "precision",
]

_SCHEMA_SPLIT_PUMPS = _SCHEMA_PUMP_EPISODES + ["split_status"]


_ARTIFACT_SCHEMAS = {
    "pump_episodes":               _SCHEMA_PUMP_EPISODES,
    "x2_to_x4_episodes":           _SCHEMA_PUMP_EPISODES,
    "x4_plus_episodes":            _SCHEMA_PUMP_EPISODES,
    "scanner_caught_pumps":        _SCHEMA_CAUGHT,
    "missed_pumps":                _SCHEMA_MISSED,
    "pre_pump_ultra_bars":         _SCHEMA_PRE_PUMP_BARS,
    "pre_pump_ultra_signals":      _SCHEMA_PRE_PUMP_SIGNALS,
    "pre_pump_ultra_combinations": _SCHEMA_PRE_PUMP_COMBOS,
    "missed_diagnostics":          _SCHEMA_MISSED_DIAG,
    "ultra_pattern_stats":         _SCHEMA_PATTERN_STATS,
    "ultra_pattern_lift_stats":    _SCHEMA_PATTERN_LIFT,
    "ultra_timing_stats":          _SCHEMA_TIMING,
    "baseline_windows":            _SCHEMA_BASELINE_WINDOWS,
    "baseline_ultra_patterns":     _SCHEMA_PRE_PUMP_COMBOS,
    "baseline_pattern_stats":      _SCHEMA_PATTERN_STATS,
    "split_impact_stats":          _SCHEMA_SPLIT_IMPACT,
    "split_related_pumps":         _SCHEMA_SPLIT_PUMPS,
    "clean_non_split_pumps":       _SCHEMA_SPLIT_PUMPS,
    "post_reverse_split_pumps":    _SCHEMA_SPLIT_PUMPS,
}


def artifact_schema(name: str) -> list[str]:
    return _ARTIFACT_SCHEMAS.get(name, [])


# ── Main run loop ────────────────────────────────────────────────────────────

_WORKERS = 8


def _process_symbol(
    symbol: str,
    *,
    universe: str,
    pump_horizon: int,
    pre_pump_window: int,
    detection_window: int,
    pump_target: str,
    start_date: str | None,
    end_date: str | None,
    fetch_bars: int,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Returns (enriched_episodes, pre_pump_bars_rows, signals_rows, combos_rows)."""
    from main import api_bar_signals
    from ultra_pump_extraction import (
        build_pre_pump_artifacts, classify_caught_from_pre_pump,
    )
    try:
        bars = api_bar_signals(symbol, "1d", fetch_bars)
    except Exception:
        return [], [], [], []
    if not bars or len(bars) < 20:
        return [], [], [], []

    episodes = _detect_episodes_for_symbol(
        symbol, bars,
        universe=universe,
        pump_horizon=pump_horizon,
        pre_pump_window=pre_pump_window,
        detection_window=detection_window,
        pump_target=pump_target,
        start_date=start_date,
        end_date=end_date,
    )

    pre_bars_rows: list[dict] = []
    signals_rows: list[dict] = []
    combos_rows:  list[dict] = []
    enriched: list[dict] = []
    for ep in episodes:
        bb, ss, cc = build_pre_pump_artifacts(ep, bars)
        pre_bars_rows.extend(bb)
        signals_rows.extend(ss)
        combos_rows.extend(cc)
        ep_classified = classify_caught_from_pre_pump(
            ep, bb, detection_window=detection_window,
        )
        enriched.append(ep_classified)
    return enriched, pre_bars_rows, signals_rows, combos_rows


def run_ultra_pump_research(run_id: int, payload: dict) -> None:
    """Background entrypoint for an ULTRA Pump Research run."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from scanner import get_universe_tickers
    from replay_storage import (
        run_dir, write_parquet, write_json, register_artifact,
    )

    _set(running=True, run_id=run_id, status="running", phase="starting",
         phase_message="Resolving universe",
         mode=payload.get("mode"), universe=payload.get("universe"),
         pump_target=payload.get("pump_target", "X2_TO_X4"),
         symbols_total=0, symbols_completed=0,
         episodes_found=0, x2_to_x4_count=0, x4_plus_count=0,
         caught_count=0, missed_count=0,
         started_at=time.time(), elapsed_secs=0, error=None,
         run_warnings=[])
    t0 = time.time()

    try:
        universe = payload.get("universe", "all_us")
        pump_target = payload.get("pump_target", "X2_TO_X4")
        pump_horizon = int(payload.get("pump_horizon") or DEFAULT_PUMP_HORIZON)
        pre_pump_window = int(payload.get("pre_pump_window_bars") or DEFAULT_PRE_PUMP_WINDOW_BARS)
        detection_window = int(payload.get("scanner_detection_window_bars") or DEFAULT_SCANNER_WINDOW_BARS)
        lookback_bars = int(payload.get("lookback_bars") or DEFAULT_LOOKBACK_BARS)
        fetch_bars = _compute_fetch_bars(lookback_bars, pump_horizon)
        start_date = _normalize_date(payload.get("start_date"))
        end_date = _normalize_date(payload.get("end_date"))

        # Resolve universe
        scanner_universe = universe
        if universe == "nasdaq_gt5":
            scanner_universe = "nasdaq"
        try:
            tickers = list(get_universe_tickers(scanner_universe))
        except Exception:
            tickers = []
        if not tickers:
            log.warning("ultra_pump[%d]: empty universe %s", run_id, universe)
        _set(symbols_total=len(tickers), phase="scanning",
             phase_message=f"Detecting pump episodes across {len(tickers)} symbols")

        run_warnings: list[str] = []
        if lookback_bars < 100:
            run_warnings.append(
                "Limited context: only 30 bars of ULTRA history per episode. "
                "Pattern statistics are PARTIAL — sample size smaller, but every "
                "artifact slot is still produced."
            )

        # ── Detection phase ──────────────────────────────────────────────────
        all_episodes: list[dict] = []
        x2_to_x4: list[dict] = []
        x4_plus: list[dict] = []
        caught_list: list[dict] = []
        missed_list: list[dict] = []

        kwargs = dict(
            universe=universe,
            pump_horizon=pump_horizon,
            pre_pump_window=pre_pump_window,
            detection_window=detection_window,
            pump_target=pump_target,
            start_date=start_date,
            end_date=end_date,
            fetch_bars=fetch_bars,
        )

        all_pre_pump_bars: list[dict] = []
        all_pre_pump_signals: list[dict] = []
        all_pre_pump_combos: list[dict] = []
        missed_diagnostics: list[dict] = []
        completed = 0
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            futs = {pool.submit(_process_symbol, t, **kwargs): t for t in tickers}
            for fut in as_completed(futs):
                while _state.get("pause_requested"):
                    _set(elapsed_secs=round(time.time() - t0, 1))
                    time.sleep(0.5)
                if _state.get("stop_requested"):
                    for f in futs:
                        if not f.done():
                            f.cancel()
                    break
                sym = futs[fut]
                try:
                    enriched, pb_bars, pb_sigs, pb_combos = fut.result()
                except Exception as exc:
                    log.debug("ultra_pump worker for %s raised: %s", sym, exc)
                    enriched, pb_bars, pb_sigs, pb_combos = [], [], [], []
                all_pre_pump_bars.extend(pb_bars)
                all_pre_pump_signals.extend(pb_sigs)
                all_pre_pump_combos.extend(pb_combos)
                for ep in enriched:
                    all_episodes.append(ep)
                    if ep["category"] == "X2_TO_X4":
                        x2_to_x4.append(ep)
                    elif ep["category"] == "X4_PLUS":
                        x4_plus.append(ep)
                    if ep.get("caught_status") == "CAUGHT":
                        caught_list.append({
                            "episode_id": ep["episode_id"],
                            "symbol": ep["symbol"],
                            "anchor_date": ep["anchor_date"],
                            "caught_bar_offset_from_anchor": ep.get("caught_bar_offset_from_anchor"),
                            "strongest_pre_pump_score": ep.get("strongest_pre_pump_score"),
                        })
                    else:
                        missed_list.append({
                            "episode_id": ep["episode_id"],
                            "symbol": ep["symbol"],
                            "anchor_date": ep["anchor_date"],
                            "missed_reason_primary": ep.get("missed_reason_primary") or "UNKNOWN",
                            "missed_reason_secondary": ep.get("missed_reason_secondary"),
                        })
                        missed_diagnostics.append({
                            "episode_id": ep["episode_id"],
                            "symbol": ep["symbol"],
                            "missed_reason_primary": ep.get("missed_reason_primary") or "UNKNOWN",
                            "missed_reason_secondary": ep.get("missed_reason_secondary"),
                            "missed_diagnostics_json": json.dumps({
                                "anchor_date": ep["anchor_date"],
                                "category": ep["category"],
                                "max_gain_pct": ep.get("max_gain_pct"),
                                "days_to_peak": ep.get("days_to_peak"),
                            }),
                            "would_have_score": ep.get("strongest_pre_pump_score"),
                            "would_have_category": ep.get("best_pre_pump_ultra_pattern"),
                            "best_pre_pump_ultra_pattern": ep.get("best_pre_pump_ultra_pattern"),
                            "strongest_pre_pump_signal": ep.get("strongest_pre_pump_signal"),
                            "filter_that_blocked_it": ep.get("missed_reason_primary"),
                            "recommended_fix": _recommend_fix_for_reason(
                                ep.get("missed_reason_primary")
                            ),
                        })
                completed += 1
                _set(symbols_completed=completed,
                     episodes_found=len(all_episodes),
                     x2_to_x4_count=len(x2_to_x4),
                     x4_plus_count=len(x4_plus),
                     caught_count=len(caught_list),
                     missed_count=len(missed_list),
                     elapsed_secs=round(time.time() - t0, 1))

        # ── Write artifacts ──────────────────────────────────────────────────
        _set(phase="writing_artifacts",
             phase_message=f"Writing {len(ARTIFACT_SLOTS_PARQUET)} parquet artifacts")
        rdir = run_dir(run_id)
        rdir.mkdir(parents=True, exist_ok=True)

        derived_hash = _derived_settings_hash(payload)
        created_at_iso = datetime.utcnow().isoformat() + "Z"

        # Helper to write parquet with schema fallback
        def _write_slot(slot: str, rows: list[dict], reason_if_empty: str) -> dict:
            path = rdir / f"{slot}.parquet"
            schema = artifact_schema(slot)
            if rows:
                n = write_parquet(path, rows)
                status = "ok"
                reason = None
            else:
                _write_empty_artifact(path, schema, reason_if_empty)
                n = 0
                status = "empty"
                reason = reason_if_empty
            size = path.stat().st_size if path.exists() else 0
            return {
                "artifact_name": slot,
                "exists": True,
                "row_count": n,
                "size_bytes": size,
                "status": status,
                "description": _ARTIFACT_DESCRIPTIONS.get(slot, slot),
                "download_endpoint": f"/api/ultra-pump/{run_id}/export?part={slot}",
                "reason_if_empty": reason,
                "schema_version": 1,
                "derived_settings_hash": derived_hash,
                "created_at": created_at_iso,
            }

        manifest_entries: list[dict] = []

        manifest_entries.append(_write_slot(
            "pump_episodes", all_episodes,
            "No pump episodes detected in the selected universe and date range."
        ))
        manifest_entries.append(_write_slot(
            "x2_to_x4_episodes", x2_to_x4,
            "No X2→X4 pump episodes detected."
        ))
        manifest_entries.append(_write_slot(
            "x4_plus_episodes", x4_plus,
            "No X4+ monster pump episodes detected."
        ))
        manifest_entries.append(_write_slot(
            "scanner_caught_pumps", caught_list,
            "No pumps classified as CAUGHT in Phase 1 (requires Phase 2 ULTRA snapshots)."
        ))
        manifest_entries.append(_write_slot(
            "missed_pumps", missed_list,
            "No missed pumps because no episodes were found."
        ))

        # Phase 2 artifacts (real data)
        manifest_entries.append(_write_slot(
            "pre_pump_ultra_bars", all_pre_pump_bars,
            "No pre-pump bars collected (no episodes detected)."
        ))
        manifest_entries.append(_write_slot(
            "pre_pump_ultra_signals", all_pre_pump_signals,
            "No pre-pump signals collected (no episodes detected)."
        ))
        manifest_entries.append(_write_slot(
            "pre_pump_ultra_combinations", all_pre_pump_combos,
            "No pre-pump combinations collected (no episodes detected)."
        ))
        manifest_entries.append(_write_slot(
            "missed_diagnostics", missed_diagnostics,
            "No missed pumps to diagnose."
        ))

        # ── Phase 3: pattern mining + baseline + lift ────────────────────────
        from ultra_pump_patterns import (
            mine_patterns, sample_baseline_windows,
        )
        _set(phase="mining_patterns",
             phase_message="Mining ULTRA patterns and computing baseline lift")
        baseline_windows_rows = sample_baseline_windows(
            all_episodes,
            pre_pump_window_bars=pre_pump_window,
            pump_horizon=pump_horizon,
            max_baseline_per_pump=10,
        )
        baseline_total = len(baseline_windows_rows)
        pattern_rows, lift_rows, timing_rows = mine_patterns(
            all_episodes, all_pre_pump_bars, all_pre_pump_signals, all_pre_pump_combos,
            baseline_total=baseline_total,
        )

        # Baseline pattern stats: one row per pattern_key from pattern_rows
        baseline_pattern_rows = [
            {
                "pattern_key": p["pattern_key"],
                "pattern_type": p["pattern_type"],
                "pump_count": 0,
                "pump_episode_coverage_pct": 0.0,
                "baseline_count": p["baseline_count"],
                "baseline_frequency_pct": p["baseline_frequency_pct"],
                "lift_vs_baseline": None,
                "odds_ratio": None,
                "precision": None,
                "recall": None,
                "false_positive_rate": None,
                "median_future_gain": None,
                "median_days_to_peak": None,
                "median_drawdown_before_peak": None,
            }
            for p in pattern_rows
        ]

        manifest_entries.append(_write_slot(
            "ultra_pattern_stats", pattern_rows,
            "No patterns mined (no episodes detected)."
        ))
        manifest_entries.append(_write_slot(
            "ultra_pattern_lift_stats", lift_rows,
            "No pattern lift rows (no episodes detected)."
        ))
        manifest_entries.append(_write_slot(
            "ultra_timing_stats", timing_rows,
            "No timing rows (no episodes detected)."
        ))
        manifest_entries.append(_write_slot(
            "baseline_windows", baseline_windows_rows,
            "No baseline windows sampled."
        ))
        manifest_entries.append(_write_slot(
            "baseline_ultra_patterns", [],
            "Baseline ULTRA snapshots not yet available — Phase 5 full-rescan replays "
            "the live scanner on non-pump dates to populate this slot."
        ))
        manifest_entries.append(_write_slot(
            "baseline_pattern_stats", baseline_pattern_rows,
            "No baseline patterns (no episodes detected)."
        ))

        # ── Phase 4: split impact ────────────────────────────────────────────
        from ultra_pump_split import (
            classify_episodes_split_status, build_split_partitions,
            split_aware_pattern_stats,
        )
        _set(phase="split_impact", phase_message="Classifying split-impact status")
        split_window = int(payload.get("split_impact_window_days") or DEFAULT_SPLIT_IMPACT_WINDOW)
        episodes_with_split = classify_episodes_split_status(
            all_episodes, split_impact_window_days=split_window,
        )
        partitions = build_split_partitions(episodes_with_split)
        split_impact_rows, lift_rows_updated = split_aware_pattern_stats(
            pattern_rows, lift_rows, episodes_with_split,
            all_pre_pump_signals, all_pre_pump_combos,
        )

        # Overwrite the previously-written lift slot with the split-aware version
        # (the manifest entry was already appended — re-write the parquet)
        lift_path = rdir / "ultra_pattern_lift_stats.parquet"
        if lift_rows_updated:
            write_parquet(lift_path, lift_rows_updated)
        # Update manifest entry row_count + size in-place
        for entry in manifest_entries:
            if entry["artifact_name"] == "ultra_pattern_lift_stats":
                entry["row_count"] = len(lift_rows_updated)
                entry["size_bytes"] = lift_path.stat().st_size if lift_path.exists() else 0
                if lift_rows_updated:
                    entry["status"] = "ok"
                    entry["reason_if_empty"] = None
                break

        manifest_entries.append(_write_slot(
            "split_impact_stats", split_impact_rows,
            "No split-impact stats computed (no patterns or no episodes)."
        ))
        manifest_entries.append(_write_slot(
            "split_related_pumps", partitions["split_related_pumps"],
            "No split-related pumps detected in this run."
        ))
        manifest_entries.append(_write_slot(
            "clean_non_split_pumps", partitions["clean_non_split_pumps"],
            "No clean (non-split) pumps detected."
        ))
        manifest_entries.append(_write_slot(
            "post_reverse_split_pumps", partitions["post_reverse_split_pumps"],
            "No post-reverse-split pumps detected."
        ))

        # ── JSON artifacts ───────────────────────────────────────────────────
        warnings_path = rdir / "warnings.json"
        write_json(warnings_path, {"warnings": run_warnings})

        progress_path = rdir / "progress.json"
        write_json(progress_path, get_state())

        # ── Phase 5: recommendations ─────────────────────────────────────────
        from ultra_pump_recommendations import build_recommendations
        summary = {
            "total_episodes": len(all_episodes),
            "x2_to_x4_count": len(x2_to_x4),
            "x4_plus_count":  len(x4_plus),
            "caught_count":   len(caught_list),
            "missed_count":   len(missed_list),
            "symbols_scanned": completed,
            "baseline_total": baseline_total,
            "pattern_count":  len(pattern_rows),
        }
        recs_bundle = build_recommendations(
            pattern_rows, lift_rows_updated, summary=summary,
        )
        recs_path = rdir / "ultra_recommendations.json"
        write_json(recs_path, {
            "run_id": run_id,
            "generated_at": created_at_iso,
            "derived_settings_hash": derived_hash,
            **recs_bundle,
        })

        research_bundle = {
            "run_id": run_id,
            "generated_at": created_at_iso,
            "derived_settings_hash": derived_hash,
            "summary": summary,
            "verdict_counts": recs_bundle.get("verdict_counts", {}),
            "top_recommendations": recs_bundle.get("recommendations", [])[:25],
            "split_impact_stats":  split_impact_rows,
            "split_partition_counts": {
                "clean_non_split":    len(partitions["clean_non_split_pumps"]),
                "split_related":      len(partitions["split_related_pumps"]),
                "post_reverse_split": len(partitions["post_reverse_split_pumps"]),
            },
            "warnings": run_warnings,
            "phase": "phase_5_complete",
            "settings": {
                "universe": universe,
                "pump_target": pump_target,
                "pump_horizon": pump_horizon,
                "pre_pump_window_bars": pre_pump_window,
                "scanner_detection_window_bars": detection_window,
                "lookback_bars": lookback_bars,
                "split_impact_window_days": int(payload.get("split_impact_window_days") or DEFAULT_SPLIT_IMPACT_WINDOW),
                "start_date": start_date,
                "end_date": end_date,
            },
        }
        rb_path = rdir / "research_bundle.json"
        write_json(rb_path, research_bundle)

        # Export manifest
        export_manifest = {
            "run_id": run_id,
            "generated_at": created_at_iso,
            "derived_settings_hash": derived_hash,
            "artifacts": manifest_entries,
        }
        em_path = rdir / "export_manifest.json"
        write_json(em_path, export_manifest)

        # Logs stub
        logs_path = rdir / "logs.txt"
        try:
            logs_path.write_text(
                f"ULTRA Pump Research run {run_id}\n"
                f"Started: {datetime.utcfromtimestamp(t0).isoformat()}Z\n"
                f"Universe: {universe}\n"
                f"Pump target: {pump_target}\n"
                f"Pump horizon: {pump_horizon}D\n"
                f"Pre-pump window: {pre_pump_window} bars\n"
                f"Scanner detection window: {detection_window} bars\n"
                f"Symbols scanned: {completed}/{len(tickers)}\n"
                f"Episodes found: {len(all_episodes)}\n",
                encoding="utf-8",
            )
        except Exception:
            pass

        # ── Update run row ───────────────────────────────────────────────────
        final_status = "stopped" if _state.get("stop_requested") else "completed"
        summary = research_bundle["summary"]
        _update_run(
            run_id,
            status=final_status,
            total_episodes=len(all_episodes),
            total_caught=len(caught_list),
            total_missed=len(missed_list),
            symbols_total=len(tickers),
            symbols_completed=completed,
            summary_json=json.dumps(summary, default=str),
            artifact_manifest_json=json.dumps([e["artifact_name"] for e in manifest_entries]),
        )
        _finalize_finished_at(run_id)

        # ── Persist results to DB (persistent archive) ───────────────────────
        _set(phase="persisting", phase_message="Persisting results to database")
        persist_research_results(
            run_id,
            all_episodes=all_episodes,
            episodes_with_split=episodes_with_split,
            pattern_rows=pattern_rows,
            lift_rows=lift_rows_updated,
            research_bundle=research_bundle,
        )
        refresh_ultra_research_run_summary(run_id)

        run_snapshot = _get_run_row(run_id)
        write_json(rdir / "run.json", run_snapshot)

        # ── Register artifacts in DB ─────────────────────────────────────────
        with get_db() as db:
            for entry in manifest_entries:
                p = rdir / f"{entry['artifact_name']}.parquet"
                register_artifact(db, run_id, entry["artifact_name"], p, entry["row_count"])
            register_artifact(db, run_id, "research_bundle", rb_path, 1, fmt="json")
            register_artifact(db, run_id, "export_manifest", em_path, 1, fmt="json")
            register_artifact(db, run_id, "warnings",        warnings_path, 1, fmt="json")
            register_artifact(db, run_id, "ultra_recommendations", recs_path, 1, fmt="json")
            db.commit()

        _set(status=final_status, running=False,
             phase="completed", phase_message="Run finished",
             stop_requested=False, pause_requested=False,
             elapsed_secs=round(time.time() - t0, 1))
        log.info("ultra_pump[%d] %s — %d episodes (X2→X4=%d, X4+=%d)",
                 run_id, final_status, len(all_episodes), len(x2_to_x4), len(x4_plus))

    except Exception as exc:
        tb = traceback.format_exc()
        log.error("ultra_pump_research failed: %s\n%s", exc, tb)
        _set(status="failed", running=False, error=str(exc),
             phase="failed", phase_message=str(exc),
             stop_requested=False, pause_requested=False,
             elapsed_secs=round(time.time() - t0, 1))
        _update_run(run_id, status="failed", error_message=str(exc))
        _finalize_finished_at(run_id)


# ── Persistent archive helpers ────────────────────────────────────────────────

def persist_research_results(
    run_id: int,
    all_episodes: list[dict],
    episodes_with_split: list[dict],
    pattern_rows: list[dict],
    lift_rows: list[dict],
    research_bundle: dict,
) -> None:
    """
    Write completed run results to the persistent DB tables:
      - ultra_research_episodes  (one row per episode)
      - ultra_research_patterns  (one row per pattern, merged with lift)
      - ultra_research_bundles   (full bundle JSON)

    Safe to call multiple times — uses INSERT OR REPLACE / UPSERT logic.
    """
    ph = _ph()
    now_expr = "NOW()" if USE_PG else "datetime('now')"

    # Build a dict of split_status keyed by episode_id for merging
    split_map: dict[str, str] = {}
    for ep in (episodes_with_split or []):
        eid = ep.get("episode_id") or ""
        if eid:
            split_map[eid] = ep.get("split_status", "")

    # Build lift map keyed by pattern_key
    lift_map: dict[str, dict] = {}
    for lr in (lift_rows or []):
        pk = lr.get("pattern_key") or ""
        if pk:
            lift_map[pk] = lr

    try:
        with get_db() as db:
            # --- episodes ---
            # Delete old rows for this run first (idempotent re-run support)
            db.execute(f"DELETE FROM ultra_research_episodes WHERE run_id={ph}", [run_id])

            ep_cols = [
                "run_id", "episode_id", "symbol", "universe", "category",
                "anchor_date", "anchor_close", "peak_date", "peak_high",
                "max_gain_pct", "first_x2_date", "days_to_peak", "days_to_first_x2",
                "max_drawdown_before_peak_pct", "pre_pump_window_start_date",
                "pre_pump_window_end_date", "pre_pump_window_bars",
                "scanner_detection_window_bars", "pump_horizon",
                "caught_status", "caught_bar_offset_from_anchor",
                "strongest_pre_pump_score", "split_status",
            ]
            ep_placeholders = ", ".join([ph] * len(ep_cols))
            ep_sql = (f"INSERT INTO ultra_research_episodes ({', '.join(ep_cols)}) "
                      f"VALUES ({ep_placeholders})")

            ep_batch = []
            for ep in (all_episodes or []):
                eid = ep.get("episode_id") or ""
                ep_batch.append([
                    run_id,
                    eid,
                    ep.get("symbol"),
                    ep.get("universe"),
                    ep.get("category"),
                    ep.get("anchor_date"),
                    ep.get("anchor_close"),
                    ep.get("peak_date"),
                    ep.get("peak_high"),
                    ep.get("max_gain_pct"),
                    ep.get("first_x2_date"),
                    ep.get("days_to_peak"),
                    ep.get("days_to_first_x2"),
                    ep.get("max_drawdown_before_peak_pct"),
                    ep.get("pre_pump_window_start_date"),
                    ep.get("pre_pump_window_end_date"),
                    ep.get("pre_pump_window_bars"),
                    ep.get("scanner_detection_window_bars"),
                    ep.get("pump_horizon"),
                    ep.get("caught_status"),
                    ep.get("caught_bar_offset_from_anchor"),
                    ep.get("strongest_pre_pump_score"),
                    split_map.get(eid, ""),
                ])
            if ep_batch:
                db.executemany(ep_sql, ep_batch)

            # --- patterns ---
            db.execute(f"DELETE FROM ultra_research_patterns WHERE run_id={ph}", [run_id])

            pt_cols = [
                "run_id", "pattern_key", "pattern_type",
                "pump_count", "pump_episode_coverage_pct",
                "baseline_count", "baseline_frequency_pct",
                "lift_vs_baseline", "odds_ratio", "precision", "recall",
                "false_positive_rate", "median_future_gain",
                "median_days_to_peak", "median_drawdown_before_peak",
                "lift_all", "lift_clean_non_split",
                "lift_split_related", "lift_post_reverse_split",
            ]
            pt_placeholders = ", ".join([ph] * len(pt_cols))
            pt_sql = (f"INSERT INTO ultra_research_patterns ({', '.join(pt_cols)}) "
                      f"VALUES ({pt_placeholders})")

            pt_batch = []
            for pr in (pattern_rows or []):
                pk = pr.get("pattern_key") or ""
                lr = lift_map.get(pk, {})
                pt_batch.append([
                    run_id,
                    pk,
                    pr.get("pattern_type"),
                    pr.get("pump_count"),
                    pr.get("pump_episode_coverage_pct"),
                    pr.get("baseline_count"),
                    pr.get("baseline_frequency_pct"),
                    pr.get("lift_vs_baseline"),
                    pr.get("odds_ratio"),
                    pr.get("precision"),
                    pr.get("recall"),
                    pr.get("false_positive_rate"),
                    pr.get("median_future_gain"),
                    pr.get("median_days_to_peak"),
                    pr.get("median_drawdown_before_peak"),
                    lr.get("lift_all"),
                    lr.get("lift_clean_non_split"),
                    lr.get("lift_split_related"),
                    lr.get("lift_post_reverse_split"),
                ])
            if pt_batch:
                db.executemany(pt_sql, pt_batch)

            # --- bundle ---
            bundle_json = json.dumps(research_bundle, default=str)
            if USE_PG:
                db.execute(
                    f"INSERT INTO ultra_research_bundles (run_id, bundle_json, updated_at) "
                    f"VALUES ({ph}, {ph}, NOW()) "
                    f"ON CONFLICT (run_id) DO UPDATE SET bundle_json=EXCLUDED.bundle_json, updated_at=NOW()",
                    [run_id, bundle_json],
                )
            else:
                db.execute(
                    f"INSERT OR REPLACE INTO ultra_research_bundles (run_id, bundle_json, updated_at) "
                    f"VALUES ({ph}, {ph}, datetime('now'))",
                    [run_id, bundle_json],
                )

            db.commit()
        log.info("ultra_pump[%d]: persisted %d episodes, %d patterns to DB",
                 run_id, len(ep_batch), len(pt_batch))
    except Exception as exc:
        log.error("ultra_pump[%d]: persist_research_results failed: %s", run_id, exc)


def refresh_ultra_research_run_summary(run_id: int) -> None:
    """
    Re-query actual DB row counts and update ultra_pump_runs totals + summary_json.
    Call after persist_research_results() to ensure accurate counts.
    """
    ph = _ph()
    try:
        with get_db() as db:
            db.execute(
                f"SELECT COUNT(*) AS n FROM ultra_research_episodes WHERE run_id={ph}",
                [run_id],
            )
            row = db.fetchone()
            ep_count = (row or {}).get("n", 0) or 0

            db.execute(
                f"SELECT COUNT(*) AS n FROM ultra_research_episodes "
                f"WHERE run_id={ph} AND category='X2_TO_X4'",
                [run_id],
            )
            row = db.fetchone()
            x2_count = (row or {}).get("n", 0) or 0

            db.execute(
                f"SELECT COUNT(*) AS n FROM ultra_research_episodes "
                f"WHERE run_id={ph} AND category='X4_PLUS'",
                [run_id],
            )
            row = db.fetchone()
            x4_count = (row or {}).get("n", 0) or 0

            db.execute(
                f"SELECT COUNT(*) AS n FROM ultra_research_episodes "
                f"WHERE run_id={ph} AND caught_status='CAUGHT'",
                [run_id],
            )
            row = db.fetchone()
            caught_count = (row or {}).get("n", 0) or 0

            db.execute(
                f"SELECT COUNT(*) AS n FROM ultra_research_patterns WHERE run_id={ph}",
                [run_id],
            )
            row = db.fetchone()
            pattern_count = (row or {}).get("n", 0) or 0

        missed_count = ep_count - caught_count
        summary = {
            "total_episodes": ep_count,
            "x2_to_x4_count": x2_count,
            "x4_plus_count": x4_count,
            "caught_count": caught_count,
            "missed_count": missed_count,
            "pattern_count": pattern_count,
            "db_row_counts": {
                "episodes": ep_count,
                "patterns": pattern_count,
            },
        }
        _update_run(
            run_id,
            total_episodes=ep_count,
            total_caught=caught_count,
            total_missed=missed_count,
            summary_json=json.dumps(summary, default=str),
        )
        log.info("ultra_pump[%d]: refreshed run summary from DB — %d episodes, %d patterns",
                 run_id, ep_count, pattern_count)
    except Exception as exc:
        log.error("ultra_pump[%d]: refresh_ultra_research_run_summary failed: %s", run_id, exc)


_MISSED_RECOMMENDED_FIXES = {
    "NO_PRE_PUMP_DATA":                "Increase fetch lookback bars so pre-pump window is fully covered.",
    "INSUFFICIENT_HISTORY":            "Use a longer lookback_bars setting (>= 250) or a later start date.",
    "NO_ULTRA_SCORE_AVAILABLE":        "Backfill historical ULTRA scanner snapshots for the affected date range.",
    "MISSING_HISTORICAL_SNAPSHOT":     "Replay the ULTRA scanner historically (Phase 5 full-rescan).",
    "ULTRA_SCORE_BELOW_THRESHOLD":     "Lower the score threshold or add a complementary detector for this pattern.",
    "PROFILE_CATEGORY_NOT_QUALIFIED":  "Allow WATCH category for X4+ candidates or add a category override.",
    "UNKNOWN":                         "Investigate manually — diagnostics row has more context.",
}


def _recommend_fix_for_reason(reason: str | None) -> str:
    if not reason:
        return _MISSED_RECOMMENDED_FIXES["UNKNOWN"]
    return _MISSED_RECOMMENDED_FIXES.get(reason.upper(), _MISSED_RECOMMENDED_FIXES["UNKNOWN"])


_ARTIFACT_DESCRIPTIONS = {
    "pump_episodes":               "All detected pump episodes (X2→X4 + X4+)",
    "x2_to_x4_episodes":           "Pump episodes with 100% <= max_gain < 300%",
    "x4_plus_episodes":            "Monster pump episodes with max_gain >= 300%",
    "scanner_caught_pumps":        "Episodes the ULTRA scanner caught in the pre-pump window",
    "missed_pumps":                "Episodes the ULTRA scanner missed",
    "pre_pump_ultra_bars":         "Per-bar ULTRA context for each pre-pump window",
    "pre_pump_ultra_signals":      "Flattened individual ULTRA signals seen pre-pump",
    "pre_pump_ultra_combinations": "ULTRA signal combinations seen pre-pump",
    "missed_diagnostics":          "Why each missed pump was missed + recommended fixes",
    "ultra_pattern_stats":         "Per-pattern pump statistics (precision, recall, gain)",
    "ultra_pattern_lift_stats":    "Lift vs baseline for each ULTRA pattern",
    "ultra_timing_stats":          "Signal-to-pump timing distribution per pattern",
    "baseline_windows":            "Non-pump control windows used for lift calculation",
    "baseline_ultra_patterns":     "ULTRA patterns seen in baseline windows",
    "baseline_pattern_stats":      "Baseline pattern frequency for lift",
    "split_impact_stats":          "Pattern statistics segmented by split status",
    "split_related_pumps":         "Episodes flagged as split-related",
    "clean_non_split_pumps":       "Episodes with no split contamination",
    "post_reverse_split_pumps":    "Episodes following a reverse split",
}

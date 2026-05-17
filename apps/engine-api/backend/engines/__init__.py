"""
engine_api — pure-compute layer for the scanner pipeline.

This subpackage is the FUTURE apps/engine-api/ Railway service (Phase B-2).
For now it's an in-process module, but its public surface is exactly what
will be exposed as HTTP when extracted.

Architectural law (per docs/ARCHITECTURE_TARGET.md):
    Pure compute only. No DATABASE_URL access. No Massive HTTP. No DB
    writes. No scan orchestration.

Public API:
    run_engines(ticker, timeframe, df) -> list[bar]
        The one entry point that runs the full 14-engine pipeline +
        scoring (turbo, RTB, profile, canonical, beta, ultra). Returns
        normalized per-bar objects following unified_schema.build_bar().

    build_indicators(df) -> df+indicators
        Shared OHLCV → derived columns. Used as input to engines.

    build_bar(...) / empty_signals() / empty_scores() / ROW_ORDER / ...
        Normalized schema builders + constants. Anything that needs to
        construct a "bar" object should import from here.

    GENERATOR_VERSION-style version markers from individual engines
    (TURBO_PROFILE_DEFAULTS / RTB_VERSION etc.) are not exposed; they
    live with the engine that owns them.

Individual engine modules (chart_signal_engine.py etc.) remain
importable for tests and parity checks via the dotted path:
    from backend.engine_api.chart_turbo_engine import compute_turbo_score
But application code should ONLY import from this barrel.
"""
from __future__ import annotations

# ── Orchestration entry point ────────────────────────────────────────────────
from .engine_registry import run_engines

# ── Shared indicator builder ─────────────────────────────────────────────────
from .indicator_builder import build_indicators

# ── Normalized schema (used by consumers building bars by hand) ──────────────
from .unified_schema import (
    build_bar,
    empty_signals,
    empty_scores,
    empty_roles,
    empty_split,
    empty_indicators,
    empty_ohlcv,
    bar_to_history_row,
    ROW_ORDER,
    SIGNAL_ROW_KEYS,
    SCORE_FIELDS,
    ROLE_FIELDS,
    SPLIT_FIELDS,
)

# ── Row builder + scoring functions (used by scoring_adapter to feed
#    ultra_score on the same row turbo saw) ────────────────────────────────
from .chart_turbo_row_builder import build_turbo_row
from .chart_turbo_engine import compute_turbo_score
from .chart_rtb_engine import calc_rtb_v4
from .chart_profile_playbook import enrich_row_with_profile
from .chart_canonical_scoring_engine import compute_canonical_score
from .chart_beta_engine import calc_beta_score


__all__ = [
    # orchestration
    "run_engines",
    # indicators
    "build_indicators",
    # schema
    "build_bar", "empty_signals", "empty_scores", "empty_roles",
    "empty_split", "empty_indicators", "empty_ohlcv",
    "bar_to_history_row",
    "ROW_ORDER", "SIGNAL_ROW_KEYS",
    "SCORE_FIELDS", "ROLE_FIELDS", "SPLIT_FIELDS",
    # row + scoring
    "build_turbo_row",
    "compute_turbo_score",
    "calc_rtb_v4",
    "enrich_row_with_profile",
    "compute_canonical_score",
    "calc_beta_score",
]

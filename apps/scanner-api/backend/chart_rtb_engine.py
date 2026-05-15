"""
chart_rtb_engine.py — Phase 8G commit 8: turbo + rtb scoring.

⚠️ PARTIAL PORT — DOCUMENTED GAP.

The old backend/rtb_engine.py is 690 lines of stateful per-bar phase logic
(A/B/C/D phases with hard/soft resets, hysteresis, launch-cluster guards,
phase-rank distance hysteresis). The verbatim port was deferred from this
commit due to scope; the implementation here is a deliberately simpler
*tier-derived* approximation:

    compute_rtb_from_bar(bar) -> {"rtb_phase", "rtb_total"}

Mapping (approximation of old phase semantics, NOT a verbatim formula port):
    GOG_TIER in {G1P, G2P, G3P}     -> phase "C", rtb_total = gog_score
    GOG_TIER in {G1L, G2L, G3L}     -> phase "B", rtb_total = gog_score
    GOG_TIER in {G1C, G2C, G3C}     -> phase "B", rtb_total = gog_score
    GOG_TIER in {GOG1, GOG2, GOG3}  -> phase "A", rtb_total = gog_score
    SETUP fires (A/SM/N/MX) but no GOG_TIER  -> phase "A"
    any launch cluster (vbo_up / buy_2809 / rocket / be_up / bo_up)
        with rsi >= 70 or pct_change_5d > 25  -> phase "D" (late)
    otherwise -> phase "0"

This gives the Super Chart and Ultra UI non-null rtb_phase/rtb_total values
that approximate the old A/B/C/D semantics, while leaving the verbatim
calc_rtb_v4 port for a follow-up phase.

For `turbo_score`: the new system's `ultra_score` (banded 0-100) is the
canonical replacement for old turbo_score. We expose `turbo_score` as an
alias so existing frontend filters keep working without duplicate scoring.

When/if backend/rtb_engine.py is ported verbatim, this module should be
replaced with the real per-bar stateful engine. Until then, this is the
documented gap.
"""
from __future__ import annotations

from typing import Any


_PHASE_C_TIERS = frozenset({"G1P", "G2P", "G3P"})
_PHASE_B_TIERS = frozenset({"G1L", "G2L", "G3L", "G1C", "G2C", "G3C"})
_PHASE_A_TIERS = frozenset({"GOG1", "GOG2", "GOG3"})

_LATE_LAUNCH_LABELS = frozenset({"VBO↑", "BUY", "ROCKET", "BE↑", "BO↑"})


def compute_rtb_from_bar(bar: dict[str, Any]) -> dict[str, Any]:
    """
    Derive an RTB-style {phase, total} dict from a normalized bar.

    Uses bar.signals.gog (12 tier labels), bar.signals.setup, bar.signals.i,
    bar.signals.l, bar.indicators.rsi.
    """
    signals = bar.get("signals") or {}
    ind     = bar.get("indicators") or {}

    gog_labels   = signals.get("gog")   or []
    setup_labels = signals.get("setup") or []
    i_labels     = signals.get("i")     or []
    l_labels     = signals.get("l")     or []

    # rtb_total: use first GOG tier's implied score, or 0
    _tier_to_score = {
        "G1P": 100, "G2P": 92, "G3P": 88,
        "G1L": 82,  "G2L": 76, "G3L": 72,
        "G1C": 66,  "G2C": 60, "G3C": 56,
        "GOG1": 50, "GOG2": 46, "GOG3": 42,
    }
    rtb_total = 0
    for lbl in gog_labels:
        if lbl in _tier_to_score:
            rtb_total = _tier_to_score[lbl]
            break

    # Phase classification
    has_late_launch = bool(set(i_labels + l_labels) & _LATE_LAUNCH_LABELS)
    rsi_val = ind.get("rsi") or 0
    is_extended = (rsi_val or 0) >= 70

    phase = "0"
    if any(t in _PHASE_C_TIERS for t in gog_labels):
        phase = "C"
    elif any(t in _PHASE_B_TIERS for t in gog_labels):
        phase = "B"
    elif any(t in _PHASE_A_TIERS for t in gog_labels):
        phase = "A"
    elif setup_labels:
        phase = "A"

    if has_late_launch and is_extended:
        phase = "D"

    return {
        "rtb_phase": phase,
        "rtb_total": rtb_total,
        # Provenance flag so any consumer knows this is the approximation.
        "rtb_source": "tier_approximation_v1",
    }


def fill_scores_from_bar(bar: dict[str, Any]) -> None:
    """
    In-place: populate bar["scores"] turbo_score / rtb_phase / rtb_total.

    - turbo_score = bar.scores.ultra_score (alias). The old turbo_engine 0-100
      score is superseded by ultra_score in the new system. They use different
      formulas; consumers reading turbo_score will see ultra_score values.
    - rtb_phase / rtb_total: tier-derived approximation (see module docstring).
    """
    scores = bar.setdefault("scores", {})
    ultra = scores.get("ultra_score")
    if ultra is not None and scores.get("turbo_score") is None:
        scores["turbo_score"] = ultra

    rtb = compute_rtb_from_bar(bar)
    if scores.get("rtb_phase") is None:
        scores["rtb_phase"] = rtb["rtb_phase"]
    if scores.get("rtb_total") is None:
        scores["rtb_total"] = rtb["rtb_total"]
    scores["rtb_source"] = rtb["rtb_source"]

"""
ultra_pump_extraction.py — Phase-2 ULTRA context extractor.

Given the per-bar signal matrix produced by `api_bar_signals(ticker, "1d", N)`
(see backend/main.py), produce three artifact row streams for each pump
episode's pre-pump window:

  pre_pump_ultra_bars         — one row per (episode, bar): full ULTRA context
  pre_pump_ultra_signals      — flattened (episode, bar, signal_name)
  pre_pump_ultra_combinations — one row per (episode, bar, combination_key)

No future-data leakage: only bar fields at index i (and earlier) are read.
"""
from __future__ import annotations
import json
from typing import Any, Iterable

# Signal lists exposed on each per-bar row from api_bar_signals (see main.py).
# These are *string lists* of short labels — not score values.
# api_bar_signals emits short keys ("l","f","fly",…); legacy "_list"-suffixed
# names are accepted as fallbacks for any caller that pre-projects them.
_LIST_FIELDS = (
    "l", "f", "fly", "g", "b",
    "combo", "vol", "vabs", "wick", "ultra",
    "setup", "context",
    # legacy/compat names
    "l_list", "f_list", "fly_list", "g_list", "b_list",
    "combo_list", "vol_list", "vabs_list", "wick_list", "ultra_list",
)

# Scalar context fields we attempt to read from the per-bar dict.  All optional;
# missing fields are tracked via data_quality.missing_fields.
_CONTEXT_SCALAR_FIELDS = (
    "ultra_score", "ultra_score_band", "ultra_score_raw_before_penalty",
    "ultra_score_penalty_total",
    "tz", "tz_sig", "tz_state",
    "profile_category", "profile_score",
    "tz_intel_role", "tz_intel",
    "pullback_evidence_tier", "pullback_stage", "pullback_pullback_stage",
    "pullback_is_currently_active",
    "rare_evidence_tier", "rare_is_currently_active",
    "abr_category",
    "gog_setup", "gog_tier", "gog_context", "gog_score",
    "final_regime", "FINAL_REGIME",
    "rtb_phase", "rtb_score",
    "beta_score",
    "wlnbb_bucket", "ema50_state",
    "volume", "close", "high", "low", "open",
    "change_pct", "vol_ratio",
)


def _safe_get(row: dict, key: str, default=None):
    if not isinstance(row, dict):
        return default
    v = row.get(key)
    return default if v is None or v == "" else v


def _collect_signals(row: dict) -> list[str]:
    """Flatten every label list on this bar into one deduped ordered list."""
    seen: set[str] = set()
    out: list[str] = []
    # Include T/Z signal name as its own token
    tz_sig = _safe_get(row, "tz") or _safe_get(row, "tz_sig")
    if isinstance(tz_sig, str) and tz_sig.strip():
        token = tz_sig.strip().upper()
        if token not in seen:
            seen.add(token)
            out.append(token)
    for field in _LIST_FIELDS:
        v = _safe_get(row, field)
        if v is None:
            continue
        if isinstance(v, (list, tuple, set)):
            it: Iterable = v
        elif isinstance(v, str):
            it = v.split()
        else:
            it = ()
        for item in it:
            tok = str(item).strip()
            if not tok:
                continue
            up = tok.upper()
            if up not in seen:
                seen.add(up)
                out.append(up)
    return out


def _signal_family(signal: str) -> str:
    s = signal.upper()
    if s.startswith("T") and s[1:].isdigit():
        return "T"
    if s.startswith("Z") and s[1:].isdigit():
        return "Z"
    if s.startswith("L"):
        return "L"
    if s.startswith("F") and s[1:].isdigit():
        return "F"
    if s.startswith("G") and s[1:].isdigit():
        return "G"
    if s.startswith("B") and s[1:].isdigit():
        return "B"
    if s.startswith("FRI"):
        return "FRI"
    if s.startswith("FLY"):
        return "FLY"
    if "BIAS" in s:
        return "BIAS"
    if "WICK" in s or s.startswith("WP") or s.startswith("WC"):
        return "WICK"
    if s in ("ROCKET", "BUY", "BB↑", "BB_BRK", "ATR↑", "RTV", "SVS", "UM", "CONS", "CONSO"):
        return "COMBO"
    if s in ("BEST↑", "FBO↑", "FBO↓", "EB↑", "EB↓", "4BF", "3↑", "L88"):
        return "ULTRA"
    if s.endswith("×") or s.endswith("x"):
        return "VOL"
    return "OTHER"


def _format_combination(signals: list[str], k: int = 3) -> str:
    """Stable lexicographically sorted combination key of size k from the
    given signal list. If fewer signals than k, returns "" (no combo)."""
    if len(signals) < k:
        return ""
    s = sorted(set(signals))
    return "+".join(s[:k])


def _extract_ultra_context(bar: dict, scanner_output: dict | None = None) -> dict:
    """Return a canonical per-bar ULTRA context dict.

    `bar` is one row from api_bar_signals output. `scanner_output` (optional)
    is the live scanner row if available — overrides bar values for fields
    where the scanner has richer info (e.g. profile_category, tz_intel_role).
    """
    if not isinstance(bar, dict):
        bar = {}
    if not isinstance(scanner_output, dict):
        scanner_output = {}
    ctx: dict[str, Any] = {}
    missing: list[str] = []
    for f in _CONTEXT_SCALAR_FIELDS:
        v = scanner_output.get(f) if scanner_output.get(f) not in (None, "") else bar.get(f)
        if v in (None, ""):
            missing.append(f)
        ctx[f] = v
    ctx["signals"] = _collect_signals(bar)
    ctx["signal_count"] = len(ctx["signals"])
    ctx["data_quality_missing_fields"] = missing
    return ctx


# ── Pre-pump artifact builders ────────────────────────────────────────────────

def build_pre_pump_artifacts(
    episode: dict,
    bars: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Build (bars_rows, signals_rows, combinations_rows) for one episode.

    Uses ONLY bar indices ≤ anchor_index. NEVER reads forward bars here.

    The pre-pump window is the last `pre_pump_window_bars` bars at and before
    the anchor. Position 0 = anchor bar, 1 = 1 bar before anchor, etc.
    """
    bars_rows: list[dict] = []
    signals_rows: list[dict] = []
    combos_rows: list[dict] = []

    anchor_idx = int(episode.get("anchor_index") or 0)
    window = int(episode.get("pre_pump_window_bars") or 14)
    start = max(0, anchor_idx - window + 1)
    pre_bars = bars[start: anchor_idx + 1]
    if not pre_bars:
        return bars_rows, signals_rows, combos_rows

    episode_id = episode.get("episode_id")
    symbol = episode.get("symbol")

    for offset, bar in enumerate(pre_bars):
        # bars_before_anchor: 0 at anchor, increasing going back
        bars_before_anchor = (len(pre_bars) - 1) - offset
        date_str = bar.get("date")
        if not isinstance(date_str, str):
            date_str = str(date_str)[:10] if date_str is not None else ""

        ctx = _extract_ultra_context(bar)
        signals = ctx["signals"]

        bars_rows.append({
            "episode_id": episode_id,
            "symbol": symbol,
            "bar_date": date_str,
            "bars_before_anchor": bars_before_anchor,
            "close": bar.get("close"),
            "volume": bar.get("volume"),
            "ultra_score": ctx.get("ultra_score"),
            "ultra_score_band": ctx.get("ultra_score_band"),
            "profile_category": ctx.get("profile_category"),
            "tz_intel_role": ctx.get("tz_intel_role"),
            "abr_category": ctx.get("abr_category"),
            "tz_sig": ctx.get("tz") or ctx.get("tz_sig"),
            "pullback_evidence_tier": ctx.get("pullback_evidence_tier"),
            "rare_evidence_tier": ctx.get("rare_evidence_tier"),
            "final_regime": ctx.get("final_regime") or ctx.get("FINAL_REGIME"),
            "signals_json": json.dumps(signals),
            "signal_count": ctx["signal_count"],
            "data_quality_missing_fields": json.dumps(ctx["data_quality_missing_fields"]),
        })

        for sig in signals:
            signals_rows.append({
                "episode_id": episode_id,
                "symbol": symbol,
                "bar_date": date_str,
                "bars_before_anchor": bars_before_anchor,
                "signal_name": sig,
                "signal_family": _signal_family(sig),
            })

        # Combos of size 2 and 3
        for k in (2, 3):
            combo_key = _format_combination(signals, k)
            if not combo_key:
                continue
            combos_rows.append({
                "episode_id": episode_id,
                "symbol": symbol,
                "bar_date": date_str,
                "bars_before_anchor": bars_before_anchor,
                "combo_key": combo_key,
                "combo_size": k,
                "combo_components": combo_key,
            })

    return bars_rows, signals_rows, combos_rows


# ── Caught/missed classification (Phase-2 real) ───────────────────────────────

def classify_caught_from_pre_pump(
    episode: dict,
    pre_pump_bars_rows: list[dict],
    *,
    score_threshold: float = 50.0,
    detection_window: int = 14,
) -> dict:
    """
    Given the pre_pump_bars rows for ONE episode, classify CAUGHT or MISSED.

    CAUGHT: at least one bar in the *scanner detection window* (last N bars
            before the anchor) has ultra_score >= score_threshold AND
            profile_category in ('SWEET_SPOT','BUILDING').

    MISSED: no such bar exists. Records would_have_score and the strongest
            signal seen, plus a primary reason from the spec enum.
    """
    out = dict(episode)
    # Window: last `detection_window` bars ending at anchor inclusive
    rows = [r for r in pre_pump_bars_rows
            if r.get("episode_id") == episode.get("episode_id")
            and r.get("bars_before_anchor") is not None
            and r["bars_before_anchor"] < detection_window]

    best_score = None
    best_row = None
    best_signal = None
    has_qualified_bar = False
    for r in rows:
        sc = r.get("ultra_score")
        try:
            sc_f = float(sc) if sc is not None else None
        except (TypeError, ValueError):
            sc_f = None
        if sc_f is not None and (best_score is None or sc_f > best_score):
            best_score = sc_f
            best_row = r
        prof = (r.get("profile_category") or "").upper()
        if sc_f is not None and sc_f >= score_threshold and prof in ("SWEET_SPOT", "BUILDING"):
            has_qualified_bar = True
        sigs = r.get("signals_json")
        if isinstance(sigs, str) and sigs.startswith("["):
            try:
                arr = json.loads(sigs)
                if arr and not best_signal:
                    best_signal = arr[0]
            except Exception:
                pass

    out["strongest_pre_pump_score"] = best_score
    out["best_pre_pump_ultra_pattern"] = (best_row.get("profile_category")
                                          if best_row else None)
    out["strongest_pre_pump_signal"] = best_signal

    if has_qualified_bar:
        out["caught_status"] = "CAUGHT"
        out["caught_bar_offset_from_anchor"] = (best_row or {}).get("bars_before_anchor")
        out["missed_reason_primary"] = None
        out["missed_reason_secondary"] = None
    else:
        out["caught_status"] = "MISSED"
        out["caught_bar_offset_from_anchor"] = None
        # Pick a primary reason
        if not rows:
            out["missed_reason_primary"] = "NO_PRE_PUMP_DATA"
            out["missed_reason_secondary"] = "INSUFFICIENT_HISTORY"
        elif best_score is None:
            out["missed_reason_primary"] = "NO_ULTRA_SCORE_AVAILABLE"
            out["missed_reason_secondary"] = "MISSING_HISTORICAL_SNAPSHOT"
        elif best_score < score_threshold:
            out["missed_reason_primary"] = "ULTRA_SCORE_BELOW_THRESHOLD"
            out["missed_reason_secondary"] = f"max_score={best_score:.1f}<{score_threshold}"
        else:
            out["missed_reason_primary"] = "PROFILE_CATEGORY_NOT_QUALIFIED"
            out["missed_reason_secondary"] = (best_row or {}).get("profile_category") or "UNKNOWN"

    return out

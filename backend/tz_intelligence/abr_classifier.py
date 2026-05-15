"""ABR (A/B/B+/R) classification overlay for TZ Signal Intelligence.

Read-only: does NOT alter role, score, or any existing TZ field.
Supported universes: sp500 (SP500 thresholds/gate) and nasdaq_gt5 (NASDAQ thresholds/gate).

Root design: seq4_str carries short signal codes (T4, Z1G, …) while
matrix.composite is keyed by full composite names (T4L13NU, Z1GL5ED, …).
Quality for prev1/prev2 is therefore computed by aggregating ALL composite
rules whose pattern STARTS WITH the signal prefix (e.g. all T4L* patterns).
"""
from __future__ import annotations
import csv
import os
import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

# ── Universe mapping ──────────────────────────────────────────────────────────

ABR_UNIVERSE_MAP: Dict[str, str] = {
    "sp500":      "SP500",
    "nasdaq_gt5": "NASDAQ",
}
ABR_SUPPORTED = frozenset(ABR_UNIVERSE_MAP)

# ── Quality thresholds (weighted med10d_pct across signal composites) ─────────

_THRESHOLDS: Dict[str, Dict[str, float]] = {
    "SP500":   {"STRONG": 0.8,  "GOOD": 0.3,  "AVERAGE": 0.0,  "REJECT": -1e9},
    "NASDAQ":  {"STRONG": 0.4,  "GOOD": 0.1,  "AVERAGE": -0.1, "REJECT": -1e9},
}

# Gate: minimum quality of prev1 to qualify for A/B/B+
_GATE: Dict[str, str] = {
    "SP500":  "STRONG",
    "NASDAQ": "GOOD",   # GOOD or STRONG
}

# ── Signal prefix helpers ─────────────────────────────────────────────────────

_SIG_RE = re.compile(r'^([TZL]\d+[GR]?)')


def _sig_prefix(label: str) -> str:
    """Extract signal prefix from a raw signal or full composite name.

    'T4'        → 'T4'
    'Z1G'       → 'Z1G'
    'T4L13NU'   → 'T4'
    'Z1GL5ED'   → 'Z1G'
    '—' / ''    → ''
    """
    if not label or label == "—":
        return ""
    m = _SIG_RE.match(label)
    return m.group(1) if m else ""


def _composite_matches(pat: str, prefix: str) -> bool:
    """True iff composite pattern *pat* belongs to signal *prefix*.

    Uses the lane-separator 'L' so 'Z1' never matches 'Z1GL5ED':
      prefix='Z1'  pat='Z1L12NU'   → pat[2]='L' → True
      prefix='Z1'  pat='Z1GL5ED'   → pat[2]='G' → False
      prefix='Z1G' pat='Z1GL5ED'   → pat[3]='L' → True
    """
    if not prefix:
        return False
    if not pat.startswith(prefix):
        return False
    rest_idx = len(prefix)
    if rest_idx == len(pat):   # exact match
        return True
    return pat[rest_idx] == "L"


# ── ABR DB path discovery ─────────────────────────────────────────────────────

def _find_abr_csv() -> str:
    base = os.path.dirname(__file__)
    return os.path.join(base, "ABR_rule_database.csv")


# ── ABR DB loading ────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_abr_db() -> Dict[Tuple[str, str, str, str], dict]:
    """Return dict keyed by (universe, signal, sequence, category).

    BASELINE and SEQ_BASELINE rows are skipped.
    """
    path = _find_abr_csv()
    index: Dict[Tuple[str, str, str, str], dict] = {}
    if not os.path.exists(path):
        return index
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            cat = row.get("category", "").strip()
            if cat in ("BASELINE", "SEQ_BASELINE"):
                continue
            key = (
                row.get("universe", "").strip(),
                row.get("signal",   "").strip(),
                row.get("sequence", "").strip(),
                cat,
            )
            index[key] = row
    return index


def abr_db_stats() -> dict:
    """Diagnostic: return DB file status and loaded rule count."""
    path = _find_abr_csv()
    loaded = os.path.exists(path)
    db = _load_abr_db()
    return {"loaded": loaded, "path": path, "row_count": len(db)}


# ── Quality classification ────────────────────────────────────────────────────

def _classify_quality(med: Optional[float], abr_universe: str) -> str:
    if med is None:
        return "UNKNOWN"
    thr = _THRESHOLDS.get(abr_universe, _THRESHOLDS["SP500"])
    if med >= thr["STRONG"]:
        return "STRONG"
    if med >= thr["GOOD"]:
        return "GOOD"
    if med >= thr["AVERAGE"]:
        return "AVERAGE"
    return "REJECT"


def _composite_med_for_signal(signal: str, matrix, scan_universe: str) -> Optional[float]:
    """Weighted-average med10d_pct for all composite rules whose pattern
    starts with the given signal prefix.

    e.g. signal='T4' aggregates T4L5NU, T4L13NU, T4L34NU, …
         signal='Z1G' aggregates Z1GL5ED, Z1GL46ED, … (NOT Z1L* rules)

    Falls back to reject_composite if no positive rules found.
    """
    if not signal or signal == "—":
        return None
    prefix = _sig_prefix(signal)
    if not prefix:
        return None

    allowed = matrix.allowed_univs(scan_universe)
    total_n = 0.0
    weighted = 0.0
    found = False

    for univ in allowed:
        for pat, rules in matrix.composite.get(univ, {}).items():
            if not _composite_matches(pat, prefix):
                continue
            for rule in rules:
                try:
                    n   = float(rule.get("n") or 0)
                    med = float(rule.get("med10d_pct") or 0)
                except (TypeError, ValueError):
                    continue
                if n > 0:
                    weighted += med * n
                    total_n  += n
                    found = True

    if not found:
        for univ in allowed:
            for pat, rules in matrix.reject_composite.get(univ, {}).items():
                if not _composite_matches(pat, prefix):
                    continue
                for rule in rules:
                    try:
                        n   = float(rule.get("n") or 0)
                        med = float(rule.get("med10d_pct") or 0)
                    except (TypeError, ValueError):
                        continue
                    if n > 0:
                        weighted += med * n
                        total_n  += n
                        found = True

    if not found or total_n == 0:
        return None
    return weighted / total_n


# ── ABR category logic ────────────────────────────────────────────────────────

def _gate_passes(prev1_quality: str, abr_universe: str) -> bool:
    gate = _GATE.get(abr_universe, "STRONG")
    if gate == "STRONG":
        return prev1_quality == "STRONG"
    return prev1_quality in ("GOOD", "STRONG")   # NASDAQ gate


def _abr_category(prev1_quality: str, prev2_quality: str, abr_universe: str) -> str:
    if prev1_quality == "UNKNOWN" or prev2_quality == "UNKNOWN":
        return "UNKNOWN"
    if not _gate_passes(prev1_quality, abr_universe):
        return "R"
    if prev2_quality == "STRONG":
        return "B+"
    if prev2_quality == "GOOD":
        return "B"
    if prev2_quality == "AVERAGE":
        return "A"
    return "R"   # prev2 = REJECT


# ── ABR role suggestion / action hint (non-binding) ───────────────────────────

def _role_suggestion(category: str, current_role: str) -> str:
    is_short = "SHORT" in current_role
    if category == "A":
        return "CHECK_SHORT_CONFLICT" if is_short else "BULL_CONTINUATION_CANDIDATE"
    if category in ("B", "B+"):
        return "CHECK_SHORT_CONFLICT" if is_short else "MOMENTUM_CONTINUATION_CONTEXT"
    if category == "R":
        return "ABR_R_NO_BUY"   # R is not bullish; specific short confirmation handled by context flags
    return ""


def _action_hint(category: str) -> str:
    return {
        "A":  "PRIMARY_LONG_CONTEXT",
        "B":  "SECONDARY_LONG_CONTEXT",
        "B+": "MOMENTUM_CONTINUATION_CONTEXT",
        "R":  "DO_NOT_BUY_OR_SHORT_WATCH_IF_NEGATIVE",
    }.get(category, "NO_ABR_EDGE")


# ── ABR context flags (role × ABR category cross-signal) ─────────────────────

_PULLBACK_CONFIRM_ROLES = frozenset({"PULLBACK_READY_B", "PULLBACK_WATCH"})
_SHORT_ROLE             = "SHORT_WATCH"
_CONTINUATION_ROLES     = frozenset({"PULLBACK_WATCH", "BULL_WATCH"})
_BULLISH_ABR_CATS       = frozenset({"A", "B", "B+"})
_STRONG_ABR_CATS        = frozenset({"B", "B+"})


def compute_abr_context_flags(
    role: str,
    abr_category: str,
    abr_med10d_pct: Optional[float],
    above_ema20: bool,
    above_ema50: bool,
    liquidity_tier: str,
) -> dict:
    """Compute cross-signal ABR context flags without altering TZ role/score.

    Returns three new fields:
      abr_conflict_flag      — non-empty when ABR contradicts TZ role
      abr_confirmation_flag  — non-empty when ABR corroborates TZ role
      abr_context_type       — descriptive label for the conflict/confirmation
    Also returns an updated abr_role_suggestion when rule 4 applies.
    """
    conflict_flag      = ""
    confirmation_flag  = ""
    context_type       = ""
    role_suggestion    = ""

    # Rule 1: Pullback confirmation
    if role in _PULLBACK_CONFIRM_ROLES and abr_category in _STRONG_ABR_CATS:
        confirmation_flag = "ABR_PULLBACK_CONFIRMED"
        context_type      = "PULLBACK_ABR_ALIGNED"

    # Rule 2: Short conflict (bearish price structure + bullish ABR sequence)
    if role == _SHORT_ROLE and abr_category in _BULLISH_ABR_CATS:
        conflict_flag  = "ABR_BULLISH_CONTEXT_CONFLICT"
        context_type   = "MIXED_BEARISH_PRICE_BULLISH_ABR"

    # Rule 3: Short confirmation (bearish ABR reinforces SHORT_WATCH)
    if (role == _SHORT_ROLE and abr_category == "R"
            and abr_med10d_pct is not None and abr_med10d_pct < 0):
        confirmation_flag = "ABR_SHORT_CONFIRMED"
        role_suggestion   = "ABR_SHORT_CONFIRMATION_CANDIDATE"
        if not context_type:
            context_type = "SHORT_ABR_ALIGNED"

    # Rule 4: Continuation suggestion upgrade
    if (role in _CONTINUATION_ROLES
            and abr_category in _STRONG_ABR_CATS
            and above_ema20
            and above_ema50
            and liquidity_tier in ("OK", "STRONG")):
        role_suggestion = "BULL_CONTINUATION_CANDIDATE"

    return {
        "abr_conflict_flag":     conflict_flag,
        "abr_confirmation_flag": confirmation_flag,
        "abr_context_type":      context_type,
        "abr_role_suggestion":   role_suggestion,   # overrides base suggestion if set
    }


# ── Main classifier ───────────────────────────────────────────────────────────

_EMPTY: dict = {
    "abr_category":          "UNKNOWN",
    "abr_sequence":          "",
    "abr_prev1_composite":   "",
    "abr_prev2_composite":   "",
    "abr_prev1_comp_med10d": None,
    "abr_prev2_comp_med10d": None,
    "abr_prev1_quality":     "UNKNOWN",
    "abr_prev2_quality":     "UNKNOWN",
    "abr_gate_pass":         False,
    "abr_rule_found":        False,
    "abr_n":                 None,
    "abr_med10d_pct":        None,
    "abr_avg10d_pct":        None,
    "abr_fail10d_pct":       None,
    "abr_win10d_pct":        None,
    "abr_action_hint":       "NO_ABR_EDGE",
    "abr_role_suggestion":   "",
}


def classify_abr(
    final_signal: str,
    seq4_str: str,
    history_rows: List[dict],
    matrix,
    scan_universe: str,
    current_role: str = "",
) -> dict:
    """Classify a bar's ABR category.

    Args:
        final_signal:  signal on the current bar (e.g. "T3", "Z2G")
        seq4_str:      "|"-joined 4-bar sequence (prev3|prev2|prev1|current)
        history_rows:  previous bar dicts (oldest first), each from stock_stat CSV
        matrix:        MatrixIndex instance
        scan_universe: "sp500" or "nasdaq_gt5"
        current_role:  TZ role already assigned (used only for role_suggestion)
    """
    if scan_universe not in ABR_SUPPORTED or not final_signal:
        return dict(_EMPTY)

    abr_universe = ABR_UNIVERSE_MAP[scan_universe]

    # ── Extract prev signal codes from seq4_str (prev3|prev2|prev1|current) ──
    parts = seq4_str.split("|") if seq4_str else []
    abr_seq   = "|".join(parts[:3]) if len(parts) >= 3 else ""
    prev1_sig = parts[2] if len(parts) > 2 else ""
    prev2_sig = parts[1] if len(parts) > 1 else ""

    # Fallback to history_rows signal columns if seq4 is incomplete
    if not prev1_sig and history_rows:
        b = history_rows[-1]
        prev1_sig = b.get("t_signal") or b.get("z_signal") or b.get("l_signal") or ""
    if not prev2_sig and len(history_rows) >= 2:
        b = history_rows[-2]
        prev2_sig = b.get("t_signal") or b.get("z_signal") or b.get("l_signal") or ""

    # ── Composite quality: prefix-based aggregation across matrix ─────────────
    prev1_med = _composite_med_for_signal(prev1_sig, matrix, scan_universe)
    prev2_med = _composite_med_for_signal(prev2_sig, matrix, scan_universe)

    prev1_quality = _classify_quality(prev1_med, abr_universe)
    prev2_quality = _classify_quality(prev2_med, abr_universe)

    gate_pass = _gate_passes(prev1_quality, abr_universe)
    category  = _abr_category(prev1_quality, prev2_quality, abr_universe)

    # ── ABR DB lookup (exact match; category is computed regardless) ──────────
    db         = _load_abr_db()
    rule       = db.get((abr_universe, final_signal, abr_seq, category))
    rule_found = rule is not None

    def _f(key: str) -> Optional[float]:
        if not rule:
            return None
        v = rule.get(key, "")
        try:
            return float(v) if v not in ("", None) else None
        except (TypeError, ValueError):
            return None

    return {
        "abr_category":          category,
        "abr_sequence":          abr_seq,
        "abr_prev1_composite":   prev1_sig,
        "abr_prev2_composite":   prev2_sig,
        "abr_prev1_comp_med10d": round(prev1_med, 4) if prev1_med is not None else None,
        "abr_prev2_comp_med10d": round(prev2_med, 4) if prev2_med is not None else None,
        "abr_prev1_quality":     prev1_quality,
        "abr_prev2_quality":     prev2_quality,
        "abr_gate_pass":         gate_pass,
        "abr_rule_found":        rule_found,
        "abr_n":                 _f("n"),
        "abr_med10d_pct":        _f("med"),
        "abr_avg10d_pct":        _f("avg"),
        "abr_fail10d_pct":       _f("fail"),
        "abr_win10d_pct":        _f("win"),
        "abr_action_hint":       _action_hint(category),
        "abr_role_suggestion":   _role_suggestion(category, current_role),
    }

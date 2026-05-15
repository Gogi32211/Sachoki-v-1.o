"""Load and index the TZ Signal Intelligence master matrix CSV.

Indexes are universe-scoped so SP500 scans never touch NASDAQ_GT5 rules.
"""
from __future__ import annotations
import csv
import os
from functools import lru_cache
from typing import Dict, List, Tuple, Optional


# ── Universe normalisation ────────────────────────────────────────────────────

_UNIVERSE_ALIASES: Dict[str, str] = {
    "SP500":       "SP500",
    "NASDAQ_GT5":  "NASDAQ_GT5",
    "NASDAQ >$5":  "NASDAQ_GT5",
    "GLOBAL":      "GLOBAL",
}

# Map scan universe key → matrix universe keys that are allowed
_ALLOWED_MATRIX_UNIVS: Dict[str, set] = {
    "sp500":      {"SP500",      "GLOBAL"},
    "nasdaq":     {"NASDAQ_GT5", "GLOBAL"},
    "nasdaq_gt5": {"NASDAQ_GT5", "GLOBAL"},  # explicit NASDAQ > $5 universe
    "russell2k":  {"SP500",      "GLOBAL"},   # best-effort; no dedicated RUSSELL rules
    "all_us":     {"SP500", "NASDAQ_GT5", "GLOBAL"},
    "split":      {"SP500", "NASDAQ_GT5", "GLOBAL"},
}


def _norm_univ(raw: str) -> str:
    return _UNIVERSE_ALIASES.get(raw.strip(), raw.strip())


# ── Conflict resolution thresholds ────────────────────────────────────────────

CONFLICT_POSITIVE_MED   =  0.8   # med10d_pct >= this → positive wins
CONFLICT_POSITIVE_FAIL  = 25.0   # fail10d_pct < this → positive wins (combined)
CONFLICT_REJECT_MED     =  0.0   # med10d_pct < this → reject wins
CONFLICT_REJECT_FAIL    = 28.0   # fail10d_pct >= this → reject wins


def _float_metric(r: dict, key: str) -> Optional[float]:
    v = r.get(key, "")
    try:
        return float(v) if v not in ("", None) else None
    except (ValueError, TypeError):
        return None


def _resolve_conflict(pos_rules: List[dict], neg_rules: List[dict]) -> str:
    """Return 'POSITIVE', 'REJECT', or 'CONFLICT'."""
    # Use best (highest med) positive rule and worst (lowest med) negative rule
    pos_meds  = [_float_metric(r, "med10d_pct")  for r in pos_rules]
    pos_fails = [_float_metric(r, "fail10d_pct") for r in pos_rules]
    neg_meds  = [_float_metric(r, "med10d_pct")  for r in neg_rules]
    neg_fails = [_float_metric(r, "fail10d_pct") for r in neg_rules]

    best_pos_med  = max((m for m in pos_meds  if m is not None), default=None)
    best_pos_fail = min((f for f in pos_fails if f is not None), default=None)
    worst_neg_med = min((m for m in neg_meds  if m is not None), default=None)
    worst_neg_fail= max((f for f in neg_fails if f is not None), default=None)

    reject_wins = (
        (worst_neg_med  is not None and worst_neg_med  < CONFLICT_REJECT_MED) or
        (worst_neg_fail is not None and worst_neg_fail >= CONFLICT_REJECT_FAIL)
    )
    positive_wins = (
        (best_pos_med  is not None and best_pos_med  >= CONFLICT_POSITIVE_MED) and
        (best_pos_fail is not None and best_pos_fail < CONFLICT_POSITIVE_FAIL)
    )

    if reject_wins:
        return "REJECT"
    if positive_wins:
        return "POSITIVE"
    return "CONFLICT"


# ── CSV path discovery ────────────────────────────────────────────────────────

def _find_csv() -> str:
    base = os.path.dirname(__file__)
    candidates = [
        os.path.join(base, "..", "tz_intelligence_package",
                     "TZ_SIGNAL_INTELLIGENCE_master_matrix_seed.csv"),
        os.path.join(base, "..", "..", "tz_intelligence_package",
                     "TZ_SIGNAL_INTELLIGENCE_master_matrix_seed.csv"),
    ]
    for p in candidates:
        p = os.path.normpath(p)
        if os.path.exists(p):
            return p
    return os.path.normpath(candidates[0])

_CSV_PATH = _find_csv()


# ── Public API ────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def load_matrix() -> "MatrixIndex":
    rows: List[dict] = []
    with open(_CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return MatrixIndex(rows)


class MatrixIndex:
    """Universe-scoped, conflict-resolved index of all matrix rules."""

    def __init__(self, rows: List[dict]):
        self.rows = rows

        # universe-scoped: {norm_univ: {pattern: [rules]}}
        self.composite:        Dict[str, Dict[str, List[dict]]] = {}
        self.reject_composite: Dict[str, Dict[str, List[dict]]] = {}
        self.seq4:             Dict[str, Dict[str, List[dict]]] = {}
        self.reject_seq4:      Dict[str, Dict[str, List[dict]]] = {}

        # signal-level baseline (universe-independent, best-effort)
        self.baseline: Dict[str, dict] = {}

        # meta/global rules (EMA, PRICE_POSITION, etc.)
        self.meta: List[dict] = []

        # pre-computed conflict map:
        # {norm_univ: {pattern: 'POSITIVE'|'REJECT'|'CONFLICT'}}
        self._conflicts: Dict[str, Dict[str, str]] = {}

        for r in rows:
            rt   = r["rule_type"]
            pat  = r["pattern"].strip()
            univ = _norm_univ(r.get("universe", ""))

            if rt == "BASELINE":
                self.baseline.setdefault(r["signal"], r)
            elif rt == "COMPOSITE":
                self.composite.setdefault(univ, {}).setdefault(pat, []).append(r)
            elif rt == "REJECT_COMPOSITE":
                self.reject_composite.setdefault(univ, {}).setdefault(pat, []).append(r)
            elif rt == "SEQ4":
                self.seq4.setdefault(univ, {}).setdefault(pat, []).append(r)
            elif rt == "REJECT_SEQ4":
                self.reject_seq4.setdefault(univ, {}).setdefault(pat, []).append(r)
            else:
                self.meta.append(r)

        self._build_conflict_map()

    def _build_conflict_map(self):
        """Pre-compute conflict resolution for every pattern that appears in
        both positive and reject indexes for the same universe."""
        all_univs = set(self.composite) | set(self.reject_composite) | \
                    set(self.seq4)       | set(self.reject_seq4)
        for univ in all_univs:
            self._conflicts.setdefault(univ, {})
            # composite conflicts
            pos_pats = set(self.composite.get(univ, {}))
            neg_pats = set(self.reject_composite.get(univ, {}))
            for pat in pos_pats & neg_pats:
                res = _resolve_conflict(
                    self.composite[univ][pat],
                    self.reject_composite[univ][pat],
                )
                self._conflicts[univ][f"COMP:{pat}"] = res
            # seq4 conflicts
            pos_pats = set(self.seq4.get(univ, {}))
            neg_pats = set(self.reject_seq4.get(univ, {}))
            for pat in pos_pats & neg_pats:
                res = _resolve_conflict(
                    self.seq4[univ][pat],
                    self.reject_seq4[univ][pat],
                )
                self._conflicts[univ][f"SEQ4:{pat}"] = res

    # ── Scoped lookups ────────────────────────────────────────────────────────

    def allowed_univs(self, scan_universe: str) -> set:
        return _ALLOWED_MATRIX_UNIVS.get(scan_universe, {"SP500", "GLOBAL"})

    def get_composite_rules(self, pattern: str, scan_universe: str) -> Tuple[List[dict], List[dict]]:
        """Return (positive_rules, reject_rules) scoped to scan_universe."""
        allowed = self.allowed_univs(scan_universe)
        pos, neg = [], []
        for univ in allowed:
            pos += self.composite.get(univ, {}).get(pattern, [])
            neg += self.reject_composite.get(univ, {}).get(pattern, [])
        return pos, neg

    def get_seq4_rules(self, pattern: str, scan_universe: str) -> Tuple[List[dict], List[dict]]:
        """Return (positive_rules, reject_rules) scoped to scan_universe."""
        allowed = self.allowed_univs(scan_universe)
        pos, neg = [], []
        for univ in allowed:
            pos += self.seq4.get(univ, {}).get(pattern, [])
            neg += self.reject_seq4.get(univ, {}).get(pattern, [])
        return pos, neg

    def get_conflict(self, pattern: str, kind: str, scan_universe: str) -> Optional[str]:
        """Return pre-computed conflict resolution or None if no conflict."""
        key = f"{kind}:{pattern}"
        allowed = self.allowed_univs(scan_universe)
        for univ in allowed:
            res = self._conflicts.get(univ, {}).get(key)
            if res is not None:
                return res
        return None

    # ── Meta bonus helpers ────────────────────────────────────────────────────

    def get_ema_bonus(self) -> int:
        for r in self.meta:
            if r["pattern"] == "EMA50_RECLAIM":
                return int(r["score_base"] or 0)
        return 10

    def get_price_position_bonus(self) -> int:
        for r in self.meta:
            if r["pattern"] == "FINAL_CLOSE_TOP_75PCT_4BAR":
                return int(r["score_base"] or 0)
        return 10

    def get_short_go_bonus(self) -> int:
        for r in self.meta:
            if r["pattern"] == "BREAK_4BAR_LOW_AFTER_REJECT":
                return int(r["score_base"] or 0)
        return 35

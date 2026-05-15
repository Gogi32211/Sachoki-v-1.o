"""
ultra_signal_parser.py — robust parser for Stock Stat / Bulk Signal CSV rows.

Stock Stat CSV stores per-bar signals as compact text columns (T, Z, L, F,
FLY, G, B, Combo, ULT, VOL, VABS, WICK) where each cell is a space-separated
list of label tokens such as ``"ABS CLB VBO↑ RS+"``. Live ULTRA on the other
hand carries flat boolean keys (``buy_2809``, ``bb_brk``, ``rs_strong``, …).

This parser normalises BOTH shapes into the same canonical flag dict so
ULTRA Replay combo analytics, the ULTRA Score helper, and any other
downstream consumer can ask one question — ``flags['rs_strong']`` —
without caring about the source.

Hard rules
  • Pure read-only — never mutates input rows.
  • Robust to empty strings / NaN / pandas missing values.
  • Tolerates space, comma, pipe, semicolon separators.
  • Handles unicode arrow tokens (``↑`` / ``↓``).
  • Accepts case-insensitive column lookup (``Combo`` vs ``combo``,
    ``VABS`` vs ``vabs``, ``ULT`` vs ``ult`` vs ``ultra``).
  • NEVER reads forward-return / future-bar fields.
"""
from __future__ import annotations

from typing import Iterable

# ─────────────────────────────────────────────────────────────────────────────
# Token / column normalisation
# ─────────────────────────────────────────────────────────────────────────────

# Accept all separators the spec mentions (space / comma / pipe / semicolon).
_SEP_TRANSLATE = str.maketrans({",": " ", "|": " ", ";": " ", "/": " ",
                                  "\t": " ", "\n": " "})

# Map of canonical column → list of accepted CSV header variants. Stock Stat
# uses mixed case; live ULTRA orchestrator emits lowercase. We accept both.
_COLUMN_ALIASES = {
    "T":     ("T", "t_signal", "t"),
    "Z":     ("Z", "z_signal", "z"),
    "L":     ("L", "l", "l_signal"),
    "F":     ("F", "f"),
    "FLY":   ("FLY", "fly"),
    "G":     ("G", "g"),
    "B":     ("B", "b"),
    "Combo": ("Combo", "combo", "COMBO"),
    "ULT":   ("ULT", "ULTRA", "ultra", "ult"),
    "VOL":   ("VOL", "vol"),
    "VABS":  ("VABS", "vabs"),
    "WICK":  ("WICK", "wick"),
}


def _is_nan(v) -> bool:
    try:
        return v != v  # NaN check
    except Exception:
        return False


def _stringify(v) -> str:
    if v is None or _is_nan(v):
        return ""
    return str(v)


def _column_text(row: dict, canonical: str) -> str:
    """Return the raw text value from any of the canonical column's aliases."""
    if not isinstance(row, dict):
        return ""
    for k in _COLUMN_ALIASES.get(canonical, (canonical,)):
        if k in row:
            v = row[k]
            if v is None or _is_nan(v):
                continue
            if isinstance(v, (list, tuple, set)):
                return " ".join(str(x) for x in v if x not in (None, ""))
            s = str(v)
            if s.strip():
                return s
    return ""


def _tokens_in_column(row: dict, canonical: str) -> set:
    """Return the uppercase token set for the named column. Tolerates any
    of the accepted separators."""
    txt = _column_text(row, canonical)
    if not txt:
        return set()
    norm = txt.translate(_SEP_TRANSLATE)
    return set(t.strip().upper() for t in norm.split() if t.strip())


def _all_tokens(row: dict, columns: Iterable[str]) -> set:
    out: set = set()
    for c in columns:
        out |= _tokens_in_column(row, c)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public helpers (advertised in the task spec)
# ─────────────────────────────────────────────────────────────────────────────

def has_token(row: dict, column: str, token: str) -> bool:
    """True iff ``token`` (case-insensitive) is one of the tokens in ``row[column]``."""
    if not token:
        return False
    return token.upper() in _tokens_in_column(row, column)


def has_any_token(row: dict, columns: Iterable[str], tokens: Iterable[str]) -> bool:
    """True iff any of ``tokens`` appears in any of ``columns``."""
    want = {t.upper() for t in tokens if t}
    if not want:
        return False
    return bool(want & _all_tokens(row, columns))


# ─────────────────────────────────────────────────────────────────────────────
# Truthy helper for live flat boolean keys (live ULTRA shape)
# ─────────────────────────────────────────────────────────────────────────────

def _truthy(v) -> bool:
    if v is None or v == "" or _is_nan(v):
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "0.0", "false", "none", "null", "nan")
    return bool(v)


# ─────────────────────────────────────────────────────────────────────────────
# Main parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_stock_stat_signals(row: dict) -> dict:
    """Parse a Stock Stat / Bulk Signal CSV row (or live ULTRA row) into a
    canonical boolean-flag dict + a small set of pass-through string fields.

    The output is read directly by Replay Analytics combo detection and by
    the ULTRA Score helper. Missing inputs simply produce False for the
    corresponding flag — never raises.
    """
    if not isinstance(row, dict):
        row = {}

    # Column-token sets — built once per row so repeated lookups are cheap.
    tok_T     = _tokens_in_column(row, "T")
    tok_Z     = _tokens_in_column(row, "Z")
    tok_L     = _tokens_in_column(row, "L")
    tok_F     = _tokens_in_column(row, "F")
    tok_G     = _tokens_in_column(row, "G")
    tok_B     = _tokens_in_column(row, "B")
    tok_FLY   = _tokens_in_column(row, "FLY")
    tok_Combo = _tokens_in_column(row, "Combo")
    tok_ULT   = _tokens_in_column(row, "ULT")
    tok_VOL   = _tokens_in_column(row, "VOL")
    tok_VABS  = _tokens_in_column(row, "VABS")
    tok_WICK  = _tokens_in_column(row, "WICK")

    # ── Flat boolean fallback (live ULTRA shape) ─────────────────────────────
    def flat(*keys) -> bool:
        return any(_truthy(row.get(k)) for k in keys)

    def in_any(token: str, *colsets) -> bool:
        token = token.upper()
        return any(token in s for s in colsets)

    # Pass-through textual fields — useful for combo detection & audit
    t_signal = _column_text(row, "T") or row.get("tz_sig", "") or ""
    z_signal = _column_text(row, "Z") or ""

    # ── L / WLNBB ────────────────────────────────────────────────────────────
    l34   = "L34"   in tok_L or flat("l34", "l34_active")
    l43   = "L43"   in tok_L or flat("l43", "l43_active")
    l64   = "L64"   in tok_L or flat("l64", "l64_active")
    l22   = "L22"   in tok_L or flat("l22", "l22_active")
    fri34 = "FRI34" in tok_L or flat("fri34")
    fri43 = "FRI43" in tok_L or flat("fri43")
    fri64 = "FRI64" in tok_L or flat("fri64")
    blue  = ("BL" in tok_L or "BLUE" in tok_L) or flat("blue")
    cci_ready = "CCI" in tok_L or flat("cci_ready")

    # ── Breakout / Ultra ─────────────────────────────────────────────────────
    bo_up   = in_any("BO↑",  tok_ULT, tok_L, tok_Combo) or flat("bo_up")
    bo_dn   = in_any("BO↓",  tok_ULT, tok_L, tok_Combo) or flat("bo_dn")
    bx_up   = in_any("BX↑",  tok_ULT, tok_L, tok_Combo) or flat("bx_up")
    bx_dn   = in_any("BX↓",  tok_ULT, tok_L, tok_Combo) or flat("bx_dn")
    be_up   = in_any("BE↑",  tok_ULT, tok_L, tok_Combo) or flat("be_up")
    be_dn   = in_any("BE↓",  tok_ULT, tok_L, tok_Combo) or flat("be_dn")
    eb_bull = in_any("EB↑",  tok_ULT, tok_L, tok_Combo) or flat("eb_bull")
    eb_bear = in_any("EB↓",  tok_ULT, tok_L, tok_Combo) or flat("eb_bear")
    fbo_bull = in_any("FBO↑", tok_ULT, tok_Combo) or flat("fbo_bull")
    fbo_bear = in_any("FBO↓", tok_ULT, tok_Combo) or flat("fbo_bear")
    ultra_3up = ("3↑" in tok_ULT or "3UP" in tok_ULT) or flat("ultra_3up")
    ultra_3dn = ("3↓" in tok_ULT or "3DN" in tok_ULT) or flat("ultra_3dn")
    four_bf   = ("4BF" in tok_ULT or "4BF" in tok_Combo) or flat("bf_buy")
    sig_260308 = ("260308" in tok_ULT) or flat("sig_260308")
    l88        = ("L88"    in tok_ULT) or flat("sig_l88")

    # ── VABS / setup ─────────────────────────────────────────────────────────
    abs_sig = ("ABS" in tok_VABS) or flat("abs_sig")
    va = ("VA" in tok_Combo) or ("VA" in tok_VABS) or flat("va")
    svs_2809 = ("SVS" in tok_Combo) or ("SVS" in tok_VABS) or flat("svs_2809")
    climb_sig = (
        "CLB" in tok_VABS or "CLM" in tok_VABS or "CLIMB" in tok_VABS
        or flat("climb_sig")
    )
    load_sig = (
        "LOAD" in tok_VABS or "LD" in tok_VABS or "LD" in tok_Combo
        or flat("load_sig")
    )
    strong_sig = (
        "STR" in tok_VABS or "STRONG" in tok_VABS or "STR" in tok_Combo
        or flat("strong_sig")
    )
    vbo_up = "VBO↑" in tok_VABS or flat("vbo_up")
    vbo_dn = "VBO↓" in tok_VABS or flat("vbo_dn")
    ns = "NS" in tok_VABS or flat("ns")
    nd = "ND" in tok_VABS or flat("nd")
    sq = "SQ" in tok_VABS or flat("sq")
    sc = "SC" in tok_VABS or flat("sc")
    bc = "BC" in tok_VABS or flat("bc")

    # ── Combo / momentum ─────────────────────────────────────────────────────
    buy_2809 = "BUY" in tok_Combo or "BUY_2809" in tok_Combo or flat("buy_2809")
    rocket   = "ROCKET" in tok_Combo or "🚀" in tok_Combo or flat("rocket")
    bb_brk   = "BB↑" in tok_Combo or "BB_BRK" in tok_Combo or flat("bb_brk")
    atr_brk  = "ATR↑" in tok_Combo or "ATR_BRK" in tok_Combo or flat("atr_brk")
    hilo_buy  = "HILO↑" in tok_Combo or flat("hilo_buy")
    hilo_sell = "HILO↓" in tok_Combo or flat("hilo_sell")
    rtv = "RTV" in tok_Combo or flat("rtv")
    conso_2809 = ("CON" in tok_Combo or "CONSO" in tok_Combo) or flat("conso_2809")
    um_2809 = "UM" in tok_Combo or flat("um_2809")
    ca = "CA" in tok_Combo or flat("ca")
    cd = "CD" in tok_Combo or flat("cd")
    cw = "CW" in tok_Combo or flat("cw")
    any_f = "ANY F" in tok_Combo or "ANY_F" in tok_Combo or flat("any_f")
    sig3g = "3G" in tok_Combo or "SIG3G" in tok_Combo or flat("sig3g")

    # ── RS / relative strength ───────────────────────────────────────────────
    rs_strong = (
        "RS+" in tok_VABS or "RS+" in tok_Combo or "RS+" in tok_ULT
        or flat("rs_strong")
    )
    rs = (
        "RS" in tok_VABS or "RS" in tok_Combo or "RS" in tok_ULT
        or flat("rs")
    )

    # ── G / GOG ──────────────────────────────────────────────────────────────
    gog_present = bool(tok_G & {"GOG", "G1", "G2", "G3",
                                  "G1C", "G2C", "G3C",
                                  "G1P", "G2P", "G3P",
                                  "G1L", "G2L", "G3L",
                                  "GOG1", "GOG2", "GOG3"}) or flat("gog_sig")

    # ── B / F enumerations ───────────────────────────────────────────────────
    b_flags = {f"b{n}": (f"B{n}" in tok_B) or flat(f"b{n}") for n in range(1, 12)}
    f_flags = {
        f"f{n}": (f"F{n}" in tok_F or f"F{n}" in tok_Combo) or flat(f"f{n}")
        for n in range(1, 12)
    }

    # ── TZ transition ────────────────────────────────────────────────────────
    # Honour an explicit row['tz_bull_flip'] first; otherwise infer from
    # Combo / ULT transition tokens.  T4/T6/T1G alone are NOT a flip.
    tz_bull_flip_explicit = _truthy(row.get("tz_bull_flip"))
    tz_transition_present = bool(
        tok_Combo & {"TZ→3", "TZ→2", "TZ>3", "TZ>2"}
        or tok_ULT   & {"TZ→3", "TZ→2", "TZ>3", "TZ>2"}
    )
    tz_bull_flip = bool(tz_bull_flip_explicit or tz_transition_present)

    return {
        # T/Z passthrough
        "t_signal": t_signal,
        "z_signal": z_signal,
        # L / WLNBB
        "l34": l34, "l43": l43, "l64": l64, "l22": l22,
        "fri34": fri34, "fri43": fri43, "fri64": fri64,
        "blue": blue, "cci_ready": cci_ready,
        # Breakout / Ultra
        "bo_up": bo_up, "bo_dn": bo_dn,
        "bx_up": bx_up, "bx_dn": bx_dn,
        "be_up": be_up, "be_dn": be_dn,
        "eb_bull": eb_bull, "eb_bear": eb_bear,
        "fbo_bull": fbo_bull, "fbo_bear": fbo_bear,
        "ultra_3up": ultra_3up, "ultra_3dn": ultra_3dn,
        "four_bf": four_bf, "sig_260308": sig_260308, "l88": l88,
        # VABS / setup
        "abs_sig": abs_sig, "va": va, "svs_2809": svs_2809,
        "climb_sig": climb_sig, "load_sig": load_sig, "strong_sig": strong_sig,
        "vbo_up": vbo_up, "vbo_dn": vbo_dn,
        "ns": ns, "nd": nd, "sq": sq, "sc": sc, "bc": bc,
        # Combo / momentum
        "buy_2809": buy_2809, "rocket": rocket,
        "bb_brk": bb_brk, "atr_brk": atr_brk,
        "hilo_buy": hilo_buy, "hilo_sell": hilo_sell, "rtv": rtv,
        "conso_2809": conso_2809, "um_2809": um_2809,
        "ca": ca, "cd": cd, "cw": cw, "any_f": any_f, "sig3g": sig3g,
        # RS
        "rs_strong": rs_strong, "rs": rs,
        # G / GOG
        "gog_present": gog_present,
        # B / F enumerations
        **b_flags, **f_flags,
        # TZ transition
        "tz_bull_flip": tz_bull_flip,
        "tz_transition_present": tz_transition_present,
    }


# Source-column documentation used by the parser audit. Order matches
# parse_stock_stat_signals output above.
SOURCE_COLUMNS = {
    "abs_sig":     ("VABS", "abs_sig"),
    "va":          ("Combo", "VABS", "va"),
    "svs_2809":    ("Combo", "VABS", "svs_2809"),
    "climb_sig":   ("VABS", "climb_sig"),
    "load_sig":    ("VABS", "Combo", "load_sig"),
    "strong_sig":  ("VABS", "Combo", "strong_sig"),
    "rs_strong":   ("VABS", "Combo", "ULT", "rs_strong"),
    "bb_brk":      ("Combo", "bb_brk"),
    "bx_up":       ("ULT", "L", "Combo", "bx_up"),
    "eb_bull":     ("ULT", "L", "Combo", "eb_bull"),
    "be_up":       ("ULT", "L", "Combo", "be_up"),
    "bo_up":       ("ULT", "L", "Combo", "bo_up"),
    "buy_2809":    ("Combo", "buy_2809"),
    "rocket":      ("Combo", "rocket"),
    "l34":         ("L", "l34", "l34_active"),
    "fri34":       ("L", "fri34"),
    "tz_bull_flip":("Combo", "ULT", "tz_bull_flip"),
}

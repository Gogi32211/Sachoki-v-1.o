"""
chart_rtb_engine.py — Phase 8I: VERBATIM port of root backend/rtb_engine.py.

RTB v4 (Reversal-To-Breakout Phase Score). Goal: rank stocks moving from
downtrend / dead base → accumulation → first reversal → breakout-ready
state, flagging the 1–3 bars BEFORE the breakout.

Output fields per bar (returned by calc_rtb_v4):
    rtb_build       A-phase score  (cap 12)
    rtb_turn        B-phase score  (cap 14)
    rtb_ready       C-phase score  (cap 12)
    rtb_bonus3      3-bar context  (cap  8)
    rtb_late        D-phase penalty (cap 12)
    rtb_total       max(0, build+turn+ready+bonus3 − late)
    rtb_phase       "0" | "A" | "B" | "C" | "D"
    rtb_transition  A_START | A_HOLD | A_TO_B | B_HOLD | B_TO_C |
                    C_HOLD | C_TO_D | RESET_HARD | RESET_SOFT
    rtb_phase_age   bars the current phase has been running

Hard rule for this port: do NOT change any formula, threshold, branch, or
constant. Only the module docstring + provenance comments differ from the
old file. All helper functions, magic numbers, and decision orders are
copied as-is.

This file replaces the Phase 8G "tier_approximation_v1" stub that derived
rtb_phase from GOG tier — that stub was formally proven wrong by the
Phase 8I audit (see docs/PHASE_8I_ULTRA_TURBO_RTB_AUDIT.md).
"""
from __future__ import annotations

from typing import Any


# ── tiny helpers ─────────────────────────────────────────────────────────────

def _b(row: dict, key: str) -> bool:
    """Bool-cast a signal from the row (0/None/missing → False)."""
    return bool(row.get(key, 0))


def _tz(row: dict) -> str:
    """Return tz_sig string for this bar."""
    return row.get("tz_sig", "") or ""


def _is_w(row: dict) -> bool:
    """Volume-below-lower-BB (W bucket = lowest volume tier)."""
    return row.get("vol_bucket", "") == "W"


def _is_green(row: dict) -> bool:
    """Candle closed above its open."""
    c = float(row.get("close", 0) or 0)
    o = float(row.get("open",  0) or 0)
    return c > o


def _any_sig(history: list[dict], key: str, n: int) -> bool:
    return any(_b(bar, key) for bar in history[:n])


def _any_tz(history: list[dict], codes: set, n: int) -> bool:
    return any(_tz(bar) in codes for bar in history[:n])


def _any_w(history: list[dict], n: int) -> bool:
    return any(_is_w(bar) for bar in history[:n])


# ── Z-code sets used in multiple places ──────────────────────────────────────
_Z3  = {"Z10", "Z11", "Z12"}
_Z4  = {"Z9",  "Z10", "Z11", "Z12"}
_TZ_COIL = {"T1", "T9", "T3", "T11", "T12"}
_TZ_TURN_TOP = {"T1", "T1G", "T9"}

# ── Phase rank for hysteresis distance calculation ───────────────────────────
_PHASE_RANK: dict[str, int] = {"0": 0, "A": 1, "B": 2, "C": 3, "D": 4}

# ── Launch-cluster signals (A→D guard + hysteresis exception) ────────────────
_LAUNCH_CLUSTER_SIGS = frozenset({
    "vbo_up", "bo_up", "bx_up", "bf_buy", "be_up",
    "buy_2809", "rocket", "fly_abcd",
})


# ═════════════════════════════════════════════════════════════════════════════
# A phase — BUILD  (cap 12)
# ═════════════════════════════════════════════════════════════════════════════

def _calc_build(row: dict, history: list[dict]) -> float:
    h3 = history[:3]
    h5 = history[:5]
    tz_cur = _tz(row)

    if _b(row, "conso_2809"):
        base = 5
    elif _b(row, "l64"):
        base = 3
    elif _b(row, "l22"):
        base = 2
    elif _b(row, "pp"):
        base = 1
    else:
        base = 0

    dry = 0.0
    w_cur    = _is_w(row)
    w_recent = _any_w(h3, 3)

    if w_cur:
        dry += 4 if _is_green(row) else 1
    elif w_recent:
        dry += 2

    if _b(row, "ns"):
        dry += 2
    if _b(row, "load_sig"):
        dry += 3

    w_pool_3 = sum(1 for b in h3 if _is_w(b)) + (1 if w_cur else 0)
    w_pool_5 = sum(1 for b in h5 if _is_w(b)) + (1 if w_cur else 0)
    if w_pool_5 >= 3:
        dry += 3
    elif w_pool_3 >= 2:
        dry += 2

    wyck = 0.0
    if _any_sig(h3, "sq", 3) or _any_sig(h3, "d_spring", 3):
        wyck += 3
    if _any_sig(h3, "ns", 3):
        wyck += 3

    cb = 0.0
    if w_recent and _b(row, "conso_2809"):
        cb += 2
    if w_recent and _b(row, "l64"):
        cb += 2
    l22_in5 = _b(row, "l22") or _any_sig(h5, "l22", 5)
    l64_in5 = _b(row, "l64") or _any_sig(h5, "l64", 5)
    if l22_in5 and l64_in5:
        cb += 2

    return min(12.0, base + dry + wyck + cb)


# ═════════════════════════════════════════════════════════════════════════════
# B phase — TURN  (cap 14)
# ═════════════════════════════════════════════════════════════════════════════

def _calc_turn(row: dict, history: list[dict]) -> float:
    h3 = history[:3]
    h5 = history[:5]
    tz_cur = _tz(row)

    _CANDLE = [
        ("f1",          6),
        ("g1",          5),
        ("T1G",         5),
        ("T1",          4),
        ("T9",          4),
        ("T4",          4),
        ("T6",          4),
        ("g2",          4),
        ("T3",          3),
        ("T11",         3),
        ("T12",         3),
        ("T2G",         3),
        ("g11",         3),
        ("T2",          2),
    ]
    turn_candle = 0.0
    for key, w in _CANDLE:
        if key in {"T1G","T1","T9","T3","T11","T12","T2G","T2"}:
            if tz_cur == key:
                turn_candle = w
                break
        elif _b(row, key):
            turn_candle = w
            break

    if _b(row, "l34"):
        reclaim = 5
    elif _b(row, "fri34"):
        reclaim = 4
    elif _b(row, "l43"):
        reclaim = 3
    elif _b(row, "cci_ready"):
        reclaim = 2
    else:
        reclaim = 0

    _clm = _b(row, "climb_sig")
    _sq  = _b(row, "sq")
    _abs = _b(row, "abs_sig")
    _FLOW = [
        (_b(row, "d_spring"),      6),
        (_b(row, "d_absorb_bull"), 6),
        (_b(row, "sig_l88"),       6),
        (_clm,                     6),
        (_sq,                      5),
        (_b(row, "sig_260308"),    5),
        (_b(row, "tz_bull_flip"),  6),
        (_b(row, "tz_attempt"),    4),
        (_abs,                     3),
    ]
    flow = next((w for fired, w in _FLOW if fired), 0)

    complex_t = 0.0
    prev = h3[0] if h3 else {}
    cur_close  = float(row.get("close",  0) or 0)
    prev_high  = float(prev.get("high",  0) or 0)
    prev_close = float(prev.get("close", 0) or 0)

    if _b(prev, "l64") and _is_green(row):
        if prev_high and cur_close > prev_high:
            complex_t = max(complex_t, 5)
        elif prev_close and cur_close > prev_close:
            complex_t = max(complex_t, 3)

    l64_in3 = _b(row, "l64") or _any_sig(h3, "l64", 3)
    if l64_in3 and _b(row, "l34"):
        if _sq or _clm or _abs:
            complex_t = max(complex_t, 6)
        elif _b(row,"f1") or _b(row,"g1") or tz_cur in _TZ_TURN_TOP:
            complex_t = max(complex_t, 5)

    if _any_sig(h5, "l43", 5) and _b(row, "l34"):
        bonus = 3 + (2 if _sq or _clm or _abs else 0)
        complex_t = max(complex_t, bonus)

    z3_in3      = _any_tz(h3, _Z3, 3)
    l22_l64_ctx = (_b(row,"l22") or _b(row,"l64") or
                   _any_sig(h3,"l22",3) or _any_sig(h3,"l64",3))
    if z3_in3 and l22_l64_ctx and _b(row, "f1"):
        complex_t = max(complex_t, 7)

    z4_in5 = _any_tz(h5, _Z4, 5)
    l22_l64_in5 = (_b(row,"l22") or _b(row,"l64") or
                   _any_sig(h5,"l22",5) or _any_sig(h5,"l64",5))
    tz_trap_base = (3 + (2 if l22_l64_in5 else 0)) if z4_in5 else 0

    if tz_trap_base and tz_cur in _TZ_COIL:
        coil = tz_trap_base + 4
        if _b(row,"l22") or _b(row,"l64") or _b(row,"l34"):
            coil += 2
        if _sq or _clm or _abs:
            coil += 3
        complex_t = max(complex_t, coil)
    elif tz_trap_base:
        complex_t = max(complex_t, tz_trap_base)

    return min(14.0, turn_candle + reclaim + flow + complex_t)


# ═════════════════════════════════════════════════════════════════════════════
# C phase — READY  (cap 12)
# ═════════════════════════════════════════════════════════════════════════════

def _calc_ready(row: dict, history: list[dict]) -> float:
    h5 = history[:5]
    tz_cur = _tz(row)

    if _b(row, "svs_2809"):
        rd = 3
    elif _b(row, "um_2809"):
        rd = 2
    elif _b(row, "blue"):
        rd = 2
    else:
        rd = 0

    if _b(row, "x2g_wick"):
        wr = 4
    elif _b(row, "x2_wick"):
        wr = 3
    elif _b(row, "x1g_wick"):
        wr = 3
    elif _b(row, "x1_wick"):
        wr = 2
    elif _b(row, "x3_wick"):
        wr = 1
    else:
        wr = 0
    if _b(row, "wick_bull"):
        wr = max(wr, 3)

    if _b(row, "para_retest"):
        para = 4
    elif _b(row, "para_plus"):
        para = 3
    elif _b(row, "para_start"):
        para = 2
    elif _b(row, "para_prep"):
        para = 1
    else:
        para = 0

    abcd   = _b(row, "fly_abcd")
    fly_cd = _b(row, "fly_cd")
    fly_bd = _b(row, "fly_bd")
    fly_ad = _b(row, "fly_ad")
    if abcd:
        fly_score = 4
    elif fly_cd and fly_bd and fly_ad:
        fly_score = 5
    elif fly_cd or fly_ad:
        fly_score = 4
    elif fly_bd:
        fly_score = 3
    else:
        fly_score = 0

    if _b(row, "sq") or _b(row, "d_spring"):
        wyck_r = 6
    elif _b(row, "ns"):
        wyck_r = 5
    else:
        wyck_r = 0

    _CTX_SIGS  = {"l64","l34","l43","l22","sq","d_spring",
                  "climb_sig","abs_sig","ns","f1","g1"}
    _CTX_TZ    = {"T1","T1G","T9","Z9","Z10","Z11","Z12"}
    _LATE_SIGS = {"vbo_up","bo_up","bx_up","bf_buy","be_up","buy_2809","rocket"}

    def _has_context() -> bool:
        for s in _CTX_SIGS:
            if _b(row, s) or _any_sig(h5, s, 5):
                return True
        if tz_cur in _CTX_TZ:
            return True
        return _any_tz(h5, _CTX_TZ | _Z4, 5)

    t4t6 = 0.0
    if tz_cur in {"T4", "T6"} and sum(1 for s in _LATE_SIGS if _b(row, s)) < 2:
        if _has_context():
            t4t6 = 5
            _ACT = {"l34","sq","climb_sig","sig_260308","tz_bull_flip"}
            if any(_b(row, s) for s in _ACT) or fly_score > 0:
                t4t6 += 3

    return min(12.0, rd + wr + para + fly_score + wyck_r + t4t6)


# ═════════════════════════════════════════════════════════════════════════════
# 3-bar contextual bonus  (cap 8)
# ═════════════════════════════════════════════════════════════════════════════

def _calc_bonus3(row: dict, history: list[dict]) -> float:
    h3 = history[:3]
    h5 = history[:5]
    tz_cur = _tz(row)

    w_cur     = _is_w(row)
    w_rec     = _any_w(h3, 3)
    l64_in3   = _b(row,"l64") or _any_sig(h3,"l64",3)
    conso_in3 = _b(row,"conso_2809") or _any_sig(h3,"conso_2809",3)
    sq_cur    = _b(row,"sq")
    clm_cur   = _b(row,"climb_sig")
    abs_cur   = _b(row,"abs_sig")
    l34_cur   = _b(row,"l34")
    f1_cur    = _b(row,"f1")
    g1_cur    = _b(row,"g1")
    t1_now    = tz_cur in _TZ_TURN_TOP
    p308_cur  = _b(row,"sig_260308")

    dry_rec   = w_cur or w_rec or _b(row,"ns") or _b(row,"load_sig") or l64_in3
    turn_now  = t1_now or f1_cur or g1_cur or l34_cur or clm_cur or sq_cur
    b1 = 4 if dry_rec and turn_now else 0

    ign_now = clm_cur or p308_cur or sq_cur or f1_cur or tz_cur == "T1G" or _b(row,"load_sig")
    b2 = 4 if conso_in3 and ign_now else 0

    reclaim_now = l34_cur or tz_cur in {"T1","T1G"} or f1_cur or g1_cur
    b3 = 5 if l64_in3 and reclaim_now else 0

    spr_in3 = (_any_sig(h3,"sq",3) or _any_sig(h3,"d_spring",3) or
               _any_sig(h3,"abs_sig",3))
    ready_now = tz_cur == "T1G" or f1_cur or g1_cur or l34_cur or p308_cur
    b4 = 5 if spr_in3 and ready_now else 0

    setup_in3 = (clm_cur or p308_cur or l34_cur or f1_cur or
                 _any_sig(h3,"climb_sig",3) or _any_sig(h3,"sig_260308",3) or
                 _any_sig(h3,"l34",3)       or _any_sig(h3,"f1",3))
    broken_out = any(_b(row,s) for s in {"vbo_up","bf_buy","bo_up","bx_up","be_up"})
    b5 = 4 if setup_in3 and not broken_out else 0

    z_in5     = _any_tz(h5, _Z4, 5)
    coil_now  = tz_cur in _TZ_COIL
    struct_now= (_b(row,"l22") or _b(row,"l64") or l34_cur or
                 sq_cur or clm_cur or abs_cur)
    b6 = 4 if z_in5 and coil_now and struct_now else 0

    return min(8.0, b1 + b2 + b3 + b4 + b5 + b6)


# ═════════════════════════════════════════════════════════════════════════════
# D phase — LATE penalty  (cap 12)
# ═════════════════════════════════════════════════════════════════════════════

def _calc_late(row: dict) -> float:
    brk = 0.0
    if _b(row,"vbo_up"):    brk += 4
    if _b(row,"bo_up"):     brk += 4
    if _b(row,"bx_up"):     brk += 4
    if _b(row,"bf_buy"):    brk += 4
    if _b(row,"fbo_bull"):  brk += 3
    if _b(row,"hilo_buy"):  brk += 3
    if _b(row,"be_up"):     brk += 6
    if _b(row,"buy_2809"):  brk += 6
    if _b(row,"rocket"):    brk += 6
    if _b(row,"eb_bull"):   brk += 2
    if _b(row,"fly_abcd"):  brk += 3

    bear = 0.0
    if _b(row,"nd"):        bear += 3
    if _b(row,"vbo_dn"):    bear += 4
    if _b(row,"bo_dn"):     bear += 4
    if _b(row,"bx_dn"):     bear += 4
    if _b(row,"fbo_bear"):  bear += 4
    if _b(row,"be_dn"):     bear += 5

    return min(12.0, brk + bear)


# ═════════════════════════════════════════════════════════════════════════════
# Phase + transition classification
# ═════════════════════════════════════════════════════════════════════════════

def _phase(build: float, turn: float, ready: float, late: float,
           total: float = 0.0) -> str:
    if late >= 5 and (turn >= 6 or ready >= 5 or total >= 18):
        return "D"
    if build >= 5 and turn >= 6 and ready >= 4 and late <= 6:
        return "C"
    if build >= 5 and turn >= 6 and late <= 6:
        return "B"
    if build >= 5 and turn < 6:
        return "A"
    return "0"


def _phase_no_d(build: float, turn: float, ready: float, late: float) -> str:
    if build >= 5 and turn >= 6 and ready >= 4 and late <= 6:
        return "C"
    if build >= 5 and turn >= 6 and late <= 6:
        return "B"
    if build >= 5:
        return "A"
    return "0"


_HARD_RESET_KEYS = {"vbo_dn","bo_dn","bx_dn","fbo_bear","be_dn"}


def _transition(prev: str, cur: str, hard: bool, soft: bool) -> str:
    if hard:           return "RESET_HARD"
    if soft:           return "RESET_SOFT"
    if cur == "0":     return "0"
    if prev == "0" and cur == "A":  return "A_START"
    if prev == cur:    return f"{cur}_HOLD"
    return f"{prev}_TO_{cur}"


# ═════════════════════════════════════════════════════════════════════════════
# Main entry point
# ═════════════════════════════════════════════════════════════════════════════

def calc_rtb_v4(
    row: dict,
    history: list[dict],
    prev_phase: str = "0",
    prev_phase_age: int = 0,
    soft_streak: int = 0,
    pending_phase: str = "",
    pending_phase_count: int = 0,
) -> dict:
    """Compute RTB v4 scores for one bar. See module docstring for outputs."""
    build  = _calc_build(row, history)
    turn   = _calc_turn(row, history)
    ready  = _calc_ready(row, history)
    bonus3 = _calc_bonus3(row, history)
    late   = _calc_late(row)
    total  = max(0.0, build + turn + ready + bonus3 - late)

    ph_raw = _phase(build, turn, ready, late, total)

    hard = any(_b(row, k) for k in _HARD_RESET_KEYS)
    if hard:
        ph_raw = "0"

    new_streak = (soft_streak + 1) if (build < 4 and turn < 4 and ready < 4) else 0
    soft = new_streak >= 3
    if soft:
        ph_raw = "0"
        new_streak = 0

    launch_count = sum(1 for s in _LAUNCH_CLUSTER_SIGS if _b(row, s))
    strong_launch = launch_count >= 3

    if not hard and not soft and prev_phase == "A" and ph_raw == "D" and launch_count < 2:
        ph_raw = _phase_no_d(build, turn, ready, late)

    if not hard and not soft and prev_phase == "D" and ph_raw == "A" and late >= 3:
        ph_raw = "D"

    new_pending_phase = ""
    new_pending_count = 0

    if not hard and not soft and not strong_launch:
        prev_rank = _PHASE_RANK.get(prev_phase, 0)
        new_rank  = _PHASE_RANK.get(ph_raw, 0)
        dist = abs(new_rank - prev_rank)
        if dist >= 2:
            if pending_phase == ph_raw:
                confirmed = pending_phase_count + 1
                if confirmed >= 2:
                    ph = ph_raw
                else:
                    ph = prev_phase
                    new_pending_phase = ph_raw
                    new_pending_count = confirmed
            else:
                ph = prev_phase
                new_pending_phase = ph_raw
                new_pending_count = 1
        else:
            ph = ph_raw
    else:
        ph = ph_raw

    tr  = _transition(prev_phase, ph, hard, soft)
    age = (prev_phase_age + 1) if ph == prev_phase else 1

    h5     = history[:5]
    tz_cur = _tz(row)
    _CTX_SIGS_D = {"l64","l34","l43","l22","sq","d_spring",
                   "climb_sig","abs_sig","ns","f1","g1"}
    _CTX_TZ_D   = {"T1","T1G","T9","Z9","Z10","Z11","Z12"}
    _LATE_SIGS_D = {"vbo_up","bo_up","bx_up","bf_buy","be_up","buy_2809","rocket"}
    _ACT_D       = {"l34","sq","climb_sig","sig_260308","tz_bull_flip"}

    def _dbg_has_ctx() -> bool:
        for s in _CTX_SIGS_D:
            if _b(row, s) or _any_sig(h5, s, 5):
                return True
        if tz_cur in _CTX_TZ_D:
            return True
        return _any_tz(h5, _CTX_TZ_D | _Z4, 5)

    _late_count = sum(1 for s in _LATE_SIGS_D if _b(row, s))
    no_live = _late_count < 2
    dbg_ctx = _dbg_has_ctx()
    dbg_t4_ctx  = (tz_cur == "T4") and no_live and dbg_ctx
    dbg_t6_ctx  = (tz_cur == "T6") and no_live and dbg_ctx
    dbg_t4t6_ap = (
        tz_cur in {"T4", "T6"} and no_live and dbg_ctx and
        (any(_b(row, s) for s in _ACT_D) or
         any(_b(row, s) for s in {"fly_cd","fly_bd","fly_ad","fly_abcd"}))
    )

    return {
        "rtb_build":      round(build,  1),
        "rtb_turn":       round(turn,   1),
        "rtb_ready":      round(ready,  1),
        "rtb_bonus3":     round(bonus3, 1),
        "rtb_late":       round(late,   1),
        "rtb_total":      round(total,  1),
        "rtb_phase":      ph,
        "rtb_transition": tr,
        "rtb_phase_age":  age,
        # Carry-forward state
        "_soft_streak":         new_streak,
        "_pending_phase":       new_pending_phase,
        "_pending_phase_count": new_pending_count,
        # Debug
        "dbg_context_ready":        dbg_ctx,
        "dbg_t4_ctx":               dbg_t4_ctx,
        "dbg_t6_ctx":               dbg_t6_ctx,
        "dbg_t4t6_activation_plus": dbg_t4t6_ap,
        "dbg_launch_cluster_count": launch_count,
        "dbg_pending_phase":        new_pending_phase,
        "dbg_pending_phase_count":  new_pending_count,
    }


__all__ = ["calc_rtb_v4"]

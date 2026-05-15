"""
rtb_engine.py — RTB v4  (Reversal-To-Breakout Phase Score)

Goal: rank stocks moving from downtrend / dead base → accumulation → first
reversal → breakout-ready state, flagging the 1-3 bars BEFORE the breakout.

Output fields per bar
─────────────────────
  rtb_build       A-phase score  (cap 12)
  rtb_turn        B-phase score  (cap 14)
  rtb_ready       C-phase score  (cap 12)
  rtb_bonus3      3-bar context  (cap  8)
  rtb_late        D-phase penalty (cap 12)
  rtb_total       max(0, build+turn+ready+bonus3−late)
  rtb_phase       "0" | "A" | "B" | "C" | "D"
  rtb_transition  A_START | A_HOLD | A_TO_B | B_HOLD | B_TO_C |
                  C_HOLD | C_TO_D | RESET_HARD | RESET_SOFT
  rtb_phase_age   bars the current phase has been running

Signal key mapping (turbo row → RTB spec name)
────────────────────────────────────────────────
  conso_2809   → CONSO/CONS      l64        → L64
  l22          → L22             l34        → L34
  fri34        → FRI34           l43        → L43
  cci_ready    → CCI_READY       abs_sig    → ABS
  climb_sig    → CLM             load_sig   → LD
  ns           → NS              sq         → SQ / SPR-current
  d_spring     → dSPR            d_absorb_bull → d_absorb_bull
  sig_l88      → L88             sig_260308 → 260308
  tz_bull_flip → TZ→3            tz_attempt → TZ→2
  svs_2809     → SVS             um_2809    → UM
  blue         → BLUE            vol_bucket → W-dot
  tz_sig       → T1/T1G/T2/…/T4/T6/Z9/Z10/Z11/Z12
  x2g_wick … x3_wick → X2G…X3    wick_bull  → WK↑
  para_retest  → RETEST          para_plus  → PARA+
  para_start   → PARA            para_prep  → PREP
  fly_abcd     → ABCD            fly_cd/bd/ad → CD/BD/AD
  vbo_up/dn    → VBO↑↓           bo_up/dn   → BO↑↓
  bx_up/dn     → BX↑↓            bf_buy     → 4BF
  fbo_bull/bear→ FBO↑↓           hilo_buy   → HILO↑
  be_up/dn     → BE↑↓            buy_2809   → BUY
  rocket       → ROCKET          eb_bull    → EB↑
  nd           → ND
"""

from __future__ import annotations
from typing import Any


# ── tiny helpers ──────────────────────────────────────────────────────────────

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
    """True if signal fired in any of the last *n* bars (history[0] = prev bar)."""
    return any(_b(bar, key) for bar in history[:n])


def _any_tz(history: list[dict], codes: set, n: int) -> bool:
    """True if tz_sig matched any code in last n bars."""
    return any(_tz(bar) in codes for bar in history[:n])


def _any_w(history: list[dict], n: int) -> bool:
    """True if W-dot appeared in any of the last n bars."""
    return any(_is_w(bar) for bar in history[:n])


# ── Z-code sets used in multiple places ──────────────────────────────────────
_Z3  = {"Z10", "Z11", "Z12"}
_Z4  = {"Z9",  "Z10", "Z11", "Z12"}
_TZ_COIL = {"T1", "T9", "T3", "T11", "T12"}
_TZ_TURN_TOP = {"T1", "T1G", "T9"}

# ── Phase rank for hysteresis distance calculation ────────────────────────────
_PHASE_RANK: dict[str, int] = {"0": 0, "A": 1, "B": 2, "C": 3, "D": 4}

# ── Launch-cluster signals (used for A→D guard and hysteresis exception) ──────
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

    # ── Base structure: use strongest single signal ───────────────────────────
    if _b(row, "conso_2809"):
        base = 5
    elif _b(row, "l64"):
        base = 3
    elif _b(row, "l22"):
        base = 2
    elif _b(row, "pp"):        # pivot-point signal (optional; not in all rows)
        base = 1
    else:
        base = 0

    # ── Dryness / supply shrink ───────────────────────────────────────────────
    dry = 0.0
    w_cur    = _is_w(row)
    w_recent = _any_w(h3, 3)   # W in last 3 bars (not current)

    if w_cur:
        dry += 4 if _is_green(row) else 1
    elif w_recent:
        dry += 2                # W lingered recently = orange-box context

    if _b(row, "ns"):
        dry += 2
    if _b(row, "load_sig"):    # LD = LOAD
        dry += 3

    # W repetition: count W across current + last N bars
    w_pool_3 = sum(1 for b in h3 if _is_w(b)) + (1 if w_cur else 0)
    w_pool_5 = sum(1 for b in h5 if _is_w(b)) + (1 if w_cur else 0)
    if w_pool_5 >= 3:
        dry += 3
    elif w_pool_3 >= 2:
        dry += 2

    # ── Wyckoff build context (SPR / LPS in last 3 bars) ─────────────────────
    # SPR ≈ sq (Spring Quality: narrow bar + high vol = stopping action)
    # LPS ≈ ns (No Supply: narrow bar + low vol = quiet support test)
    wyck = 0.0
    if _any_sig(h3, "sq", 3) or _any_sig(h3, "d_spring", 3):
        wyck += 3              # SPR in last 3 bars
    if _any_sig(h3, "ns", 3):
        wyck += 3              # LPS in last 3 bars

    # ── Build combo bonuses ───────────────────────────────────────────────────
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

    # ── Turn candle family: use strongest ─────────────────────────────────────
    _CANDLE = [
        ("f1",          6),
        ("g1",          5),
        ("T1G",         5),   # tz_sig checks follow
        ("T1",          4),
        ("T9",          4),
        ("T4",          4),   # pre-breakout trigger — contributes to turn like T1/T9
        ("T6",          4),   # pre-breakout trigger
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

    # ── Reclaim / support flip: use strongest ────────────────────────────────
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

    # ── Flow / stop-action / real turn: use strongest ────────────────────────
    # TZ→3 = tz_bull_flip (full bull phase transition)
    # TZ→2 = tz_attempt   (partial / early attempt)
    _clm = _b(row, "climb_sig")
    _sq  = _b(row, "sq")
    _abs = _b(row, "abs_sig")
    _FLOW = [
        (_b(row, "d_spring"),      6),
        (_b(row, "d_absorb_bull"), 6),
        (_b(row, "sig_l88"),       6),
        (_clm,                     6),   # CLM = CLIMB
        (_sq,                      5),
        (_b(row, "sig_260308"),    5),
        (_b(row, "tz_bull_flip"),  6),   # TZ→3
        (_b(row, "tz_attempt"),    4),   # TZ→2
        (_abs,                     3),
    ]
    flow = next((w for fired, w in _FLOW if fired), 0)

    # ── Complex B-phase rules ─────────────────────────────────────────────────
    complex_t = 0.0
    prev = h3[0] if h3 else {}
    cur_close  = float(row.get("close",  0) or 0)
    prev_high  = float(prev.get("high",  0) or 0)
    prev_close = float(prev.get("close", 0) or 0)

    # L64_RECLAIM_STRICT / SOFT
    if _b(prev, "l64") and _is_green(row):
        if prev_high and cur_close > prev_high:
            complex_t = max(complex_t, 5)          # close > high[1]
        elif prev_close and cur_close > prev_close:
            complex_t = max(complex_t, 3)          # close > close[1] only

    # L64_L34_ACTIVATION / L64_L34_TURN
    l64_in3 = _b(row, "l64") or _any_sig(h3, "l64", 3)
    if l64_in3 and _b(row, "l34"):
        if _sq or _clm or _abs:
            complex_t = max(complex_t, 6)          # L64_L34_ACTIVATION
        elif _b(row,"f1") or _b(row,"g1") or tz_cur in _TZ_TURN_TOP:
            complex_t = max(complex_t, 5)          # L64_L34_TURN

    # L43_TO_L34
    if _any_sig(h5, "l43", 5) and _b(row, "l34"):
        bonus = 3 + (2 if _sq or _clm or _abs else 0)
        complex_t = max(complex_t, bonus)

    # F1_TRAP_RECOVERY
    z3_in3      = _any_tz(h3, _Z3, 3)
    l22_l64_ctx = (_b(row,"l22") or _b(row,"l64") or
                   _any_sig(h3,"l22",3) or _any_sig(h3,"l64",3))
    if z3_in3 and l22_l64_ctx and _b(row, "f1"):
        complex_t = max(complex_t, 7)

    # TZ_TRAP_BASE (Z9/Z10/Z11/Z12 in last 5 bars)
    z4_in5 = _any_tz(h5, _Z4, 5)
    l22_l64_in5 = (_b(row,"l22") or _b(row,"l64") or
                   _any_sig(h5,"l22",5) or _any_sig(h5,"l64",5))
    tz_trap_base = (3 + (2 if l22_l64_in5 else 0)) if z4_in5 else 0

    # TZ_RECOVERY_COIL
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

    # ── Ready drive: use strongest ────────────────────────────────────────────
    if _b(row, "svs_2809"):
        rd = 3
    elif _b(row, "um_2809"):
        rd = 2
    elif _b(row, "blue"):
        rd = 2
    else:
        rd = 0

    # ── Wick readiness: use strongest ─────────────────────────────────────────
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

    # ── PARA block: use strongest ─────────────────────────────────────────────
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

    # ── FLY block ─────────────────────────────────────────────────────────────
    # ABCD = +4 context (but also late marker → adds to rtb_late too)
    # CD/BD/AD without ABCD = PRE_FLY_READY
    abcd   = _b(row, "fly_abcd")
    fly_cd = _b(row, "fly_cd")
    fly_bd = _b(row, "fly_bd")
    fly_ad = _b(row, "fly_ad")
    if abcd:
        fly_score = 4          # context bonus; late marker handled in _calc_late
    elif fly_cd and fly_bd and fly_ad:
        fly_score = 5          # abcFound: all sub-patterns converging
    elif fly_cd or fly_ad:
        fly_score = 4          # cdFound or adFound
    elif fly_bd:
        fly_score = 3          # bdFound
    else:
        fly_score = 0

    # ── Wyckoff ready: use strongest ─────────────────────────────────────────
    # SPR current = sq / d_spring (+6), LPS current = ns (+5)
    if _b(row, "sq") or _b(row, "d_spring"):
        wyck_r = 6
    elif _b(row, "ns"):
        wyck_r = 5
    else:
        wyck_r = 0

    # ── T4/T6 context-ready rules ─────────────────────────────────────────────
    # T4/T6 are valid C-phase core triggers only when context already exists.
    # context_ready: any of these in current bar OR last 5 bars
    _CTX_SIGS  = {"l64","l34","l43","l22","sq","d_spring",
                  "climb_sig","abs_sig","ns","f1","g1"}
    _CTX_TZ    = {"T1","T1G","T9","Z9","Z10","Z11","Z12"}
    # launch/live cluster signals that push T4/T6 to late side instead
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
            t4t6 = 5                       # T4/T6_CONTEXT_READY (+5, was +6)
            _ACT = {"l34","sq","climb_sig","sig_260308","tz_bull_flip"}
            if any(_b(row, s) for s in _ACT) or fly_score > 0:
                t4t6 += 3                  # T4T6_ACTIVATION_PLUS

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

    # Bonus 1 — dry to turn
    dry_rec   = w_cur or w_rec or _b(row,"ns") or _b(row,"load_sig") or l64_in3
    turn_now  = t1_now or f1_cur or g1_cur or l34_cur or clm_cur or sq_cur
    b1 = 4 if dry_rec and turn_now else 0

    # Bonus 2 — base to ignition
    ign_now = clm_cur or p308_cur or sq_cur or f1_cur or tz_cur == "T1G" or _b(row,"load_sig")
    b2 = 4 if conso_in3 and ign_now else 0

    # Bonus 3 — support test to reclaim
    reclaim_now = l34_cur or tz_cur in {"T1","T1G"} or f1_cur or g1_cur
    b3 = 5 if l64_in3 and reclaim_now else 0

    # Bonus 4 — spring / stop-action to ready
    spr_in3 = (_any_sig(h3,"sq",3) or _any_sig(h3,"d_spring",3) or
               _any_sig(h3,"abs_sig",3))
    ready_now = tz_cur == "T1G" or f1_cur or g1_cur or l34_cur or p308_cur
    b4 = 5 if spr_in3 and ready_now else 0

    # Bonus 5 — setup building, breakout not yet live
    setup_in3 = (clm_cur or p308_cur or l34_cur or f1_cur or
                 _any_sig(h3,"climb_sig",3) or _any_sig(h3,"sig_260308",3) or
                 _any_sig(h3,"l34",3)       or _any_sig(h3,"f1",3))
    broken_out = any(_b(row,s) for s in {"vbo_up","bf_buy","bo_up","bx_up","be_up"})
    b5 = 4 if setup_in3 and not broken_out else 0

    # Bonus 6 — advanced trap recovery
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
    # Breakout / expansion penalty
    brk = 0.0
    if _b(row,"vbo_up"):    brk += 4
    if _b(row,"bo_up"):     brk += 4
    if _b(row,"bx_up"):     brk += 4
    if _b(row,"bf_buy"):    brk += 4   # 4BF
    if _b(row,"fbo_bull"):  brk += 3
    if _b(row,"hilo_buy"):  brk += 3
    if _b(row,"be_up"):     brk += 6
    if _b(row,"buy_2809"):  brk += 6
    if _b(row,"rocket"):    brk += 6
    if _b(row,"eb_bull"):   brk += 2
    if _b(row,"fly_abcd"):  brk += 3   # ABCD late marker

    # Bearish conflict penalty
    bear = 0.0
    if _b(row,"nd"):        bear += 3
    if _b(row,"vbo_dn"):    bear += 4
    if _b(row,"bo_dn"):     bear += 4
    if _b(row,"bx_dn"):     bear += 4
    if _b(row,"fbo_bear"):  bear += 4
    if _b(row,"be_dn"):     bear += 5
    # utad / lpsy / sow: add when those signals are in the system
    # if _b(row,"utad"):    bear += 5
    # if _b(row,"lpsy"):    bear += 5
    # if _b(row,"sow"):     bear += 6

    return min(12.0, brk + bear)


# ═════════════════════════════════════════════════════════════════════════════
# Phase + transition classification
# ═════════════════════════════════════════════════════════════════════════════

def _phase(build: float, turn: float, ready: float, late: float,
           total: float = 0.0) -> str:
    # D: late penalty alone is NOT enough — must also have strong turn/ready/total
    if late >= 5 and (turn >= 6 or ready >= 5 or total >= 18):
        return "D"
    # C: ready threshold lowered 5→4; late ceiling raised 4→6
    if build >= 5 and turn >= 6 and ready >= 4 and late <= 6:
        return "C"
    # B: same entry criteria as before; late ceiling aligned with C
    if build >= 5 and turn >= 6 and late <= 6:
        return "B"
    if build >= 5 and turn < 6:
        return "A"
    return "0"


def _phase_no_d(build: float, turn: float, ready: float, late: float) -> str:
    """Best non-D classification — used when A→D direct jump is blocked."""
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
    """
    Compute RTB v4 scores for one bar.

    Parameters
    ──────────
    row                  Current bar signal dict (turbo row format).
    history              Recent bars most-recent-first (history[0] = prev bar).
                         Pass at least 5 bars for full rule coverage.
    prev_phase           Phase returned for the immediately preceding bar.
    prev_phase_age       Consecutive bars the previous phase has run.
    soft_streak          Carry-forward counter for soft-reset logic.
    pending_phase        Phase awaiting hysteresis confirmation.
    pending_phase_count  How many consecutive bars this pending phase has held.

    Returns
    ───────
    Dict with all output fields plus carry-forward state keys prefixed '_'.
    """
    build  = _calc_build(row, history)
    turn   = _calc_turn(row, history)
    ready  = _calc_ready(row, history)
    bonus3 = _calc_bonus3(row, history)
    late   = _calc_late(row)
    total  = max(0.0, build + turn + ready + bonus3 - late)

    ph_raw = _phase(build, turn, ready, late, total)

    # ── Hard reset ─────────────────────────────────────────────────────────
    hard = any(_b(row, k) for k in _HARD_RESET_KEYS)
    if hard:
        ph_raw = "0"

    # ── Soft reset ─────────────────────────────────────────────────────────
    new_streak = (soft_streak + 1) if (build < 4 and turn < 4 and ready < 4) else 0
    soft = new_streak >= 3
    if soft:
        ph_raw = "0"
        new_streak = 0

    # ── Launch cluster count (shared by A→D guard + hysteresis exception) ─
    launch_count = sum(1 for s in _LAUNCH_CLUSTER_SIGS if _b(row, s))
    strong_launch = launch_count >= 3   # ≥3 signals → bypass hysteresis

    # ── A→D guard: requires ≥2 launch signals ──────────────────────────────
    # Example: T4 fires, late=6 from one VBO but turn>=6 qualifies D.
    #          Without a second launch signal, this is NOT a real breakout
    #          from accumulation — stay at C/B/A instead.
    if not hard and not soft and prev_phase == "A" and ph_raw == "D" and launch_count < 2:
        ph_raw = _phase_no_d(build, turn, ready, late)

    # ── D→A guard: require late < 3 before recovering toward A ─────────────
    # If signals are dying but late is still elevated, stock is not yet
    # clear — hold at D until late pressure truly subsides.
    if not hard and not soft and prev_phase == "D" and ph_raw == "A" and late >= 3:
        ph_raw = "D"

    # ── Hysteresis: large phase jumps need 2 consecutive bars ──────────────
    # Covered transitions (|rank_new − rank_prev| ≥ 2):
    #   A→D (3), D→A (3), D→B (2), 0→C (3), 0→D (4) …
    # Exception: strong launch cluster (≥3 signals) confirms immediately.
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
                    ph = ph_raw            # confirmed — commit the jump
                else:
                    ph = prev_phase        # still waiting
                    new_pending_phase = ph_raw
                    new_pending_count = confirmed
            else:
                ph = prev_phase            # new candidate — wait one more bar
                new_pending_phase = ph_raw
                new_pending_count = 1
        else:
            ph = ph_raw                    # normal small step — no delay
    else:
        ph = ph_raw                        # hard/soft/strong-launch bypass

    # ── Transition label + age ─────────────────────────────────────────────
    tr  = _transition(prev_phase, ph, hard, soft)
    age = (prev_phase_age + 1) if ph == prev_phase else 1

    # ── Debug / context fields ─────────────────────────────────────────────
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
        # ── Primary outputs ──────────────────────────────────────────────
        "rtb_build":      round(build,  1),
        "rtb_turn":       round(turn,   1),
        "rtb_ready":      round(ready,  1),
        "rtb_bonus3":     round(bonus3, 1),
        "rtb_late":       round(late,   1),
        "rtb_total":      round(total,  1),
        "rtb_phase":      ph,
        "rtb_transition": tr,
        "rtb_phase_age":  age,
        # ── Carry-forward state ──────────────────────────────────────────
        "_soft_streak":         new_streak,
        "_pending_phase":       new_pending_phase,
        "_pending_phase_count": new_pending_count,
        # ── Debug fields ─────────────────────────────────────────────────
        "dbg_context_ready":        dbg_ctx,
        "dbg_t4_ctx":               dbg_t4_ctx,
        "dbg_t6_ctx":               dbg_t6_ctx,
        "dbg_t4t6_activation_plus": dbg_t4t6_ap,
        "dbg_launch_cluster_count": launch_count,
        "dbg_pending_phase":        new_pending_phase,
        "dbg_pending_phase_count":  new_pending_count,
    }


# ═════════════════════════════════════════════════════════════════════════════
# Unit-test style smoke tests  (python rtb_engine.py)
# ═════════════════════════════════════════════════════════════════════════════

def _test():
    def check(label, result, expect_phase, min_total=None):
        ph = result["rtb_phase"]
        tot = result["rtb_total"]
        ok_ph  = ph == expect_phase
        ok_tot = min_total is None or tot >= min_total
        status = "PASS" if (ok_ph and ok_tot) else "FAIL"
        print(f"  [{status}] {label}: phase={ph} total={tot:.1f} "
              f"(build={result['rtb_build']} turn={result['rtb_turn']} "
              f"ready={result['rtb_ready']} late={result['rtb_late']})")
        return status == "PASS"

    passed = 0
    total  = 0

    print("── RTB v4 smoke tests ───────────────────────────────────────────")

    # 1. Pure A-phase: conso + W dot, no turn yet
    r = calc_rtb_v4(
        {"conso_2809":1, "vol_bucket":"W", "close":10, "open":9},
        history=[], prev_phase="0"
    )
    total += 1; passed += check("A-phase basic", r, "A")

    # 2. A→B transition: conso + W + T1G turn candle + L34 reclaim
    prev_row = {"conso_2809":1, "vol_bucket":"W"}
    r = calc_rtb_v4(
        {"conso_2809":1, "tz_sig":"T1G", "l34":1, "climb_sig":1,
         "vol_bucket":"W", "close":12, "open":10},
        history=[prev_row, prev_row], prev_phase="A"
    )
    total += 1; passed += check("A→B transition (T1G+L34+CLM)", r, "B", min_total=12)

    # 3. B→C: add wick + para context
    hist = [{"conso_2809":1,"tz_sig":"T1G","l34":1,"climb_sig":1,"vol_bucket":"W"}]*5
    r = calc_rtb_v4(
        {"conso_2809":1, "tz_sig":"T1G", "l34":1, "climb_sig":1,
         "x2g_wick":1, "para_retest":1, "svs_2809":1, "vol_bucket":"L"},
        history=hist, prev_phase="B"
    )
    total += 1; passed += check("B→C (wick+para+SVS)", r, "C", min_total=16)

    # 4. C→D: breakout fires (rocket)
    r = calc_rtb_v4(
        {"conso_2809":1, "tz_sig":"T1G", "l34":1, "climb_sig":1,
         "x2g_wick":1, "rocket":1},
        history=hist, prev_phase="C"
    )
    total += 1; passed += check("C→D (rocket late)", r, "D")

    # 5. Hard reset: VBO↓ kills phase
    r = calc_rtb_v4(
        {"conso_2809":1, "tz_sig":"T1G", "l34":1, "vbo_dn":1},
        history=hist, prev_phase="C"
    )
    total += 1; passed += check("Hard reset (vbo_dn)", r, "0")

    # 6. L64 reclaim strict: prev bar L64, close > high[1]
    prev = {"l64":1, "high":10.0, "close":9.5}
    r = calc_rtb_v4(
        {"conso_2809":1, "l34":1, "close":10.8, "open":10.1,
         "vol_bucket":"L", "tz_sig":""},
        history=[prev, prev, prev], prev_phase="A"
    )
    total += 1; passed += check("L64 reclaim strict (close>high[1])", r, "B")

    # 7. F1 trap recovery: Z10 in last 3 bars + L64 + F1
    hist_z = [{"tz_sig":"Z10","l64":1}]*3
    r = calc_rtb_v4(
        {"f1":1, "conso_2809":1, "close":11, "open":10, "vol_bucket":"L"},
        history=hist_z, prev_phase="A"
    )
    total += 1; passed += check("F1 trap recovery", r, "B", min_total=10)

    # 8. T4 context-ready trigger (C-phase)
    hist_ctx = [{"l34":1,"l64":1,"climb_sig":1}]*5
    r = calc_rtb_v4(
        {"tz_sig":"T4", "conso_2809":1, "l34":1, "climb_sig":1,
         "x1_wick":1, "vol_bucket":"N"},
        history=hist_ctx, prev_phase="B"
    )
    total += 1; passed += check("T4 context-ready (→C)", r, "C")

    # 9. A→D blocked: only rocket (launch_count=1 < 2) → must NOT be D
    r = calc_rtb_v4(
        {"conso_2809":1, "tz_sig":"T1G", "l34":1, "climb_sig":1, "rocket":1},
        history=[], prev_phase="A"
    )
    total += 1
    ok = r["rtb_phase"] != "D"
    print(f"  [{'PASS' if ok else 'FAIL'}] A→D blocked (rocket only): "
          f"phase={r['rtb_phase']} total={r['rtb_total']:.1f}")
    passed += int(ok)

    # 10. A→D allowed: rocket + be_up (launch_count=2 ≥ 2) → D after 2 bars
    #     Hysteresis (dist=3) still requires 2 consecutive bars unless ≥3 signals.
    row_ad = {"conso_2809":1, "tz_sig":"T1G", "l34":1, "climb_sig":1,
              "rocket":1, "be_up":1}
    r10a = calc_rtb_v4(row_ad, history=[], prev_phase="A")
    r10b = calc_rtb_v4(row_ad, history=[], prev_phase="A",
                       pending_phase=r10a["_pending_phase"],
                       pending_phase_count=r10a["_pending_phase_count"])
    total += 1; passed += check("A→D allowed after 2 bars (rocket+be_up)", r10b, "D")

    # 11. D tighten: late=6 but turn<6 AND ready<5 AND total<18 → NOT D
    r = calc_rtb_v4(
        {"conso_2809":1, "be_up":1},          # be_up=6 → late=6; build only, no turn
        history=[], prev_phase="B"
    )
    total += 1
    ok = r["rtb_phase"] != "D"
    print(f"  [{'PASS' if ok else 'FAIL'}] D tighten (late=6, weak turn/ready): "
          f"phase={r['rtb_phase']} total={r['rtb_total']:.1f} late={r['rtb_late']:.1f}")
    passed += int(ok)

    # 12. Hysteresis: 0→C needs 2 bars
    strong_c = {"conso_2809":1,"tz_sig":"T1G","l34":1,"climb_sig":1,
                "x2g_wick":1,"svs_2809":1,"vol_bucket":"N"}
    r1 = calc_rtb_v4(strong_c, history=[], prev_phase="0")
    # bar 1: should stay at "0" (pending C, count=1)
    ok1 = r1["rtb_phase"] == "0"
    r2  = calc_rtb_v4(strong_c, history=[], prev_phase="0",
                      pending_phase=r1["_pending_phase"],
                      pending_phase_count=r1["_pending_phase_count"])
    # bar 2: should confirm C
    ok2 = r2["rtb_phase"] == "C"
    ok = ok1 and ok2
    print(f"  [{'PASS' if ok else 'FAIL'}] Hysteresis 0→C (bar1={r1['rtb_phase']}, bar2={r2['rtb_phase']})")
    total += 1; passed += int(ok)

    # 13. D→A guard: late=4 blocks recovery to A (stays D)
    r = calc_rtb_v4(
        {"conso_2809":1, "vol_bucket":"W"},   # build=9, turn=0 → raw phase=A; late=0<3 ok
        history=[], prev_phase="D"
    )
    # raw phase = A (build=9, turn=0), late=0 < 3 → D→A NOT blocked by guard
    # but hysteresis (dist=3) should hold at D bar-1
    ok = r["rtb_phase"] == "D"
    print(f"  [{'PASS' if ok else 'FAIL'}] D→A hysteresis bar1 stays D (phase={r['rtb_phase']} pending={r['_pending_phase']})")
    total += 1; passed += int(ok)

    # 14. Strong launch bypasses hysteresis: 0→D with ≥3 signals → immediate
    r = calc_rtb_v4(
        {"conso_2809":1,"tz_sig":"T1G","l34":1,"climb_sig":1,
         "rocket":1,"be_up":1,"vbo_up":1},   # launch_count=3 → strong_launch
        history=[], prev_phase="0"
    )
    total += 1; passed += check("Strong launch bypasses hysteresis (0→D)", r, "D")

    print(f"\n  {passed}/{total} tests passed")
    return passed == total


if __name__ == "__main__":
    _test()

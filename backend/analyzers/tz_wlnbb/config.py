"""TZ/WLNBB Analyzer — version and parameters."""
import re as _re

TZ_WLNBB_VERSION = "260506_TZ_F_WLNBB_CMB_python_v1"

DEFAULT_LOOKBACK_TRADING_DAYS = 320    # ~320 trading days ≈ 1.28 calendar years
OUTPUT_SCHEMA_VERSION = "2"

SEQUENCE_FAMILIES = [
    "T_to_T", "T_to_Z", "Z_to_T", "Z_to_Z",
    "Z_to_L", "L_to_T", "L_to_Z", "L_to_L",
    "PREUP_after_Z", "PREUP_after_L", "PREDN_after_T", "PREDN_after_L",
]


# Dynamic L combos: any sequence of digits 1-6 after "L", e.g. L12, L34, L46, L234
_L_DYNAMIC_RE = _re.compile(r'^L[1-6]+$')


def is_known_l_signal(sig: str) -> bool:
    """True for any valid L signal including dynamic digit combos like L12, L346."""
    return bool(_L_DYNAMIC_RE.match(sig))


def signal_family(sig: str) -> str:
    """Determine signal family from signal name."""
    if not sig: return ""
    if sig.startswith("T"): return "T"
    if sig.startswith("Z"): return "Z"
    if sig.startswith("L"): return "L"
    if sig.startswith("P"): return "PREUP"
    if sig.startswith("D"): return "PREDN"
    return ""


def sequence_family(prev_sig: str, curr_sig: str) -> str:
    """Determine the canonical sequence family name."""
    pf = signal_family(prev_sig)
    cf = signal_family(curr_sig)
    if not pf or not cf: return ""
    # For PREUP/PREDN we track: Z→PREUP (prev=Z, curr=PREUP) → PREUP_after_Z
    canonical = {
        ("Z", "PREUP"):  "PREUP_after_Z",
        ("L", "PREUP"):  "PREUP_after_L",
        ("T", "PREDN"):  "PREDN_after_T",
        ("L", "PREDN"):  "PREDN_after_L",
    }
    return canonical.get((pf, cf), f"{pf}_to_{cf}")

# Engulfing defaults
USE_WICK = False
MIN_BODY_RATIO = 1.0

# Doji threshold (body/range)
DOJI_THRESH = 0.05

# WLNBB Bollinger Band period
WLNBB_MA_PERIOD = 20

# T priority order (highest priority first)
T_PRIORITY = ["T4", "T6", "T1G", "T2G", "T1", "T2", "T9", "T10", "T3", "T11", "T5", "T12"]
T_PRIORITY_RANK = {s: i+1 for i, s in enumerate(T_PRIORITY)}

# Z priority order
Z_PRIORITY = ["Z4", "Z6", "Z1G", "Z2G", "Z1", "Z2", "Z9", "Z10", "Z3", "Z11", "Z5", "Z12", "Z8", "Z7"]
Z_PRIORITY_RANK = {s: i+1 for i, s in enumerate(Z_PRIORITY)}

# PREUP priority
PREUP_PRIORITY = ["P66", "P55", "P89", "P3", "P2", "P50"]
# PREDN priority
PREDN_PRIORITY = ["D66", "D55", "D89", "D3", "D2", "D50"]

# Known signals registry
KNOWN_T_SIGNALS = set(T_PRIORITY)  # includes T12
KNOWN_Z_SIGNALS = set(Z_PRIORITY)
KNOWN_L_SIGNALS = {"L1","L2","L3","L4","L5","L6","L34","L43","L64","L22"}
KNOWN_PREUP_SIGNALS = set(PREUP_PRIORITY)
KNOWN_PREDN_SIGNALS = set(PREDN_PRIORITY)
ALL_KNOWN_SIGNALS = KNOWN_T_SIGNALS | KNOWN_Z_SIGNALS | KNOWN_L_SIGNALS | KNOWN_PREUP_SIGNALS | KNOWN_PREDN_SIGNALS

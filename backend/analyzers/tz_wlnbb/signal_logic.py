"""TZ/WLNBB signal logic — Pine Script to Python conversion."""
from __future__ import annotations
from .config import T_PRIORITY_RANK, Z_PRIORITY_RANK


def compute_tz_wlnbb_for_bar(
    o: float, h: float, l: float, c: float, v: float,
    prev_o: float, prev_h: float, prev_l: float, prev_c: float, prev_v: float,
    ema9: float, ema20: float, ema34: float, ema50: float, ema89: float, ema200: float,
    vol_mid: float, vol_up: float, vol_low: float,
    prev_vol_mid: float, prev_vol_up: float, prev_vol_low: float,
    prev_is_doji: bool = False,
    use_wick: bool = False,
    min_body_ratio: float = 1.0,
    doji_thresh: float = 0.05,
) -> dict:
    """
    Compute all TZ/WLNBB signals for a single bar.

    Returns a dict with all signal fields.
    """

    # ── DOJI ──────────────────────────────────────────────────────────────────
    bar_range = h - l
    body_size = abs(c - o)
    # Pine 260506: isDoji = close == open (exact equality)
    is_doji = (c == o)

    # ── CANDLE DIRECTION ───────────────────────────────────────────────────────
    is_bull = c > o
    is_bear = c < o
    prev1_is_bull = prev_c > prev_o
    # Pine: prev1_is_bear includes doji (Z7_raw[1] = prev_is_doji)
    prev1_is_bear = prev_c < prev_o or prev_is_doji

    # ── ENGULFING ──────────────────────────────────────────────────────────────
    prev_body = abs(prev_c - prev_o)
    prev_top  = max(prev_o, prev_c)
    prev_bot  = min(prev_o, prev_c)
    curr_body = abs(c - o)
    curr_top  = max(o, c)
    curr_bot  = min(o, c)

    e_high = h        if use_wick else curr_top
    e_low  = l        if use_wick else curr_bot
    e_ph   = prev_h   if use_wick else prev_top
    e_pl   = prev_l   if use_wick else prev_bot

    prev_body_safe = max(prev_body, 1e-10)
    body_ratio_ok  = (curr_body / prev_body_safe) >= min_body_ratio
    fully_engulfs  = e_high >= e_ph and e_pl >= e_low and body_ratio_ok
    is_inside      = curr_top <= prev_top and curr_bot >= prev_bot

    # ── T RAWS ────────────────────────────────────────────────────────────────
    T1G_raw = prev1_is_bear and (o > prev_c) and (o > prev_o) and (c > prev_o) and is_bull
    T1_raw  = prev1_is_bear and (o >= prev_c) and (prev_o >= o) and (c > prev_o) and is_bull
    T2G_raw = prev1_is_bull and (o >= prev_o) and (o > prev_c) and (c > prev_c) and is_bull
    T2_raw  = prev1_is_bull and (o >= prev_o) and (o <= prev_c) and (c > prev_c) and is_bull
    T3_raw  = prev1_is_bear and is_bull and (o < prev_o) and (o < prev_c) and (c < prev_o) and (c > prev_c)
    T4_raw  = prev1_is_bear and is_bull and fully_engulfs
    T5_raw  = prev1_is_bear and is_bull and (o < prev_o) and (o < prev_c) and (c < prev_o) and (prev_c >= c)
    T6_raw  = prev1_is_bull and is_bull and fully_engulfs
    T9_raw  = prev1_is_bear and is_bull and is_inside
    T10_raw = prev1_is_bull and is_bull and is_inside
    # Pine 260506: T12 defined before T11 so T11 can exclude it
    T12_raw = prev1_is_bull and is_bull and (o < prev_o) and (c < prev_o)
    T11_raw = prev1_is_bull and (o < prev_o) and (c < prev_c or c < prev_o) and is_bull and not T12_raw

    # ── Z RAWS ────────────────────────────────────────────────────────────────
    Z1G_raw  = prev1_is_bull and (o < prev_c) and (o < prev_o) and (c < prev_o) and is_bear
    Z1_raw   = prev1_is_bull and (o <= prev_c) and (o > prev_o) and (c < prev_o) and is_bear
    Z2G_raw  = prev1_is_bear and (o <= prev_o) and (o < prev_c) and (c < prev_c) and is_bear
    Z2_raw   = prev1_is_bear and (o <= prev_o) and (o >= prev_c) and (c < prev_c) and is_bear
    Z3_raw   = prev1_is_bull and is_bear and (o > prev_o) and (o > prev_c) and (c > prev_o) and (c < prev_c)
    Z4_raw   = prev1_is_bull and is_bear and fully_engulfs
    Z5_raw   = prev1_is_bull and is_bear and (o > prev_o) and (o > prev_c) and (c > prev_o) and (c >= prev_c)
    Z6_raw   = prev1_is_bear and is_bear and fully_engulfs
    Z9_raw   = prev1_is_bull and is_bear and is_inside
    Z10_raw  = prev1_is_bear and is_bear and is_inside
    Z11_raw  = prev1_is_bear and (o > prev_o) and is_bear and (c > prev_c or c > prev_o)
    Z12_raw  = prev1_is_bull and (o <= prev_o) and (c < o)
    Z8_base  = prev1_is_bull and (o > prev_c) and is_bear and (c >= prev_o)

    # Collect base Z raws (without Z8 and Z7)
    base_z_raws = {
        "Z1G": Z1G_raw, "Z1": Z1_raw, "Z2G": Z2G_raw, "Z2": Z2_raw,
        "Z3": Z3_raw, "Z4": Z4_raw, "Z5": Z5_raw, "Z6": Z6_raw,
        "Z9": Z9_raw, "Z10": Z10_raw, "Z11": Z11_raw, "Z12": Z12_raw,
    }
    any_base_z = any(base_z_raws.values())

    # Z8 only fires if no other Z fires
    Z8_raw = Z8_base and not any_base_z

    # Collect base T raws (used to block Z7; includes T12 per Pine 260506)
    base_t_raws = {
        "T1G": T1G_raw, "T1": T1_raw, "T2G": T2G_raw, "T2": T2_raw,
        "T3": T3_raw, "T4": T4_raw, "T5": T5_raw, "T6": T6_raw,
        "T9": T9_raw, "T10": T10_raw, "T11": T11_raw, "T12": T12_raw,
    }
    any_base_t = any(base_t_raws.values())

    # Z7 only fires if no T and no Z fires (including Z8); doji = exact close==open
    Z7_raw = is_doji and not any_base_t and not any_base_z and not Z8_raw

    # Build final raw sets
    t_raw: set = {k for k, v in base_t_raws.items() if v}
    z_raw: set = {k for k, v in base_z_raws.items() if v}
    if Z8_raw:
        z_raw.add("Z8")
    if Z7_raw:
        z_raw.add("Z7")

    # ── PRIORITY ENGINE ────────────────────────────────────────────────────────
    t_signal = ""
    bull_priority_code = 0
    for sig in ["T4", "T6", "T1G", "T2G", "T1", "T2", "T9", "T10", "T3", "T11", "T5", "T12"]:
        if sig in t_raw:
            t_signal = sig
            bull_priority_code = T_PRIORITY_RANK[sig]
            break

    z_signal = ""
    bear_priority_code = 0
    for sig in ["Z4", "Z6", "Z1G", "Z2G", "Z1", "Z2", "Z9", "Z10", "Z3", "Z11", "Z5", "Z12", "Z8", "Z7"]:
        if sig in z_raw:
            z_signal = sig
            bear_priority_code = Z_PRIORITY_RANK[sig]
            break

    has_t_signal = bool(t_signal)
    has_z_signal = bool(z_signal)

    # ── PREUP / PREDN ─────────────────────────────────────────────────────────
    cross_ema9   = o < ema9   and c > ema9
    cross_ema20  = o < ema20  and c > ema20
    cross_ema34  = o < ema34  and c > ema34
    cross_ema50  = o < ema50  and c > ema50
    cross_ema89  = o < ema89  and c > ema89
    cross_ema200 = o < ema200 and c > ema200

    raw_p66 = cross_ema200 and (cross_ema9 or cross_ema20 or cross_ema34 or cross_ema50 or cross_ema89)
    raw_p55 = cross_ema89  and (cross_ema9 or cross_ema20 or cross_ema34 or cross_ema50 or cross_ema200)
    raw_p3  = cross_ema9 and cross_ema20 and cross_ema50
    raw_p2  = cross_ema9 and cross_ema20
    raw_p50 = cross_ema50

    preup_signal = ""
    preup_raw: set = set()
    if raw_p66:   preup_raw.add("P66")
    if raw_p55:   preup_raw.add("P55")
    if cross_ema89: preup_raw.add("P89")
    if raw_p3:    preup_raw.add("P3")
    if raw_p2:    preup_raw.add("P2")
    if raw_p50:   preup_raw.add("P50")

    if raw_p66:           preup_signal = "P66"
    elif raw_p55:         preup_signal = "P55"
    elif cross_ema89:     preup_signal = "P89"
    elif raw_p3:          preup_signal = "P3"
    elif raw_p2:          preup_signal = "P2"
    elif raw_p50:         preup_signal = "P50"

    drop_ema9   = o > ema9   and c < ema9
    drop_ema20  = o > ema20  and c < ema20
    drop_ema34  = o > ema34  and c < ema34
    drop_ema50  = o > ema50  and c < ema50
    drop_ema89  = o > ema89  and c < ema89
    drop_ema200 = o > ema200 and c < ema200

    raw_d66 = drop_ema200 and (drop_ema9 or drop_ema20 or drop_ema34 or drop_ema50 or drop_ema89)
    raw_d55 = drop_ema89  and (drop_ema9 or drop_ema20 or drop_ema34 or drop_ema50 or drop_ema200)
    raw_d3  = drop_ema9 and drop_ema20 and drop_ema50
    raw_d2  = drop_ema9 and drop_ema20
    raw_d50 = drop_ema50

    predn_signal = ""
    predn_raw: set = set()
    if raw_d66:   predn_raw.add("D66")
    if raw_d55:   predn_raw.add("D55")
    if drop_ema89: predn_raw.add("D89")
    if raw_d3:    predn_raw.add("D3")
    if raw_d2:    predn_raw.add("D2")
    if raw_d50:   predn_raw.add("D50")

    if raw_d66:           predn_signal = "D66"
    elif raw_d55:         predn_signal = "D55"
    elif drop_ema89:      predn_signal = "D89"
    elif raw_d3:          predn_signal = "D3"
    elif raw_d2:          predn_signal = "D2"
    elif raw_d50:         predn_signal = "D50"

    has_preup = bool(preup_signal)
    has_predn = bool(predn_signal)

    # ── WLNBB VOLUME BUCKETS ──────────────────────────────────────────────────
    is_W  = v < vol_low
    is_L  = not is_W and v < vol_mid
    is_N  = not is_W and not is_L and v < vol_up
    is_B  = not is_W and not is_L and not is_N and v < (vol_up + vol_mid)
    is_VB = not is_W and not is_L and not is_N and not is_B

    if is_W:
        volume_bucket = "W"
    elif is_L:
        volume_bucket = "L"
    elif is_N:
        volume_bucket = "N"
    elif is_B:
        volume_bucket = "B"
    else:
        volume_bucket = "VB"

    prev_is_W  = prev_v < prev_vol_low
    prev_is_L  = not prev_is_W and prev_v < prev_vol_mid
    prev_is_N  = not prev_is_W and not prev_is_L and prev_v < prev_vol_up
    prev_is_B  = not prev_is_W and not prev_is_L and not prev_is_N and prev_v < (prev_vol_up + prev_vol_mid)
    prev_is_VB = not prev_is_W and not prev_is_L and not prev_is_N and not prev_is_B

    same_bucket = (
        (is_W and prev_is_W) or (is_L and prev_is_L) or
        (is_N and prev_is_N) or (is_B and prev_is_B) or
        (is_VB and prev_is_VB)
    )
    vol_up_raw = v > prev_v
    vol_down   = v < prev_v

    dn_VB = prev_is_VB and (is_B or is_N or is_L or is_W)
    dn_B  = prev_is_B  and (is_N or is_L or is_W)
    dn_N  = prev_is_N  and (is_L or is_W)
    dn_L  = prev_is_L  and is_W
    bucket_down = dn_VB or dn_B or dn_N or dn_L

    up_W = prev_is_W and (is_L or is_N or is_B or is_VB)
    up_L = prev_is_L and (is_N or is_B or is_VB)
    up_N = prev_is_N and (is_B or is_VB)
    up_B = prev_is_B and is_VB
    bucket_up = up_W or up_L or up_N or up_B

    vol_down_adapted = bucket_down or (same_bucket and vol_down)
    vol_up_adapted   = bucket_up   or (same_bucket and vol_up_raw)

    # ── L SIGNALS ─────────────────────────────────────────────────────────────
    up_close   = c > prev_c
    down_close = c < prev_c
    no_new_low_by_close  = c >= prev_l
    no_new_high_by_close = c <= prev_h

    l1_raw = vol_down_adapted and up_close
    l2_raw = vol_down_adapted and no_new_low_by_close
    l3_raw = vol_up_adapted   and up_close
    l4_raw = vol_up_adapted   and no_new_high_by_close
    l5_raw = vol_down_adapted and down_close
    l6_raw = vol_up_adapted   and down_close

    l34_active = l3_raw and l4_raw and (c >= o)
    l64_active = l6_raw and l4_raw and (c < o)
    l43_active = l6_raw and l4_raw and (c > o)
    l22_active = l3_raw and l4_raw and (c < o)

    l_digits = ""
    for i, active in [(1, l1_raw), (2, l2_raw), (3, l3_raw), (4, l4_raw), (5, l5_raw), (6, l6_raw)]:
        if active:
            l_digits += str(i)
    l_signal = ("L" + l_digits) if l_digits else ""
    has_l_signal = bool(l_signal)

    # ── NE SUFFIX ─────────────────────────────────────────────────────────────
    # E if close exceeds prev bar's high or low
    ne_suffix = "E" if (c > prev_h or c < prev_l) else "N"

    # ── WICK SUFFIX ───────────────────────────────────────────────────────────
    wick_up   = h > prev_h
    wick_down = l < prev_l
    if wick_up and wick_down:
        wick_suffix = "B"
    elif wick_up:
        wick_suffix = "U"
    elif wick_down:
        wick_suffix = "D"
    else:
        wick_suffix = ""

    wick_ext_up   = wick_up
    wick_ext_down = wick_down
    wick_ext_both = wick_up and wick_down

    # ── PENETRATION SUFFIX ────────────────────────────────────────────────────
    prev_body_top = max(prev_o, prev_c)
    prev_body_bot = min(prev_o, prev_c)

    wick_penetration_upper = (h >= prev_body_top) and (h <= prev_h)
    wick_penetration_lower = (l <= prev_body_bot) and (l >= prev_l)
    wick_penetration_both  = wick_penetration_upper and wick_penetration_lower

    if wick_penetration_both:
        penetration_suffix = "H"
    elif wick_penetration_upper:
        penetration_suffix = "P"
    elif wick_penetration_lower:
        penetration_suffix = "R"
    else:
        penetration_suffix = ""

    # ── COMBINED LABELS ────────────────────────────────────────────────────────
    # Pine logic:
    # lane1Core = hasTsig ? tBase + lPart : (not hasZsig ? lPart : "")
    # lane3Core = hasZsig ? zBase + lPart : ""
    t_base = t_signal
    z_base = z_signal
    l_part = l_signal  # may be empty

    suffix = ne_suffix + wick_suffix + penetration_suffix

    if has_t_signal:
        lane1_core = t_base + l_part
    elif not has_z_signal:
        lane1_core = l_part
    else:
        lane1_core = ""

    lane3_core = (z_base + l_part) if has_z_signal else ""

    lane1_label = (lane1_core + suffix) if lane1_core else ""
    lane3_label = (lane3_core + suffix) if lane3_core else ""

    # ── COMPOSITE STATE LABELS ────────────────────────────────────────────────
    # T composite: T + L + full suffix
    if has_t_signal:
        composite_t_core  = t_signal + l_signal
        composite_t_label = composite_t_core + suffix
    else:
        composite_t_core  = ""
        composite_t_label = ""

    # Z composite: Z + L + full suffix
    if has_z_signal:
        composite_z_core  = z_signal + l_signal
        composite_z_label = composite_z_core + suffix
    else:
        composite_z_core  = ""
        composite_z_label = ""

    # L-only composite (no T/Z)
    if has_l_signal and not has_t_signal and not has_z_signal:
        composite_l_core  = l_signal
        composite_l_label = composite_l_core + suffix
    else:
        composite_l_core  = ""
        composite_l_label = ""

    # Primary composite (T > Z > L)
    if composite_t_label:
        composite_primary_label = composite_t_label
        composite_core          = composite_t_core
    elif composite_z_label:
        composite_primary_label = composite_z_label
        composite_core          = composite_z_core
    elif composite_l_label:
        composite_primary_label = composite_l_label
        composite_core          = composite_l_core
    else:
        composite_primary_label = ""
        composite_core          = ""

    # All composite labels on this bar (T and Z can both exist)
    _all_c = [x for x in [composite_t_label, composite_z_label, composite_l_label] if x]
    composite_all_labels  = "|".join(_all_c)
    composite_full_label  = composite_primary_label
    composite_full_suffix = suffix  # ne + wick_ext + penetration

    # ── CONTEXT BOOLEANS ──────────────────────────────────────────────────────
    has_tz_l_combo      = (has_t_signal or has_z_signal) and has_l_signal
    has_bullish_context = has_t_signal or has_preup
    has_bearish_context = has_z_signal or has_predn

    return {
        "is_bull": is_bull,
        "is_bear": is_bear,
        "is_doji": is_doji,
        "t_raw": t_raw,
        "z_raw": z_raw,
        "t_signal": t_signal,
        "z_signal": z_signal,
        "bull_priority_code": bull_priority_code,
        "bear_priority_code": bear_priority_code,
        "preup_signal": preup_signal,
        "predn_signal": predn_signal,
        "preup_raw": preup_raw,
        "predn_raw": predn_raw,
        "volume_bucket": volume_bucket,
        "vol_down_adapted": vol_down_adapted,
        "vol_up_adapted": vol_up_adapted,
        "l1_raw": l1_raw,
        "l2_raw": l2_raw,
        "l3_raw": l3_raw,
        "l4_raw": l4_raw,
        "l5_raw": l5_raw,
        "l6_raw": l6_raw,
        "l34_active": l34_active,
        "l43_active": l43_active,
        "l64_active": l64_active,
        "l22_active": l22_active,
        "l_digits": l_digits,
        "l_signal": l_signal,
        "ne_suffix": ne_suffix,
        "wick_suffix": wick_suffix,
        "penetration_suffix": penetration_suffix,
        "wick_penetration_upper": wick_penetration_upper,
        "wick_penetration_lower": wick_penetration_lower,
        "wick_penetration_both": wick_penetration_both,
        "wick_ext_up":   wick_ext_up,
        "wick_ext_down": wick_ext_down,
        "wick_ext_both": wick_ext_both,
        "prev_body_top": prev_body_top,
        "prev_body_bot": prev_body_bot,
        "prev_high":     prev_h,
        "prev_low":      prev_l,
        "composite_t_label":       composite_t_label,
        "composite_z_label":       composite_z_label,
        "composite_primary_label": composite_primary_label,
        "composite_all_labels":    composite_all_labels,
        "composite_core":          composite_core,
        "composite_suffix":        suffix,
        "composite_full_suffix":   composite_full_suffix,
        "composite_full_label":    composite_full_label,
        "lane1_label": lane1_label,
        "lane3_label": lane3_label,
        "has_t_signal": has_t_signal,
        "has_z_signal": has_z_signal,
        "has_l_signal": has_l_signal,
        "has_preup": has_preup,
        "has_predn": has_predn,
        "has_tz_l_combo": has_tz_l_combo,
        "has_bullish_context": has_bullish_context,
        "has_bearish_context": has_bearish_context,
    }

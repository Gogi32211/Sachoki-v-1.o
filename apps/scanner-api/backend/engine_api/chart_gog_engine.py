"""
chart_gog_engine.py — Phase 8G commit 7: port of root backend/gog_engine.py.

GOG Priority Engine + Internal F8 — Setup (A/SM/N/MX), GOG_TIER
(G1P/G2P/G3P/G1L/G2L/G3L/G1C/G2C/G3C/GOG1/GOG2/GOG3) and Context
(LD/LDS/LDC/LDP/LRC/LRP/WRC/F8C/SQB/BCT/SVS).

Verbatim formula port. The function signature accepts the outputs of the
other engines (wlnbb / sig / f / vabs / ultra260 / ultraV2 / combo) so
nothing in this file imports old root backend modules.

compute_forward_stats from the old file is NOT ported here — that helper is
for backtest research, not live scanner output.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ── Parameters ───────────────────────────────────────────────────────────────
_GOG_LOOKBACK         = 5
_LOAD_LOOKBACK        = 10
_LDP_LOOKBACK         = 10
_WRC_LOOKBACK         = 10
_CONTEXT_COOLDOWN     = 2
_BOTTOM_LOOKBACK      = 10
_HARD_BOTTOM_LOOKBACK = 14
_SUPPORT_LOOKBACK     = 18
_ABS_LOOKBACK         = 12
_IGNITION_LOOKBACK    = 6
_SEQ_LOOKBACK         = 24
_COOLDOWN_BARS        = 4
_RSI_LENGTH           = 14
_RSI_COMPARE_BARS     = 2
_USE_RSI_FILTER       = True
_LATE_BREAK_MULT      = 2.80


# ── Helpers ──────────────────────────────────────────────────────────────────

def _barssince(ser):
    b = ser.astype(bool).to_numpy()
    n = len(b)
    positions = np.arange(n, dtype=float)
    last_true = np.where(b, positions, -np.inf)
    cummax = np.maximum.accumulate(last_true)
    result = np.where(np.isinf(cummax), np.nan, positions - cummax)
    return pd.Series(result, index=ser.index)


def _f_happened(cond, n):
    return cond.astype(bool).rolling(n, min_periods=1).max().astype(bool)


def _f_stepOk(older, newer, n):
    bs_o = _barssince(older)
    bs_n = _barssince(newer)
    return (bs_o.notna() & bs_n.notna() & (bs_o <= n) & (bs_n <= n) & (bs_o >= bs_n))


def _cooldown(ser, cd):
    if cd <= 0:
        return ser.astype(bool)
    b = ser.astype(bool).to_numpy().copy()
    last_fire = -cd - 1
    for i in range(len(b)):
        if b[i]:
            if i - last_fire <= cd:
                b[i] = False
            else:
                last_fire = i
    return pd.Series(b, index=ser.index)


def _rsi(close, length=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=length - 1, min_periods=length).mean()
    avg_loss = loss.ewm(com=length - 1, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _sv(frame, col, idx):
    if frame is None or frame.empty or col not in frame.columns:
        return pd.Series(False, index=idx)
    return frame[col].fillna(0).astype(bool).reindex(idx, fill_value=False)


# ── Main computation ─────────────────────────────────────────────────────────

def compute_gog_signals(df, wlnbb, sig_df, f_sigs, vabs, ultra260, ultraV2, combo_df):
    if df is None or df.empty:
        return pd.DataFrame()

    idx = df.index
    n = len(idx)

    seq = _SEQ_LOOKBACK
    sup = _SUPPORT_LOOKBACK
    abs_lb = _ABS_LOOKBACK
    gog_lb = _GOG_LOOKBACK
    load_lb = _LOAD_LOOKBACK
    ldp_lb = _LDP_LOOKBACK
    wrc_lb = _WRC_LOOKBACK
    cd_ctx = _CONTEXT_COOLDOWN

    def _safe(frame):
        if frame is None:
            return pd.DataFrame()
        return frame

    wlnbb    = _safe(wlnbb)
    sig_df   = _safe(sig_df)
    f_sigs   = _safe(f_sigs)
    vabs     = _safe(vabs)
    ultra260 = _safe(ultra260)
    ultraV2  = _safe(ultraV2)
    combo_df = _safe(combo_df)

    def _sig(name):
        if sig_df.empty or 'sig_name' not in sig_df.columns:
            return pd.Series(False, index=idx)
        raw = (sig_df['sig_name'] == name)
        return raw.reindex(idx, fill_value=False).astype(bool)

    T6, T4, T1G, T2G, T1, T2, T3 = (_sig(x) for x in ('T6','T4','T1G','T2G','T1','T2','T3'))
    T9, T10, T11, T12, T5        = (_sig(x) for x in ('T9','T10','T11','T12','T5'))
    Z4, Z6, Z1G, Z2G, Z1, Z2, Z3 = (_sig(x) for x in ('Z4','Z6','Z1G','Z2G','Z1','Z2','Z3'))
    Z9, Z10, Z11, Z12, Z5, Z7    = (_sig(x) for x in ('Z9','Z10','Z11','Z12','Z5','Z7'))

    F3, F4, F6, F11 = (_sv(f_sigs, c, idx) for c in ('f3','f4','f6','f11'))

    VBO_UP   = _sv(vabs, 'vbo_up',    idx)
    LOAD     = _sv(vabs, 'load_sig',  idx)
    SQ       = _sv(vabs, 'sq',        idx)
    NS       = _sv(vabs, 'ns',        idx)
    ABS_SIG  = _sv(vabs, 'abs_sig',   idx)
    CLM_SIG  = _sv(vabs, 'climb_sig', idx)

    L34   = _sv(wlnbb, 'L34',  idx)
    L43   = _sv(wlnbb, 'L43',  idx)
    L64   = _sv(wlnbb, 'L64',  idx)
    L22   = _sv(wlnbb, 'L22',  idx)
    L555  = _sv(wlnbb, 'L555', idx)
    BO_UP = _sv(wlnbb, 'BO_UP', idx)
    BX_UP = _sv(wlnbb, 'BX_UP', idx)
    BE_UP = _sv(wlnbb, 'BE_UP', idx)

    SIG_260308 = _sv(ultra260, 'sig_260308', idx)
    L88        = _sv(ultra260, 'sig_l88',    idx)

    BF4      = _sv(ultraV2, 'bf_buy',   idx)
    FBO_BULL = _sv(ultraV2, 'fbo_bull', idx)
    EB_BULL  = _sv(ultraV2, 'eb_bull',  idx)

    BUY_HERE      = _sv(combo_df, 'buy_2809',  idx)
    THREE_G       = _sv(combo_df, 'sig3g',     idx)
    BB_BRK        = _sv(combo_df, 'bb_brk',    idx)
    ATR_BRK       = _sv(combo_df, 'atr_brk',   idx)
    ROCKET        = _sv(combo_df, 'rocket',    idx)
    RTV           = _sv(combo_df, 'rtv',       idx)
    HILO_BUY      = _sv(combo_df, 'hilo_buy',  idx)
    UM            = _sv(combo_df, 'um_2809',   idx)
    SVS_RAW_COMBO = _sv(combo_df, 'svs_2809',  idx)
    CONS          = _sv(combo_df, 'cons_atr',  idx)

    rsi = _rsi(df['close'], _RSI_LENGTH)
    rsi_pass = (rsi > rsi.shift(_RSI_COMPARE_BARS, fill_value=0)) if _USE_RSI_FILTER else pd.Series(True, index=idx)

    Z11_1 = Z11.shift(1, fill_value=False); Z11_2 = Z11.shift(2, fill_value=False)
    Z10_1 = Z10.shift(1, fill_value=False); Z10_2 = Z10.shift(2, fill_value=False)
    T10_1 = T10.shift(1, fill_value=False); T10_2 = T10.shift(2, fill_value=False)
    T11_1 = T11.shift(1, fill_value=False)
    T12_1 = T12.shift(1, fill_value=False); T12_2 = T12.shift(2, fill_value=False)

    F8_raw = (
        (Z11_2 & Z11) | (Z10_2 & Z10) | (Z11_1 & Z11) | (Z10_1 & Z11) |
        (Z10_1 & Z10) | (Z11_1 & Z10) |
        (T10_1 & T10) | (T10_2 & T10) | (T11_1 & T10) |
        ((T12_1 | T12_2) & T10) | (T11_1 & T11) |
        ((T12_1 | T12_2) & T11) | (T10_1 & T12) | (T12_1 & T12)
    )
    F8 = F8_raw & rsi_pass

    vol_avg20 = df['volume'].rolling(20, min_periods=1).mean()
    vol_ratio = df['volume'] / vol_avg20.replace(0, np.nan)
    SVS = (
        (vol_ratio > 1.4) & (vol_ratio.shift(1, fill_value=0) <= 1.4)
        & (df['close'] > df['open'])
    )

    if 'vol_bucket' in wlnbb.columns:
        isW = (wlnbb['vol_bucket'].reindex(idx, fill_value='') == 'W')
    else:
        vol_mid = df['volume'].rolling(20, min_periods=1).mean()
        vol_std = df['volume'].rolling(20, min_periods=1).std().fillna(0)
        isW = df['volume'] < (vol_mid - vol_std).fillna(0)

    bullBar = df['close'] > df['open']

    zStep = Z1G | Z2G | Z3 | Z5 | Z9 | Z10 | Z11 | Z12 | Z7
    supportStep = L64 | L43 | L22 | L34 | L555
    absStep = SQ | NS | ABS_SIG | CLM_SIG | LOAD | VBO_UP
    tStep = T1 | T1G | T2 | T2G | T3 | T4 | T6 | T10 | T11 | T12 | F3 | F6 | F4 | F8 | F11
    finalStep = (
        VBO_UP | BO_UP | BX_UP | BE_UP | BUY_HERE | SIG_260308 | L88
        | F3 | F6 | F8 | BB_BRK | ATR_BRK | THREE_G | ROCKET
    )

    zToL       = _f_stepOk(zStep,       supportStep, seq)
    lToAbs     = _f_stepOk(supportStep, absStep,     seq)
    absToT     = _f_stepOk(absStep,     tStep,       seq)
    tToFinal   = _f_stepOk(tStep,       finalStep,   seq)

    fullSequence       = zToL & lToAbs & absToT & tToFinal
    supportAbsSequence = lToAbs & absToT
    preFinalSequence   = (_f_happened(supportStep, sup) & _f_happened(absStep, abs_lb) & tStep)
    resetSequence      = (_f_happened(supportStep, seq) & _f_happened(absStep, seq) & _f_happened(tStep, seq))

    recentSupport = _f_happened(supportStep, sup)
    recentAbs     = _f_happened(absStep, abs_lb)

    priorRangeHigh = df['high'].rolling(20, min_periods=1).max().shift(1, fill_value=0)
    lateCloseBreak = df['close'] > priorRangeHigh * _LATE_BREAK_MULT

    comboStrongNow = BUY_HERE | BB_BRK | ATR_BRK | THREE_G | ROCKET | SVS
    strBstContext  = comboStrongNow | SVS | SIG_260308 | L88

    preTurnStructure = (
        bullBar
        | (df['close'] > df['close'].shift(1, fill_value=0))
        | T6 | F3 | F6 | F8 | VBO_UP | BE_UP | BO_UP
        | BUY_HERE | SIG_260308 | comboStrongNow
    )

    smxRealTrigger = T6 | F3 | F6 | VBO_UP | BE_UP | BO_UP
    smxEarlyTrigger = (T1 | T1G | T4 | T2 | T2G) & (
        LOAD | VBO_UP | BE_UP | F3 | F6
        | _f_happened(LOAD | VBO_UP | BE_UP, 3)
    )
    smxCurrentTrigger = smxRealTrigger | smxEarlyTrigger
    smxContextOk = recentSupport & (recentAbs | VBO_UP | LOAD | SQ | NS | ABS_SIG)
    smxStructureOk = (
        fullSequence | supportAbsSequence | preFinalSequence | resetSequence
        | (smxContextOk & smxCurrentTrigger)
    )
    smxRaw = smxStructureOk & smxCurrentTrigger & preTurnStructure & ~lateCloseBreak
    SM = _cooldown(smxRaw, _COOLDOWN_BARS)

    priorLocalHigh = df['high'].rolling(10, min_periods=1).max().shift(1, fill_value=0)
    akanFinalStrict = (
        VBO_UP | T6 | F3 | F6
        | ((T4 | T2G | T2) & finalStep)
        | BE_UP | BO_UP | BUY_HERE | SIG_260308
    )
    akanNearLocal = (
        ((priorLocalHigh - df['close']) / df['close'].replace(0, np.nan)).fillna(0) * 100.0 <= 30.0
    )
    akanPressure = (
        akanNearLocal | (df['close'] > priorLocalHigh)
        | (df['close'] > df['high'].shift(1, fill_value=0))
        | VBO_UP | BO_UP | BE_UP
    )
    akanStructureOk = fullSequence | supportAbsSequence | preFinalSequence
    akanRaw = (akanStructureOk & akanFinalStrict & finalStep & akanPressure
               & preTurnStructure & ~lateCloseBreak)
    A = _cooldown(akanRaw, _COOLDOWN_BARS)

    hardBottomZ      = Z4 | Z6 | Z9 | Z10 | Z11 | Z12
    lateBottomZ      = Z10 | Z11 | Z12
    bottomT          = T10 | T11 | T12
    recentHardBottomZ = _f_happened(hardBottomZ, _HARD_BOTTOM_LOOKBACK)
    recentCompression = recentHardBottomZ | _f_happened(lateBottomZ | bottomT | F8, _BOTTOM_LOOKBACK)

    absorptionContext     = recentSupport & recentAbs
    softAbsorptionContext = (recentAbs | recentSupport | _f_happened(SQ | NS | LOAD | ABS_SIG, abs_lb))

    firstIgnitionNow = (
        T3 | T2G | T2 | T6 | F3 | F6 | F4 | F8
        | BO_UP | BE_UP | VBO_UP | BUY_HERE | SIG_260308 | L88
    )
    earlyIgnitionNow = ((T3 | T2G | T2 | T6 | F3 | F6 | F8) & softAbsorptionContext)
    momentumClusterNow = (
        (VBO_UP | BUY_HERE | SIG_260308 | L88 | BE_UP | BO_UP
         | F3 | F6 | F8 | BB_BRK | ATR_BRK | THREE_G | ROCKET)
        & (T6 | T2G | T2 | F3 | F6 | F4 | F8
           | (df['close'] > df['high'].shift(1, fill_value=0))
           | BB_BRK | ATR_BRK | THREE_G | ROCKET)
    )
    continuationLadderNow = (
        (BUY_HERE | SIG_260308 | L88 | VBO_UP | BB_BRK | ATR_BRK | THREE_G | ROCKET)
        & (F3 | F6 | F8 | T6 | BE_UP | BO_UP | BB_BRK | ATR_BRK)
    )

    nnnStructureOk = (
        resetSequence | supportAbsSequence | preFinalSequence
        | (recentCompression & (absorptionContext | (recentCompression & softAbsorptionContext)))
    )
    nnnRaw = nnnStructureOk & (firstIgnitionNow | earlyIgnitionNow) & preTurnStructure
    N = _cooldown(nnnRaw, _COOLDOWN_BARS)

    recentNNNStyleIgnition = _f_happened(
        nnnRaw | firstIgnitionNow | earlyIgnitionNow, _IGNITION_LOOKBACK
    )
    mxBaseOk      = recentNNNStyleIgnition | recentCompression | absorptionContext
    mxClusterOk   = momentumClusterNow | continuationLadderNow | comboStrongNow
    mxStructureOk = (
        fullSequence | supportAbsSequence | preFinalSequence | resetSequence
        | (mxBaseOk & mxClusterOk)
    )
    mxTriggerOk = (
        momentumClusterNow | continuationLadderNow | comboStrongNow
        | (firstIgnitionNow & VBO_UP)
    )
    mxRaw = mxStructureOk & mxTriggerOk & preTurnStructure
    MX = _cooldown(mxRaw, _COOLDOWN_BARS)

    l_prev3 = (
        L64.shift(1, fill_value=False) | L64.shift(2, fill_value=False) | L64.shift(3, fill_value=False)
        | L43.shift(1, fill_value=False) | L43.shift(2, fill_value=False) | L43.shift(3, fill_value=False)
        | L22.shift(1, fill_value=False) | L22.shift(2, fill_value=False) | L22.shift(3, fill_value=False)
    )

    sqbBase = SQ & (L64 | L34)
    bctBase = sqbBase & SVS
    ldBase  = LOAD
    ldsBase = LOAD & strBstContext
    ldcBase = LOAD & SQ & (L64 | L34)
    ldpBase = ldcBase & strBstContext
    lrcBase = l_prev3 & L34
    lrpBase = lrcBase & SQ & LOAD
    wrcBase = isW & (recentCompression | supportStep | absStep | NS | SQ)
    f8cBase = F8

    SQB = _cooldown(sqbBase, cd_ctx)
    BCT = _cooldown(bctBase, cd_ctx)
    LD  = _cooldown(ldBase,  cd_ctx)
    LDS = _cooldown(ldsBase, cd_ctx)
    LDC = _cooldown(ldcBase, cd_ctx)
    LDP = _cooldown(ldpBase, cd_ctx)
    LRC = _cooldown(lrcBase, cd_ctx)
    LRP = _cooldown(lrpBase, cd_ctx)
    WRC = _cooldown(wrcBase, cd_ctx)
    F8C = _cooldown(f8cBase, cd_ctx)

    asSetup  = A | SM
    nmSetup  = N | MX
    asRecent = _f_happened(asSetup, gog_lb)
    nmRecent = _f_happened(nmSetup, gog_lb)

    recentLoad    = _f_happened(LOAD,    load_lb)
    recentLDP     = _f_happened(ldpBase, ldp_lb)
    recentLRP     = _f_happened(lrpBase, ldp_lb)
    recentPremium = recentLDP | recentLRP
    recentWRC     = _f_happened(wrcBase, wrc_lb)
    recentF8C     = _f_happened(f8cBase, wrc_lb)
    recentCompCtx = recentWRC | recentF8C

    GOG1_raw = VBO_UP & asRecent & nmRecent
    GOG2_raw = VBO_UP & nmRecent & ~asRecent
    GOG3_raw = VBO_UP & asRecent & ~nmRecent

    G1P = GOG1_raw & recentPremium
    G2P = GOG2_raw & recentPremium
    G3P = GOG3_raw & recentPremium
    G1L = GOG1_raw & recentLoad & ~recentPremium
    G2L = GOG2_raw & recentLoad & ~recentPremium
    G3L = GOG3_raw & recentLoad & ~recentPremium
    G1C = GOG1_raw & recentCompCtx & ~recentLoad & ~recentPremium
    G2C = GOG2_raw & recentCompCtx & ~recentLoad & ~recentPremium
    G3C = GOG3_raw & recentCompCtx & ~recentLoad & ~recentPremium
    GOG1 = GOG1_raw & ~recentPremium & ~recentLoad & ~recentCompCtx
    GOG2 = GOG2_raw & ~recentPremium & ~recentLoad & ~recentCompCtx
    GOG3 = GOG3_raw & ~recentPremium & ~recentLoad & ~recentCompCtx

    _priority = [
        ('G1P', G1P, 100), ('G2P', G2P, 92), ('G3P', G3P, 88),
        ('G1L', G1L, 82),  ('G2L', G2L, 76), ('G3L', G3L, 72),
        ('G1C', G1C, 66),  ('G2C', G2C, 60), ('G3C', G3C, 56),
        ('GOG1', GOG1, 50),('GOG2', GOG2, 46),('GOG3', GOG3, 42),
    ]
    gog_tier_arr  = np.full(n, '', dtype=object)
    gog_score_arr = np.full(n, np.nan, dtype=float)
    for label, sig_ser, score in _priority:
        sig_np = sig_ser.to_numpy().astype(bool)
        mask = sig_np & (gog_tier_arr == '')
        gog_tier_arr[mask] = label
        gog_score_arr[mask] = score

    gog_tier  = pd.Series(gog_tier_arr,  index=idx)
    gog_score = pd.Series(gog_score_arr, index=idx)

    def _label_col(ser, label):
        return ser.astype(bool).map({True: label, False: ''})

    setup_parts = pd.concat([
        _label_col(A, 'A'), _label_col(SM, 'SM'),
        _label_col(N, 'N'), _label_col(MX, 'MX'),
    ], axis=1)
    SETUP = setup_parts.apply(lambda r: ' '.join(v for v in r if v), axis=1)

    ctx_parts = pd.concat([
        _label_col(LD,'LD'), _label_col(LDS,'LDS'), _label_col(LDC,'LDC'), _label_col(LDP,'LDP'),
        _label_col(LRC,'LRC'), _label_col(LRP,'LRP'), _label_col(WRC,'WRC'), _label_col(F8C,'F8C'),
        _label_col(SQB,'SQB'), _label_col(BCT,'BCT'), _label_col(SVS,'SVS'),
    ], axis=1)
    CONTEXT = ctx_parts.apply(lambda r: ' '.join(v for v in r if v), axis=1)

    all_parts = pd.concat([SETUP, gog_tier, CONTEXT], axis=1)
    ALL_SIGNALS = all_parts.apply(lambda r: ' '.join(v for v in r if v), axis=1)

    # Diagnostics
    pct_change_3d  = df['close'].pct_change(3)  * 100
    pct_change_5d  = df['close'].pct_change(5)  * 100
    pct_change_10d = df['close'].pct_change(10) * 100
    high_20d  = df['high'].rolling(20, min_periods=1).max()
    low_20d   = df['low'].rolling(20, min_periods=1).min()
    prev_20d_high = high_20d.shift(1, fill_value=0)
    pct_from_20d_high = (df['close'] - high_20d) / high_20d.replace(0, np.nan) * 100
    pct_from_20d_low  = (df['close'] - low_20d)  / low_20d.replace(0, np.nan)  * 100
    distance_to_20d_high_pct = (high_20d - df['close']) / df['close'].replace(0, np.nan) * 100
    vol_ma20         = df['volume'].rolling(20, min_periods=1).mean()
    volume_ratio_20d = df['volume'] / vol_ma20.replace(0, np.nan)
    dollar_volume    = df['close'] * df['volume']
    gap_pct = ((df['open'] - df['close'].shift(1))
               / df['close'].shift(1).replace(0, np.nan) * 100)
    already_extended = (
        (pct_change_5d > 80) | (pct_change_10d > 120)
        | (df['close'] > prev_20d_high * _LATE_BREAK_MULT)
        | ((gap_pct > 40) & (volume_ratio_20d > 3))
    ).fillna(False)

    result = pd.DataFrame(index=idx)
    bool_int_map = {
        'A':A,'SM':SM,'N':N,'MX':MX,
        'GOG1':GOG1,'GOG2':GOG2,'GOG3':GOG3,
        'G1P':G1P,'G2P':G2P,'G3P':G3P,
        'G1L':G1L,'G2L':G2L,'G3L':G3L,
        'G1C':G1C,'G2C':G2C,'G3C':G3C,
        'LD':LD,'LDS':LDS,'LDC':LDC,'LDP':LDP,
        'LRC':LRC,'LRP':LRP,'WRC':WRC,'F8C':F8C,
        'SQB':SQB,'BCT':BCT,'SVS':SVS,
        'LOAD':LOAD,'SQ':SQ,'W':isW,'F8':F8,
        'L34':L34,'L43':L43,'L64':L64,'L22':L22,
        'VBO_UP':VBO_UP,'BO_UP':BO_UP,'BE_UP':BE_UP,'BX_UP':BX_UP,
    }
    for col, ser in bool_int_map.items():
        result[col] = ser.astype(int).reindex(idx, fill_value=0)

    result['GOG_TIER']    = gog_tier
    result['SETUP']       = SETUP
    result['CONTEXT']     = CONTEXT
    result['ALL_SIGNALS'] = ALL_SIGNALS
    result['GOG_SCORE']   = gog_score
    result['pct_change_3d']  = pct_change_3d
    result['pct_change_5d']  = pct_change_5d
    result['pct_change_10d'] = pct_change_10d
    result['pct_from_20d_high']        = pct_from_20d_high
    result['pct_from_20d_low']         = pct_from_20d_low
    result['distance_to_20d_high_pct'] = distance_to_20d_high_pct
    result['volume_ratio_20d']         = volume_ratio_20d
    result['dollar_volume']            = dollar_volume
    result['gap_pct']                  = gap_pct
    result['already_extended_flag']    = already_extended.astype(int)
    return result


# ── Display routing — SETUP / GOG / CTX rows ─────────────────────────────────

# SETUP row: A, SM, N, MX
GOG_SETUP_COLS: list[tuple[str, str]] = [
    ("A",  "A"), ("SM", "SM"), ("N", "N"), ("MX", "MX"),
]

# GOG row: tier labels in priority order
GOG_TIER_COLS: list[tuple[str, str]] = [
    ("G1P", "G1P"), ("G2P", "G2P"), ("G3P", "G3P"),
    ("G1L", "G1L"), ("G2L", "G2L"), ("G3L", "G3L"),
    ("G1C", "G1C"), ("G2C", "G2C"), ("G3C", "G3C"),
    ("GOG1", "GOG1"), ("GOG2", "GOG2"), ("GOG3", "GOG3"),
]

# CTX row: context tokens
GOG_CTX_COLS: list[tuple[str, str]] = [
    ("LDP", "LDP"), ("LRP", "LRP"),
    ("LDC", "LDC"), ("LRC", "LRC"),
    ("LDS", "LDS"), ("LD",  "LD"),
    ("SQB", "SQB"), ("BCT", "BCT"),
    ("WRC", "WRC"), ("F8C", "F8C"),
]

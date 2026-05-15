"""
GOG Priority Engine — FULL + Internal F8
Vectorized pandas/numpy translation of PineScript "260501 GOG Priority Engine — FULL + Internal F8"
"""

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Default parameters (matching PineScript defaults)
# ---------------------------------------------------------------------------
_GOG_LOOKBACK = 5
_LOAD_LOOKBACK = 10
_LDP_LOOKBACK = 10
_WRC_LOOKBACK = 10
_CONTEXT_COOLDOWN = 2
_BOTTOM_LOOKBACK = 10
_HARD_BOTTOM_LOOKBACK = 14
_SUPPORT_LOOKBACK = 18
_ABS_LOOKBACK = 12
_IGNITION_LOOKBACK = 6
_SEQ_LOOKBACK = 24
_COOLDOWN_BARS = 4
_RSI_LENGTH = 14
_RSI_COMPARE_BARS = 2
_USE_RSI_FILTER = True
_LATE_BREAK_MULT = 2.80


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _barssince(ser):
    """
    Vectorized barssince: returns pd.Series of floats.
    0 at the bar where cond is True, 1 at the next bar, etc.
    NaN if condition has never been True up to that bar.
    """
    b = ser.astype(bool).to_numpy()
    n = len(b)
    positions = np.arange(n, dtype=float)
    last_true = np.where(b, positions, -np.inf)
    cummax = np.maximum.accumulate(last_true)
    result = np.where(np.isinf(cummax), np.nan, positions - cummax)
    return pd.Series(result, index=ser.index)


def _f_happened(cond, n):
    """cond fired in last n bars (inclusive of current bar)."""
    return cond.astype(bool).rolling(n, min_periods=1).max().astype(bool)


def _f_stepOk(older, newer, n):
    """
    Both conditions fired within n bars, and older fired before or at the same
    time as newer (older.barssince >= newer.barssince).
    """
    bs_o = _barssince(older)
    bs_n = _barssince(newer)
    return (
        bs_o.notna()
        & bs_n.notna()
        & (bs_o <= n)
        & (bs_n <= n)
        & (bs_o >= bs_n)
    )


def _cooldown(ser, cd):
    """
    Suppress a signal if the same signal fired within the previous cd bars.
    Uses a simple forward loop; cd=0 disables suppression.
    """
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
    """Wilder RSI using EWM (matches most platform implementations)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=length - 1, min_periods=length).mean()
    avg_loss = loss.ewm(com=length - 1, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _sv(frame, col, idx):
    """
    Returns bool Series from frame[col] aligned to idx.
    Returns all-False if frame is None, empty, or col is missing.
    """
    if frame is None or frame.empty or col not in frame.columns:
        return pd.Series(False, index=idx)
    return frame[col].fillna(0).astype(bool).reindex(idx, fill_value=False)


# ---------------------------------------------------------------------------
# Main computation function
# ---------------------------------------------------------------------------

def compute_gog_signals(df, wlnbb, sig_df, f_sigs, vabs, ultra260, ultraV2, combo_df):
    """
    Translate the PineScript "260501 GOG Priority Engine — FULL + Internal F8"
    into vectorized pandas/numpy.

    Parameters
    ----------
    df        : OHLCV DataFrame with DatetimeIndex (open, high, low, close, volume)
    wlnbb     : compute_wlnbb() output — L34, L43, L64, L22, L555, BO_UP, BX_UP,
                BE_UP, vol_bucket columns
    sig_df    : compute_signals() output — sig_name, is_bull, bc columns
    f_sigs    : compute_f_signals() output — f3, f4, f6, f11 columns
    vabs      : compute_vabs() output — vbo_up, load_sig, sq, ns, abs_sig,
                climb_sig columns
    ultra260  : compute_260308_l88() output — sig_260308, sig_l88 columns
    ultraV2   : compute_ultra_v2() output — bf_buy, fbo_bull, eb_bull columns
    combo_df  : compute_combo() output — buy_2809, sig3g, bb_brk, atr_brk,
                rocket, rtv, hilo_buy, um_2809, svs_2809, cons_atr columns

    Returns
    -------
    pd.DataFrame aligned to df.index with all GOG signal columns.
    """
    # -----------------------------------------------------------------------
    # Guard: empty input
    # -----------------------------------------------------------------------
    if df is None or df.empty:
        return pd.DataFrame()

    idx = df.index
    n = len(idx)

    # -----------------------------------------------------------------------
    # Aliases for parameter constants
    # -----------------------------------------------------------------------
    seq = _SEQ_LOOKBACK
    sup = _SUPPORT_LOOKBACK
    abs_lb = _ABS_LOOKBACK
    gog_lb = _GOG_LOOKBACK
    load_lb = _LOAD_LOOKBACK
    ldp_lb = _LDP_LOOKBACK
    wrc_lb = _WRC_LOOKBACK
    cd_ctx = _CONTEXT_COOLDOWN

    # -----------------------------------------------------------------------
    # Ensure all input frames are safe (None → empty DataFrame)
    # -----------------------------------------------------------------------
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

    # -----------------------------------------------------------------------
    # ---- T / Z signals from sig_df ----------------------------------------
    # -----------------------------------------------------------------------
    def _sig(name):
        """Extract a named signal from sig_df['sig_name']."""
        if sig_df.empty or 'sig_name' not in sig_df.columns:
            return pd.Series(False, index=idx)
        raw = (sig_df['sig_name'] == name)
        return raw.reindex(idx, fill_value=False).astype(bool)

    T6   = _sig('T6')
    T4   = _sig('T4')
    T1G  = _sig('T1G')
    T2G  = _sig('T2G')
    T1   = _sig('T1')
    T2   = _sig('T2')
    T3   = _sig('T3')
    T9   = _sig('T9')
    T10  = _sig('T10')
    T11  = _sig('T11')
    T12  = _sig('T12')
    T5   = _sig('T5')

    Z4   = _sig('Z4')
    Z6   = _sig('Z6')
    Z1G  = _sig('Z1G')
    Z2G  = _sig('Z2G')
    Z1   = _sig('Z1')
    Z2   = _sig('Z2')
    Z3   = _sig('Z3')
    Z9   = _sig('Z9')
    Z10  = _sig('Z10')
    Z11  = _sig('Z11')
    Z12  = _sig('Z12')
    Z5   = _sig('Z5')
    Z7   = _sig('Z7')

    # -----------------------------------------------------------------------
    # ---- F signals from f_sigs --------------------------------------------
    # -----------------------------------------------------------------------
    F3  = _sv(f_sigs, 'f3',  idx)
    F4  = _sv(f_sigs, 'f4',  idx)
    F6  = _sv(f_sigs, 'f6',  idx)
    F11 = _sv(f_sigs, 'f11', idx)

    # -----------------------------------------------------------------------
    # ---- VABS signals ------------------------------------------------------
    # -----------------------------------------------------------------------
    VBO_UP   = _sv(vabs, 'vbo_up',    idx)
    LOAD     = _sv(vabs, 'load_sig',  idx)
    SQ       = _sv(vabs, 'sq',        idx)
    NS       = _sv(vabs, 'ns',        idx)
    ABS_SIG  = _sv(vabs, 'abs_sig',   idx)
    CLM_SIG  = _sv(vabs, 'climb_sig', idx)

    # -----------------------------------------------------------------------
    # ---- WLNBB signals -----------------------------------------------------
    # -----------------------------------------------------------------------
    L34   = _sv(wlnbb, 'L34',  idx)
    L43   = _sv(wlnbb, 'L43',  idx)
    L64   = _sv(wlnbb, 'L64',  idx)
    L22   = _sv(wlnbb, 'L22',  idx)
    L555  = _sv(wlnbb, 'L555', idx)
    BO_UP = _sv(wlnbb, 'BO_UP', idx)
    BX_UP = _sv(wlnbb, 'BX_UP', idx)
    BE_UP = _sv(wlnbb, 'BE_UP', idx)

    # -----------------------------------------------------------------------
    # ---- ultra260 signals --------------------------------------------------
    # -----------------------------------------------------------------------
    SIG_260308 = _sv(ultra260, 'sig_260308', idx)
    L88        = _sv(ultra260, 'sig_l88',    idx)

    # -----------------------------------------------------------------------
    # ---- ultraV2 signals ---------------------------------------------------
    # -----------------------------------------------------------------------
    BF4      = _sv(ultraV2, 'bf_buy',   idx)
    FBO_BULL = _sv(ultraV2, 'fbo_bull', idx)
    EB_BULL  = _sv(ultraV2, 'eb_bull',  idx)

    # -----------------------------------------------------------------------
    # ---- combo_df signals --------------------------------------------------
    # -----------------------------------------------------------------------
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

    # -----------------------------------------------------------------------
    # ---- RSI ---------------------------------------------------------------
    # -----------------------------------------------------------------------
    rsi = _rsi(df['close'], _RSI_LENGTH)
    rsi_pass = (rsi > rsi.shift(_RSI_COMPARE_BARS, fill_value=0)) if _USE_RSI_FILTER else pd.Series(True, index=idx)

    # -----------------------------------------------------------------------
    # ---- F8 (Internal) -----------------------------------------------------
    # -----------------------------------------------------------------------
    # [n] notation means .shift(n, fill_value=False)
    Z11_1 = Z11.shift(1, fill_value=False)
    Z11_2 = Z11.shift(2, fill_value=False)
    Z10_1 = Z10.shift(1, fill_value=False)
    Z10_2 = Z10.shift(2, fill_value=False)
    T10_1 = T10.shift(1, fill_value=False)
    T10_2 = T10.shift(2, fill_value=False)
    T11_1 = T11.shift(1, fill_value=False)
    T12_1 = T12.shift(1, fill_value=False)
    T12_2 = T12.shift(2, fill_value=False)

    F8_raw = (
        (Z11_2 & Z11)
        | (Z10_2 & Z10)
        | (Z11_1 & Z11)
        | (Z10_1 & Z11)
        | (Z10_1 & Z10)
        | (Z11_1 & Z10)
        | (T10_1 & T10)
        | (T10_2 & T10)
        | (T11_1 & T10)
        | ((T12_1 | T12_2) & T10)
        | (T11_1 & T11)
        | ((T12_1 | T12_2) & T11)
        | (T10_1 & T12)
        | (T12_1 & T12)
    )
    F8 = F8_raw & rsi_pass

    # -----------------------------------------------------------------------
    # ---- SVS signal --------------------------------------------------------
    # -----------------------------------------------------------------------
    vol_avg20 = df['volume'].rolling(20, min_periods=1).mean()
    vol_ratio = df['volume'] / vol_avg20.replace(0, np.nan)
    SVS = (
        (vol_ratio > 1.4)
        & (vol_ratio.shift(1, fill_value=0) <= 1.4)
        & (df['close'] > df['open'])
    )

    # -----------------------------------------------------------------------
    # ---- isW (W volume bucket) --------------------------------------------
    # -----------------------------------------------------------------------
    if 'vol_bucket' in wlnbb.columns:
        isW = (wlnbb['vol_bucket'].reindex(idx, fill_value='') == 'W')
    else:
        vol_mid = df['volume'].rolling(20, min_periods=1).mean()
        vol_std = df['volume'].rolling(20, min_periods=1).std().fillna(0)
        isW = df['volume'] < (vol_mid - vol_std).fillna(0)

    # -----------------------------------------------------------------------
    # ---- bullBar helper (close > open) ------------------------------------
    # -----------------------------------------------------------------------
    bullBar = df['close'] > df['open']

    # -----------------------------------------------------------------------
    # ---- Sequence logic ---------------------------------------------------
    # -----------------------------------------------------------------------
    zStep = Z1G | Z2G | Z3 | Z5 | Z9 | Z10 | Z11 | Z12 | Z7
    supportStep = L64 | L43 | L22 | L34 | L555
    absStep = SQ | NS | ABS_SIG | CLM_SIG | LOAD | VBO_UP
    tStep = T1 | T1G | T2 | T2G | T3 | T4 | T6 | T10 | T11 | T12 | F3 | F6 | F4 | F8 | F11
    finalStep = (
        VBO_UP | BO_UP | BX_UP | BE_UP | BUY_HERE | SIG_260308 | L88
        | F3 | F6 | F8 | BB_BRK | ATR_BRK | THREE_G | ROCKET
    )

    zToL            = _f_stepOk(zStep,       supportStep, seq)
    lToAbs          = _f_stepOk(supportStep, absStep,     seq)
    absToT          = _f_stepOk(absStep,     tStep,       seq)
    tToFinal        = _f_stepOk(tStep,       finalStep,   seq)

    fullSequence        = zToL & lToAbs & absToT & tToFinal
    supportAbsSequence  = lToAbs & absToT
    preFinalSequence    = (
        _f_happened(supportStep, sup)
        & _f_happened(absStep,   abs_lb)
        & tStep
    )
    resetSequence       = (
        _f_happened(supportStep, seq)
        & _f_happened(absStep,   seq)
        & _f_happened(tStep,     seq)
    )

    recentSupport = _f_happened(supportStep, sup)
    recentAbs     = _f_happened(absStep,     abs_lb)

    # -----------------------------------------------------------------------
    # ---- Late-close-break filter ------------------------------------------
    # -----------------------------------------------------------------------
    priorRangeHigh   = df['high'].rolling(20, min_periods=1).max().shift(1, fill_value=0)
    lateCloseBreak   = df['close'] > priorRangeHigh * _LATE_BREAK_MULT

    # -----------------------------------------------------------------------
    # ---- preTurnStructure --------------------------------------------------
    # -----------------------------------------------------------------------
    comboStrongNow = BUY_HERE | BB_BRK | ATR_BRK | THREE_G | ROCKET | SVS
    strBstContext  = comboStrongNow | SVS | SIG_260308 | L88

    preTurnStructure = (
        bullBar
        | (df['close'] > df['close'].shift(1, fill_value=0))
        | T6 | F3 | F6 | F8 | VBO_UP | BE_UP | BO_UP
        | BUY_HERE | SIG_260308 | comboStrongNow
    )

    # -----------------------------------------------------------------------
    # ---- SM / A setup signals ---------------------------------------------
    # -----------------------------------------------------------------------
    smxRealTrigger = T6 | F3 | F6 | VBO_UP | BE_UP | BO_UP
    smxEarlyTrigger = (T1 | T1G | T4 | T2 | T2G) & (
        LOAD | VBO_UP | BE_UP | F3 | F6
        | _f_happened(LOAD | VBO_UP | BE_UP, 3)
    )
    smxCurrentTrigger = smxRealTrigger | smxEarlyTrigger
    smxContextOk = recentSupport & (
        recentAbs | VBO_UP | LOAD | SQ | NS | ABS_SIG
    )
    smxStructureOk = (
        fullSequence
        | supportAbsSequence
        | preFinalSequence
        | resetSequence
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
        akanNearLocal
        | (df['close'] > priorLocalHigh)
        | (df['close'] > df['high'].shift(1, fill_value=0))
        | VBO_UP | BO_UP | BE_UP
    )
    akanStructureOk = fullSequence | supportAbsSequence | preFinalSequence
    akanRaw = (
        akanStructureOk
        & akanFinalStrict
        & finalStep
        & akanPressure
        & preTurnStructure
        & ~lateCloseBreak
    )
    A = _cooldown(akanRaw, _COOLDOWN_BARS)

    # -----------------------------------------------------------------------
    # ---- N / MX signals ---------------------------------------------------
    # -----------------------------------------------------------------------
    hardBottomZ      = Z4 | Z6 | Z9 | Z10 | Z11 | Z12
    lateBottomZ      = Z10 | Z11 | Z12
    bottomT          = T10 | T11 | T12

    recentHardBottomZ = _f_happened(hardBottomZ, _HARD_BOTTOM_LOOKBACK)
    recentCompression = recentHardBottomZ | _f_happened(
        lateBottomZ | bottomT | F8, _BOTTOM_LOOKBACK
    )

    absorptionContext     = recentSupport & recentAbs
    softAbsorptionContext = (
        recentAbs
        | recentSupport
        | _f_happened(SQ | NS | LOAD | ABS_SIG, abs_lb)
    )

    firstIgnitionNow = (
        T3 | T2G | T2 | T6 | F3 | F6 | F4 | F8
        | BO_UP | BE_UP | VBO_UP | BUY_HERE | SIG_260308 | L88
    )
    earlyIgnitionNow = (
        (T3 | T2G | T2 | T6 | F3 | F6 | F8) & softAbsorptionContext
    )
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
        resetSequence
        | supportAbsSequence
        | preFinalSequence
        | (recentCompression
           & (absorptionContext | (recentCompression & softAbsorptionContext)))
    )
    nnnRaw = nnnStructureOk & (firstIgnitionNow | earlyIgnitionNow) & preTurnStructure
    N = _cooldown(nnnRaw, _COOLDOWN_BARS)

    recentNNNStyleIgnition = _f_happened(
        nnnRaw | firstIgnitionNow | earlyIgnitionNow, _IGNITION_LOOKBACK
    )
    mxBaseOk      = recentNNNStyleIgnition | recentCompression | absorptionContext
    mxClusterOk   = momentumClusterNow | continuationLadderNow | comboStrongNow
    mxStructureOk = (
        fullSequence
        | supportAbsSequence
        | preFinalSequence
        | resetSequence
        | (mxBaseOk & mxClusterOk)
    )
    mxTriggerOk = (
        momentumClusterNow
        | continuationLadderNow
        | comboStrongNow
        | (firstIgnitionNow & VBO_UP)
    )
    mxRaw = mxStructureOk & mxTriggerOk & preTurnStructure
    MX = _cooldown(mxRaw, _COOLDOWN_BARS)

    # -----------------------------------------------------------------------
    # ---- Context signals --------------------------------------------------
    # -----------------------------------------------------------------------
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

    # -----------------------------------------------------------------------
    # ---- GOG Priority Engine ----------------------------------------------
    # -----------------------------------------------------------------------
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

    # -----------------------------------------------------------------------
    # ---- GOG_TIER and GOG_SCORE -------------------------------------------
    # -----------------------------------------------------------------------
    _priority = [
        ('G1P',  G1P,  100),
        ('G2P',  G2P,   92),
        ('G3P',  G3P,   88),
        ('G1L',  G1L,   82),
        ('G2L',  G2L,   76),
        ('G3L',  G3L,   72),
        ('G1C',  G1C,   66),
        ('G2C',  G2C,   60),
        ('G3C',  G3C,   56),
        ('GOG1', GOG1,  50),
        ('GOG2', GOG2,  46),
        ('GOG3', GOG3,  42),
    ]

    gog_tier_arr  = np.full(n, '', dtype=object)
    gog_score_arr = np.full(n, np.nan, dtype=float)

    for label, sig_ser, score in _priority:
        sig_np = sig_ser.to_numpy().astype(bool)
        # only update bars that haven't been assigned yet
        mask = sig_np & (gog_tier_arr == '')
        gog_tier_arr[mask]  = label
        gog_score_arr[mask] = score

    gog_tier  = pd.Series(gog_tier_arr,  index=idx)
    gog_score = pd.Series(gog_score_arr, index=idx)

    # -----------------------------------------------------------------------
    # ---- Text / label columns ---------------------------------------------
    # -----------------------------------------------------------------------
    def _label_col(ser, label):
        return ser.astype(bool).map({True: label, False: ''})

    # SETUP
    setup_parts = pd.concat([
        _label_col(A,  'A'),
        _label_col(SM, 'SM'),
        _label_col(N,  'N'),
        _label_col(MX, 'MX'),
    ], axis=1)
    SETUP = setup_parts.apply(lambda r: ' '.join(v for v in r if v), axis=1)

    # CONTEXT
    ctx_parts = pd.concat([
        _label_col(LD,  'LD'),
        _label_col(LDS, 'LDS'),
        _label_col(LDC, 'LDC'),
        _label_col(LDP, 'LDP'),
        _label_col(LRC, 'LRC'),
        _label_col(LRP, 'LRP'),
        _label_col(WRC, 'WRC'),
        _label_col(F8C, 'F8C'),
        _label_col(SQB, 'SQB'),
        _label_col(BCT, 'BCT'),
        _label_col(SVS, 'SVS'),
    ], axis=1)
    CONTEXT = ctx_parts.apply(lambda r: ' '.join(v for v in r if v), axis=1)

    # ALL_SIGNALS
    all_parts = pd.concat([
        SETUP,
        gog_tier,
        CONTEXT,
    ], axis=1)
    ALL_SIGNALS = all_parts.apply(
        lambda r: ' '.join(v for v in r if v), axis=1
    )

    # -----------------------------------------------------------------------
    # ---- Late / overextension diagnostics ---------------------------------
    # -----------------------------------------------------------------------
    pct_change_3d  = df['close'].pct_change(3)  * 100
    pct_change_5d  = df['close'].pct_change(5)  * 100
    pct_change_10d = df['close'].pct_change(10) * 100

    high_20d      = df['high'].rolling(20, min_periods=1).max()
    low_20d       = df['low'].rolling(20, min_periods=1).min()
    prev_20d_high = high_20d.shift(1, fill_value=0)

    pct_from_20d_high       = (df['close'] - high_20d) / high_20d.replace(0, np.nan) * 100
    pct_from_20d_low        = (df['close'] - low_20d)  / low_20d.replace(0, np.nan)  * 100
    distance_to_20d_high_pct = (high_20d - df['close']) / df['close'].replace(0, np.nan) * 100

    vol_ma20          = df['volume'].rolling(20, min_periods=1).mean()
    volume_ratio_20d  = df['volume'] / vol_ma20.replace(0, np.nan)
    dollar_volume     = df['close'] * df['volume']
    gap_pct           = (
        (df['open'] - df['close'].shift(1))
        / df['close'].shift(1).replace(0, np.nan) * 100
    )

    already_extended = (
        (pct_change_5d  > 80)
        | (pct_change_10d > 120)
        | (df['close'] > prev_20d_high * _LATE_BREAK_MULT)
        | ((gap_pct > 40) & (volume_ratio_20d > 3))
    ).fillna(False)

    # -----------------------------------------------------------------------
    # ---- Assemble output DataFrame ----------------------------------------
    # -----------------------------------------------------------------------
    result = pd.DataFrame(index=idx)

    # bool→int (0/1) columns
    bool_int_map = {
        'A':             A,
        'SM':            SM,
        'N':             N,
        'MX':            MX,
        'GOG1':          GOG1,
        'GOG2':          GOG2,
        'GOG3':          GOG3,
        'G1P':           G1P,
        'G2P':           G2P,
        'G3P':           G3P,
        'G1L':           G1L,
        'G2L':           G2L,
        'G3L':           G3L,
        'G1C':           G1C,
        'G2C':           G2C,
        'G3C':           G3C,
        'LD':            LD,
        'LDS':           LDS,
        'LDC':           LDC,
        'LDP':           LDP,
        'LRC':           LRC,
        'LRP':           LRP,
        'WRC':           WRC,
        'F8C':           F8C,
        'SQB':           SQB,
        'BCT':           BCT,
        'SVS':           SVS,
        'LOAD':          LOAD,
        'SQ':            SQ,
        'W':             isW,
        'F8':            F8,
        'L34':           L34,
        'L43':           L43,
        'L64':           L64,
        'L22':           L22,
        'VBO_UP':        VBO_UP,
        'BO_UP':         BO_UP,
        'BE_UP':         BE_UP,
        'BX_UP':         BX_UP,
        'T10':           T10,
        'T11':           T11,
        'T12':           T12,
        'Z10':           Z10,
        'Z11':           Z11,
        'Z12':           Z12,
        'Z4':            Z4,
        'Z6':            Z6,
        'Z9':            Z9,
        'F3':            F3,
        'F4':            F4,
        'F6':            F6,
        'F11':           F11,
        'BF4':           BF4,
        'SIG_260308':    SIG_260308,
        'L88':           L88,
        'UM':            UM,
        'SVS_RAW':       SVS_RAW_COMBO,
        'CONS':          CONS,
        'BUY_HERE':      BUY_HERE,
        'ATR_BREAKOUT':  ATR_BRK,
        'BOLL_BREAKOUT': BB_BRK,
        'HILO_BUY':      HILO_BUY,
        'RTV':           RTV,
        'THREE_G':       THREE_G,
        'ROCKET':        ROCKET,
    }
    for col, ser in bool_int_map.items():
        result[col] = ser.astype(int).reindex(idx, fill_value=0)

    # str columns
    result['GOG_TIER']    = gog_tier
    result['SETUP']       = SETUP
    result['CONTEXT']     = CONTEXT
    result['ALL_SIGNALS'] = ALL_SIGNALS

    # float columns
    result['GOG_SCORE']              = gog_score
    result['pct_change_3d']          = pct_change_3d
    result['pct_change_5d']          = pct_change_5d
    result['pct_change_10d']         = pct_change_10d
    result['pct_from_20d_high']      = pct_from_20d_high
    result['pct_from_20d_low']       = pct_from_20d_low
    result['distance_to_20d_high_pct'] = distance_to_20d_high_pct
    result['volume_ratio_20d']       = volume_ratio_20d
    result['dollar_volume']          = dollar_volume
    result['gap_pct']                = gap_pct

    # int column
    result['already_extended_flag'] = already_extended.astype(int)

    return result


# ---------------------------------------------------------------------------
# Forward statistics
# ---------------------------------------------------------------------------

def compute_forward_stats(df, gog_df):
    """
    Returns a DataFrame (same index as df) with forward-looking statistics.

    For each bar i:
    - fwd_close_1d/3d/5d/10d  : close return pct after N bars
    - max_high_5d/10d_pct     : max(high[i+1..i+5/10]) return pct from close[i]
    - hit_5pct_5d/10d         : 1 if max_high >= close[i]*1.05
    - hit_10pct_5d/10d        : 1 if max_high >= close[i]*1.10
    - vbo_within_5/10         : VBO_UP in next 5/10 bars
    - bars_to_next_vbo        : bars until next VBO_UP (NaN if none)
    - gog_within_5/10         : any GOG signal in next 5/10 bars
    - bars_to_next_gog        : bars until next GOG (NaN if none)

    Context signals (LD/LDS/LDC/LDP/LRC/LRP/WRC/F8C/SQB/BCT):
    - ctx_to_gog_close_return : (next_gog_close - this_close)/this_close*100
    - ctx_to_gog_high_return  : (next_gog_high  - this_close)/this_close*100
    - ctx_to_vbo_close_return : same for VBO_UP
    - ctx_to_vbo_high_return  : same for VBO_UP
    """
    if df is None or df.empty or gog_df is None or gog_df.empty:
        return pd.DataFrame(index=df.index if df is not None else [])

    idx = df.index
    n = len(idx)

    # Use numpy arrays throughout for O(n) performance
    close  = df['close'].to_numpy(dtype=float)
    high   = df['high'].to_numpy(dtype=float)

    # Safe column extraction from gog_df
    def _gcol(col):
        if col in gog_df.columns:
            return gog_df[col].reindex(idx, fill_value=0).to_numpy()
        return np.zeros(n, dtype=float)

    vbo_arr = _gcol('VBO_UP').astype(bool)

    # any GOG tier active
    gog_cols = ['GOG1', 'GOG2', 'GOG3', 'G1P', 'G2P', 'G3P',
                'G1L', 'G2L', 'G3L', 'G1C', 'G2C', 'G3C']
    gog_any_arr = np.zeros(n, dtype=bool)
    for c in gog_cols:
        if c in gog_df.columns:
            gog_any_arr |= gog_df[c].reindex(idx, fill_value=0).astype(bool).to_numpy()

    # -----------------------------------------------------------------------
    # Build _nxt_vbo[i] and _nxt_gog[i]: index of the NEXT bar (> i) where
    # the condition is True. O(n) right-to-left scan.
    # -----------------------------------------------------------------------
    _nxt_vbo = np.full(n, -1, dtype=np.int64)
    _nxt_gog = np.full(n, -1, dtype=np.int64)

    _last_vbo = -1
    _last_gog = -1
    for i in range(n - 1, -1, -1):
        # Record next occurrence (strictly > i) from previous right iteration
        _nxt_vbo[i] = _last_vbo
        _nxt_gog[i] = _last_gog
        # Update trackers with current bar (will be "next" for bar i-1)
        if vbo_arr[i]:
            _last_vbo = i
        if gog_any_arr[i]:
            _last_gog = i

    # -----------------------------------------------------------------------
    # Forward-window metrics (vectorized where possible, loop elsewhere)
    # -----------------------------------------------------------------------
    result = pd.DataFrame(index=idx)

    # fwd_close — pure numpy shift arithmetic
    close_safe = np.where(close != 0, close, np.nan)
    for lag, col in [(1, 'fwd_close_1d'), (3, 'fwd_close_3d'),
                     (5, 'fwd_close_5d'), (10, 'fwd_close_10d')]:
        fwd = np.full(n, np.nan)
        end = n - lag
        if end > 0:
            fwd[:end] = (close[lag:] - close[:end]) / close_safe[:end] * 100
        result[col] = fwd

    # max_high windows — vectorized via cumulative reversed-array rolling
    # Build suffix max arrays: sfx_max[i] = max(high[i..n-1]) built right-to-left
    def _rolling_fwd_max(arr, window):
        """max of arr[i+1..i+window] for each i (0-indexed)."""
        out = np.full(n, np.nan)
        # pad-right so slice always valid
        padded = np.concatenate([arr, np.full(window, np.nan)])
        for w in range(1, window + 1):
            shifted = padded[w : w + n]
            out = np.fmax(out, shifted)  # fmax ignores NaN
        return out

    def _rolling_fwd_any(arr_bool, window):
        """any(arr[i+1..i+window]) for each i."""
        out = np.zeros(n, dtype=bool)
        padded = np.concatenate([arr_bool.astype(np.uint8),
                                 np.zeros(window, dtype=np.uint8)])
        for w in range(1, window + 1):
            out |= padded[w : w + n].astype(bool)
        return out

    for window, suffix in [(5, '5d'), (10, '10d')]:
        fwd_max_h  = _rolling_fwd_max(high, window)
        vbo_win    = _rolling_fwd_any(vbo_arr, window)
        gog_win    = _rolling_fwd_any(gog_any_arr, window)

        cl_safe = np.where(close != 0, close, np.nan)
        max_h_pct  = (fwd_max_h - close) / cl_safe * 100
        hit5_arr   = (fwd_max_h >= close * 1.05).astype(int)
        hit10_arr  = (fwd_max_h >= close * 1.10).astype(int)
        # last bar has no forward data — set to NaN/0
        if n > 0:
            max_h_pct[-1]  = np.nan
            hit5_arr[-1]   = 0
            hit10_arr[-1]  = 0

        result[f'max_high_{suffix}_pct'] = max_h_pct
        result[f'hit_5pct_{suffix}']     = hit5_arr
        result[f'hit_10pct_{suffix}']    = hit10_arr
        result[f'vbo_within_{suffix}']   = vbo_win.astype(int)
        result[f'gog_within_{suffix}']   = gog_win.astype(int)

    # bars_to_next_vbo / bars_to_next_gog
    arange_n    = np.arange(n, dtype=float)
    bars_to_vbo = np.where(_nxt_vbo >= 0, _nxt_vbo - arange_n, np.nan)
    bars_to_gog = np.where(_nxt_gog >= 0, _nxt_gog - arange_n, np.nan)
    result['bars_to_next_vbo'] = bars_to_vbo
    result['bars_to_next_gog'] = bars_to_gog

    # -----------------------------------------------------------------------
    # Context signal → next GOG / VBO returns
    # -----------------------------------------------------------------------
    ctx_signal_cols = ['LD', 'LDS', 'LDC', 'LDP', 'LRC', 'LRP', 'WRC', 'F8C', 'SQB', 'BCT']
    ctx_any = np.zeros(n, dtype=bool)
    for c in ctx_signal_cols:
        if c in gog_df.columns:
            ctx_any |= gog_df[c].reindex(idx, fill_value=0).astype(bool).to_numpy()

    ctx_to_gog_close = np.full(n, np.nan)
    ctx_to_gog_high  = np.full(n, np.nan)
    ctx_to_vbo_close = np.full(n, np.nan)
    ctx_to_vbo_high  = np.full(n, np.nan)

    for i in range(n):
        if not ctx_any[i]:
            continue
        cl_i = close[i]
        if cl_i == 0:
            continue
        j_gog = _nxt_gog[i]
        if j_gog >= 0:
            ctx_to_gog_close[i] = (close[j_gog] - cl_i) / cl_i * 100
            ctx_to_gog_high[i]  = (high[j_gog]  - cl_i) / cl_i * 100
        j_vbo = _nxt_vbo[i]
        if j_vbo >= 0:
            ctx_to_vbo_close[i] = (close[j_vbo] - cl_i) / cl_i * 100
            ctx_to_vbo_high[i]  = (high[j_vbo]  - cl_i) / cl_i * 100

    result['ctx_to_gog_close_return'] = ctx_to_gog_close
    result['ctx_to_gog_high_return']  = ctx_to_gog_high
    result['ctx_to_vbo_close_return'] = ctx_to_vbo_close
    result['ctx_to_vbo_high_return']  = ctx_to_vbo_high

    # All-bar next-event returns (within 10 bars; NaN if next event > 10 bars away)
    ret_vbo_close = np.full(n, np.nan)
    ret_vbo_high  = np.full(n, np.nan)
    ret_gog_close = np.full(n, np.nan)
    ret_gog_high  = np.full(n, np.nan)
    for i in range(n):
        cl_i = close[i]
        if cl_i == 0:
            continue
        jv = _nxt_vbo[i]
        if jv >= 0 and (jv - i) <= 10:
            ret_vbo_close[i] = (close[jv] - cl_i) / cl_i * 100
            ret_vbo_high[i]  = (high[jv]  - cl_i) / cl_i * 100
        jg = _nxt_gog[i]
        if jg >= 0 and (jg - i) <= 10:
            ret_gog_close[i] = (close[jg] - cl_i) / cl_i * 100
            ret_gog_high[i]  = (high[jg]  - cl_i) / cl_i * 100
    result['ret_to_next_vbo_close'] = ret_vbo_close
    result['ret_to_next_vbo_high']  = ret_vbo_high
    result['ret_to_next_gog_close'] = ret_gog_close
    result['ret_to_next_gog_high']  = ret_gog_high

    return result

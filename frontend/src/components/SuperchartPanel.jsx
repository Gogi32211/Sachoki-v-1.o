import { useState, useRef, useEffect, useCallback, useMemo, Fragment } from 'react'
import { api } from '../api'

const TF_OPTIONS = ['1d', '4h', '1h', '30m', '15m']
const CELL_W  = 56   // px per bar column
const HDR_W   = 38   // px for the sticky label column
const MINI_H  = 24   // px height of mini-candle row

const BUCKET_HEX = { W: '#c3c0d3', L: '#0099ff', N: '#ffd000', B: '#e48100', VB: '#b02020' }
const PREUP_SET  = new Set(['P2', 'P3', 'P50', 'P89'])

// Row definitions — getSigs(bar) returns array of signal labels
const ROWS = [
  {
    key: 'z',
    label: 'Z',
    getSigs: (b) => {
      const z = b.tz?.startsWith('Z') ? [b.tz] : []
      const p = (b.combo ?? []).filter(s => PREUP_SET.has(s))
      return [...z, ...p]
    },
    chipCls: (s) => PREUP_SET.has(s)
      ? 'bg-gray-700 text-white'
      : 'bg-red-900 text-red-300',
  },
  {
    key: 't',
    label: 'T',
    getSigs: (b) => b.tz?.startsWith('T') ? [b.tz] : [],
    chipCls: () => 'bg-green-900 text-green-300',
  },
  {
    key: 'l',
    label: 'L',
    getSigs: (b) => b.l ?? [],
    chipCls: (s) => {
      if (s.startsWith('FRI'))                   return 'bg-cyan-900 text-cyan-300'
      if (s === 'BL')                            return 'bg-sky-900 text-sky-300'
      if (s === 'CCI' || s === 'CCI0R' || s === 'CCIB') return 'bg-violet-900 text-violet-300'
      if (s === 'RL')                            return 'bg-fuchsia-900 text-fuchsia-300'
      if (s === 'RH')                            return 'bg-fuchsia-900 text-fuchsia-400'
      if (s === 'PP')                            return 'bg-yellow-900 text-yellow-300'
      if (s === 'L555' || s === 'L22')           return 'bg-rose-900 text-rose-300'
      if (s === 'L2L4')                          return 'bg-sky-900 text-sky-400'
      if (s.includes('BE'))                      return 'bg-emerald-900 text-emerald-300'
      if (s.includes('↑'))                       return 'bg-lime-900 text-lime-300'
      if (s.includes('↓'))                       return 'bg-red-900 text-red-400'
      return 'bg-blue-900 text-blue-300'
    },
  },
  {
    key: 'f',
    label: 'F',
    getSigs: (b) => b.f ?? [],
    chipCls: () => 'bg-orange-900 text-orange-300',
  },
  {
    key: 'fly',
    label: 'FLY',
    getSigs: (b) => b.fly ?? [],
    chipCls: () => 'bg-purple-900 text-purple-200',
  },
  {
    key: 'g',
    label: 'G',
    getSigs: (b) => b.g ?? [],
    chipCls: () => 'bg-violet-900 text-violet-200',
  },
  {
    key: 'b',
    label: 'B',
    getSigs: (b) => b.b ?? [],
    chipCls: () => 'bg-amber-900 text-amber-300',
  },
  {
    key: 'combo',
    label: 'I',
    getSigs: (b) => (b.combo ?? []).filter(s => !PREUP_SET.has(s)),
    chipCls: (s) => {
      if (s === 'ROCKET' || s === 'BUY') return 'bg-green-900 text-green-200 font-bold'
      if (s.includes('↑') || s === '3G') return 'bg-lime-900 text-lime-300'
      if (s.includes('↓') || s === 'CONS' || s === '↓BIAS') return 'bg-red-900 text-red-300'
      return 'bg-teal-900 text-teal-300'
    },
  },
  {
    key: 'ultra',
    label: 'ULT',
    getSigs: (b) => b.ultra ?? [],
    chipCls: (s) => {
      if (s === 'BEST↑' || s === '4BF')   return 'bg-yellow-800 text-yellow-200 font-bold'
      if (s === 'FBO↑' || s === 'EB↑' || s === '3↑') return 'bg-lime-900 text-lime-300'
      if (s === 'FBO↓' || s === 'EB↓' || s === '4BF↓') return 'bg-red-900 text-red-300'
      if (s === 'L88')   return 'bg-violet-900 text-violet-200 font-bold'
      if (s === '260308') return 'bg-purple-900 text-purple-300'
      return 'bg-sky-900 text-sky-300'
    },
  },
  {
    key: 'vol',
    label: 'VOL',
    getSigs: (b) => b.vol ?? [],
    chipCls: () => 'bg-pink-900 text-pink-300 font-bold',
  },
  {
    key: 'vabs',
    label: 'VABS',
    getSigs: (b) => b.vabs ?? [],
    chipCls: (s) => {
      if (s === 'BEST★') return 'bg-lime-800 text-lime-200 font-bold'
      if (s === 'STRONG') return 'bg-emerald-900 text-emerald-200'
      if (s.includes('↑') || ['NS', 'ABS', 'CLM', 'LOAD'].includes(s))
        return 'bg-lime-900 text-lime-300'
      return 'bg-red-900/70 text-red-300'
    },
  },
  {
    key: 'wick',
    label: 'WICK',
    getSigs: (b) => b.wick ?? [],
    chipCls: (s) => s.includes('↑') ? 'bg-sky-900 text-sky-300' : 'bg-red-900/50 text-red-300',
  },
  {
    key: 'setup',
    label: 'SETUP',
    getSigs: (b) => b.setup ?? [],
    chipCls: (s) => {
      if (s === 'A')   return 'bg-orange-800/80 text-orange-100 ring-1 ring-orange-400 font-bold'
      if (s === 'SM')  return 'bg-lime-800/80 text-lime-100 ring-1 ring-lime-400 font-bold'
      if (s === 'N')   return 'bg-cyan-800/80 text-cyan-100 ring-1 ring-cyan-400 font-bold'
      if (s === 'MX')  return 'bg-pink-800/80 text-pink-100 ring-1 ring-pink-400 font-bold'
      return 'bg-md-surface-high text-md-on-surface'
    },
  },
  {
    key: 'gog',
    label: 'GOG',
    getSigs: (b) => b.gog_tier ? [b.gog_tier] : [],
    chipCls: (s) => {
      if (s.startsWith('G1P') || s.startsWith('G2P') || s.startsWith('G3P'))
        return 'bg-green-800 text-green-100 ring-1 ring-green-400 font-bold'
      if (s.startsWith('G1L') || s.startsWith('G2L') || s.startsWith('G3L'))
        return 'bg-emerald-800 text-emerald-100 ring-1 ring-emerald-400 font-bold'
      if (s.startsWith('G1C') || s.startsWith('G2C') || s.startsWith('G3C'))
        return 'bg-teal-800 text-teal-100 ring-1 ring-teal-400 font-bold'
      return 'bg-fuchsia-800 text-fuchsia-100 ring-1 ring-fuchsia-400 font-bold'
    },
  },
  {
    key: 'context',
    label: 'CTX',
    getSigs: (b) => b.context ?? [],
    chipCls: (s) => {
      if (s === 'LDP' || s === 'LRP') return 'bg-green-900 text-green-200 font-semibold'
      if (s === 'LDC' || s === 'LRC') return 'bg-teal-900 text-teal-200'
      if (s === 'LDS' || s === 'LD')  return 'bg-cyan-900 text-cyan-300'
      if (s === 'SQB' || s === 'BCT') return 'bg-blue-900 text-blue-200'
      if (s === 'WRC' || s === 'F8C') return 'bg-slate-700 text-slate-200'
      return 'bg-md-surface-high text-md-on-surface'
    },
  },
  {
    key: 'score',
    label: 'SCORE',
    getSigs: (b) => {
      const fbs = b.final_bull_score ?? 0
      const ss  = b.signal_score ?? 0
      const score = fbs > 0 ? fbs : ss
      return score >= 20 ? [score] : []
    },
    chipCls: (s) => {
      const n = Number(s)
      if (n >= 140) return 'bg-yellow-700 text-yellow-100 font-bold ring-1 ring-yellow-400'
      if (n >= 115) return 'bg-lime-800 text-lime-100 font-bold ring-1 ring-lime-400'
      if (n >= 90)  return 'bg-green-900 text-green-200 font-semibold'
      if (n >= 65)  return 'bg-teal-900 text-teal-300'
      return 'bg-md-surface-high text-md-on-surface-var'
    },
  },
]

const BETA_ZONE_CLS = {
  ELITE:       'text-amber-200 font-bold',
  OPTIMAL:     'text-emerald-300 font-bold',
  BUY:         'text-blue-300 font-bold',
  WATCH:       'text-violet-300',
  BUILDING:    'text-yellow-400',
  EXTENDED:    'text-amber-400',
  SHORT_WATCH: 'text-red-400',
  NEUTRAL:     'text-md-on-surface-var/70',
}
const BETA_ZONE_SHORT = {
  ELITE: 'ELT', OPTIMAL: 'OPT', BUY: 'BUY', WATCH: 'WCH',
  BUILDING: 'BLD', EXTENDED: 'EXT', SHORT_WATCH: 'SHT', NEUTRAL: '',
}

function barsForTf(tf) {
  return tf === '15m' ? 400 : ['30m', '1h'].includes(tf) ? 300 : tf === '4h' ? 200 : 150
}

function fmtDate(d, isIntraday) {
  if (typeof d === 'number') {
    const dt = new Date(d * 1000)
    if (isIntraday)
      return `${dt.getMonth() + 1}/${dt.getDate()} ${String(dt.getHours()).padStart(2, '0')}:${String(dt.getMinutes()).padStart(2, '0')}`
    return `${dt.getMonth() + 1}/${dt.getDate()}`
  }
  return String(d).slice(5)
}

function MiniCandle({ b, globalMin, globalRange, h = MINI_H }) {
  const cx  = CELL_W / 2
  const bw  = 10
  const toY = (p) => h - ((p - globalMin) / globalRange) * (h - 2) - 1
  const isUp = b.close >= b.open
  const color = isUp ? '#22c55e' : '#ef4444'
  const bodyTop = Math.min(toY(b.open), toY(b.close))
  const bodyH   = Math.max(1, Math.abs(toY(b.open) - toY(b.close)))
  return (
    <svg width={CELL_W} height={h} style={{ display: 'block' }}>
      <line x1={cx} y1={toY(b.high)} x2={cx} y2={toY(b.low)}
            stroke={color} strokeWidth={0.8} />
      <rect x={cx - bw / 2} y={bodyTop} width={bw} height={bodyH} fill={color} />
    </svg>
  )
}

export default function SuperchartPanel({
  initialTicker = 'AAPL', initialTf = '1d',
  onTickerChange,
}) {
  const [ticker, setTicker]       = useState(initialTicker)
  const [inputVal, setInputVal]   = useState(initialTicker)
  const [tf, setTf]               = useState(initialTf)
  const [bars, setBars]           = useState([])
  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState(null)
  const [showStats, setShowStats] = useState(false)
  const [statsData, setStatsData] = useState(null)
  const [statsLoading, setStatsLoading] = useState(false)
  const [statsSort, setStatsSort] = useState('avg_5bar')
  const matrixRef  = useRef(null)
  const isIntraday = ['4h', '1h', '30m', '15m'].includes(tf)

  // Stats rows sorted by selected column
  const sortedStats = useMemo(() => {
    if (!statsData?.results) return []
    return Object.entries(statsData.results)
      .filter(([, v]) => (v.n ?? 0) >= 3 && !v.warning)
      .sort(([, a], [, b]) => (b[statsSort] ?? -999) - (a[statsSort] ?? -999))
  }, [statsData, statsSort])

  // Mini-candle global price range
  const { globalMin, globalRange } = useMemo(() => {
    if (!bars.length) return { globalMin: 0, globalRange: 1 }
    const lo = Math.min(...bars.map(b => b.low))
    const hi = Math.max(...bars.map(b => b.high))
    return { globalMin: lo, globalRange: (hi - lo) || 1 }
  }, [bars])

  // Notify parent so global chart follows Superchart ticker/tf
  useEffect(() => { onTickerChange?.(ticker, tf) }, [ticker, tf])

  const load = useCallback((t, f) => {
    setLoading(true)
    setError(null)
    api.barSignals(t, f, barsForTf(f))
      .then(data => {
        setBars(data)
        setTimeout(() => {
          if (matrixRef.current)
            matrixRef.current.scrollLeft = matrixRef.current.scrollWidth
        }, 120)
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const loadStats = useCallback((t, f) => {
    setStatsLoading(true)
    api.signalStats(t, f, [], false, 3)
      .then(d => setStatsData(d))
      .catch(() => setStatsData(null))
      .finally(() => setStatsLoading(false))
  }, [])

  const exportCsv = useCallback(() => {
    if (!bars.length) return
    const join = (arr) => (arr ?? []).join(' ')
    const headers = [
      'date','open','high','low','close','vol_bucket','turbo_score',
      'rtb_phase','rtb_total','rtb_transition',
      'rtb_build','rtb_turn','rtb_ready','rtb_late','rtb_bonus3',
      'dbg_context_ready','dbg_t4_ctx','dbg_t6_ctx','dbg_t4t6_activation_plus',
      'dbg_launch_cluster_count','dbg_pending_phase','dbg_pending_phase_count',
      'Z','T','L','F','FLY','G','B','Combo','ULT','VOL','VABS','WICK',
      // ── Text Summary
      'SETUP','CONTEXT','GOG_TIER','ALL_SIGNALS',
      // ── Primary Scores
      'GOG_SCORE','SIGNAL_SCORE','SIGNAL_BUCKET','RESEARCH_SCORE','REGIME',
      // ── New score system
      'CLEAN_ENTRY_SCORE','SHAKEOUT_ABSORB_SCORE','ROCKET_SCORE',
      'EXTRA_BULL_SCORE','EXPERIMENTAL_SCORE',
      'HARD_BEAR_SCORE','VOLATILITY_RISK_SCORE',
      'FINAL_BULL_SCORE','FINAL_REGIME','FINAL_SCORE_BUCKET',
      // ── Model booleans
      'MDL_UM_GOG1','MDL_BH_GOG1','MDL_F8_GOG1','MDL_F8_BCT','MDL_F8_LRP',
      'MDL_L22_BCT','MDL_L22_LRP','MDL_BE_GOG1','MDL_BO_GOG1','MDL_Z10_GOG1',
      'MDL_LOAD_GOG1','MDL_260_GOG1','MDL_RKT_GOG1','MDL_F8_SVS','MDL_F8_CONS',
      'MDL_L22_SQB','MDL_3UP_GOG1','MDL_BLUE_GOG1','MDL_BX_GOG1','MDL_UM_LRP',
      'HAS_ELITE_MODEL','HAS_BEAR_MODEL',
      // ── Backward compat
      'BEARISH_RISK_SCORE',
      // ── Score Sub-Components
      'GOG_BASE_SCORE','PREMIUM_CONTEXT_SCORE','LOAD_CONTEXT_SCORE','L_RECLAIM_SCORE',
      'COMPRESSION_CONTEXT_SCORE','SQ_BCT_SCORE','BASE_SETUP_SCORE','RAW_SUPPORT_SCORE',
      'RISK_PENALTY','RESEARCH_FORWARD_SCORE',
      // ── Setup / GOG booleans
      'A','SM','N','MX',
      'GOG1','GOG2','GOG3','G1P','G2P','G3P','G1L','G2L','G3L','G1C','G2C','G3C',
      // ── Context signals
      'LD','LDS','LDC','LDP','LRC','LRP','WRC','F8C','SQB','BCT','SVS',
      // ── Raw signals
      'LOAD','SQ','W','F8',
      'L34','L43','L64','L22',
      'VBO_UP','BO_UP','BE_UP','BX_UP',
      'T10','T11','T12','Z10','Z11','Z12','Z4','Z6','Z9',
      'F3','F4','F6','F11','4BF','SIG_260308','L88','UM','SVS_RAW','CONS',
      'BUY_HERE','ATR_BREAKOUT','BOLL_BREAKOUT','HILO_BUY','RTV','THREE_G','ROCKET',
      // ── Diagnostics
      'ALREADY_EXTENDED_FLAG',
      'PCT_CHANGE_3D','PCT_CHANGE_5D','PCT_CHANGE_10D',
      'PCT_FROM_20D_HIGH','PCT_FROM_20D_LOW','DIST_20D_HIGH','VOL_RATIO_20D',
      'DOLLAR_VOLUME','GAP_PCT',
      // ── BETA Score
      'BETA_SCORE','BETA_RAW','BETA_SETUP','BETA_MOMENTUM','BETA_EXCESS','BETA_ZONE','BETA_AUTO_BUY',
      // ── Forward returns
      'FWD_1D','FWD_3D','FWD_5D','FWD_10D','MAX_HIGH_5D','MAX_HIGH_10D',
      'HIT_5PCT_5D','HIT_10PCT_5D','HIT_5PCT_10D','HIT_10PCT_10D',
      // ── Next event
      'BARS_TO_VBO','BARS_TO_GOG',
      'VBO_W5','VBO_W10','GOG_W5','GOG_W10',
      'RET_TO_NEXT_VBO_CLOSE','RET_TO_NEXT_VBO_HIGH',
      'RET_TO_NEXT_GOG_CLOSE','RET_TO_NEXT_GOG_HIGH',
      // ── All TurboScan signal booleans
      // VABS
      'SIG_BEST','SIG_STRONG','SIG_VBO_DN',
      'SIG_NS_VABS','SIG_ND_VABS','SIG_SC','SIG_BC','SIG_ABS','SIG_CLM',
      // UltraV2
      'SIG_BEST_UP','SIG_FBO_UP','SIG_EB_UP','SIG_3UP',
      'SIG_FBO_DN','SIG_EB_DN','SIG_4BF_DN',
      // L sub
      'SIG_FRI34','SIG_FRI43','SIG_FRI64',
      'SIG_L555','SIG_L2L4','SIG_BLUE',
      'SIG_CCI','SIG_CCI0R','SIG_CCIB',
      'SIG_BO_DN','SIG_BX_DN','SIG_BE_DN',
      'SIG_RL','SIG_RH','SIG_PP',
      // G individual
      'SIG_G1','SIG_G2','SIG_G4','SIG_G6','SIG_G11',
      // B individual
      'SIG_B1','SIG_B2','SIG_B3','SIG_B4','SIG_B5','SIG_B6',
      'SIG_B7','SIG_B8','SIG_B9','SIG_B10','SIG_B11',
      // F individual
      'SIG_F1','SIG_F2','SIG_F3','SIG_F4','SIG_F5','SIG_F6',
      'SIG_F7','SIG_F8','SIG_F9','SIG_F10','SIG_F11',
      // FLY sub
      'SIG_FLY_ABCD','SIG_FLY_CD','SIG_FLY_BD','SIG_FLY_AD',
      // Wick sub
      'SIG_WK_UP','SIG_WK_DN','SIG_X1','SIG_X2','SIG_X1G','SIG_X3',
      // Combo sub
      'SIG_BIAS_UP','SIG_BIAS_DN','SIG_SVS','SIG_CONSO',
      'SIG_P2','SIG_P3','SIG_P50','SIG_P89','SIG_BUY','SIG_3G',
      // VA + vol
      'SIG_VA','SIG_VOL_5X','SIG_VOL_10X','SIG_VOL_20X',
      // TZ / state
      'SIG_TZ','SIG_T','SIG_Z',
      'SIG_TZ3','SIG_TZ2','SIG_TZ_FLIP',
      'SIG_CD','SIG_CA','SIG_CW','SIG_SEQ_BCONT',
      // ── NS/ND Delta (disambiguated from VABS)
      'SIG_NS_DELTA','SIG_ND_DELTA',
      // ── Meta family any-flags
      'SIG_ANY_F','SIG_ANY_B','SIG_ANY_P','SIG_ANY_D',
      'SIG_L_ANY','SIG_BE_ANY','SIG_GOG_PLUS','SIG_NOT_EXT',
      // ── Price vs EMA
      'PRICE_GT_20','PRICE_GT_50','PRICE_GT_89','PRICE_GT_200',
      'PRICE_LT_20','PRICE_LT_50','PRICE_LT_89','PRICE_LT_200',
      // ── RSI filters
      'RSI_LE_35','RSI_GE_70',
      // ── Source / cross-engine
      'YF_SOURCE','CROSS_2PLUS','CROSS_3PLUS','CROSS_4PLUS','EARLY_E',
      // ── P66/P55
      'SIG_P66','SIG_P55',
      // ── D-family PREDN
      'SIG_D66','SIG_D55','SIG_D89','SIG_D50','SIG_D3','SIG_D2',
      // ── Delta extras
      'SIG_FLP_UP','SIG_ORG_UP','SIG_DD_UP_RED','SIG_D_UP_RED',
      'SIG_D_DN_GREEN','SIG_DD_DN_GREEN',
      // ── CISD
      'SIG_CISD_CPLUS','SIG_CISD_CPLUS_MINUS','SIG_CISD_CPLUS_MM',
      // ── PARA context
      'SIG_PARA_PREP','SIG_PARA_START','SIG_PARA_PLUS','SIG_PARA_RETEST',
    ]
    const ctx = (b, tok) => (b.context ?? []).includes(tok) ? 1 : 0
    const s = (b, k) => b[k] ?? 0
    const rows = bars.map(b => [
      b.date,
      b.open?.toFixed(2), b.high?.toFixed(2), b.low?.toFixed(2), b.close?.toFixed(2),
      b.vol_bucket ?? '',
      b.turbo_score ?? 0,
      b.rtb_phase ?? '',
      b.rtb_total ?? 0,
      b.rtb_transition ?? '',
      b.rtb_build ?? 0,
      b.rtb_turn ?? 0,
      b.rtb_ready ?? 0,
      b.rtb_late ?? 0,
      b.rtb_bonus3 ?? 0,
      b.dbg_context_ready ? 1 : 0,
      b.dbg_t4_ctx ? 1 : 0,
      b.dbg_t6_ctx ? 1 : 0,
      b.dbg_t4t6_activation_plus ? 1 : 0,
      b.dbg_launch_cluster_count ?? 0,
      b.dbg_pending_phase ?? '',
      b.dbg_pending_phase_count ?? 0,
      b.tz?.startsWith('Z') ? b.tz : '',
      b.tz?.startsWith('T') ? b.tz : '',
      join(b.l),
      join(b.f),
      join(b.fly),
      join(b.g),
      join(b.b),
      join((b.combo ?? []).filter(s => !PREUP_SET.has(s))),
      join(b.ultra),
      join(b.vol),
      join(b.vabs),
      join(b.wick),
      // ── Text Summary
      join(b.setup), join(b.context), b.gog_tier ?? '', b.all_signals ?? '',
      // ── Primary Scores
      b.gog_score ?? 0,
      b.signal_score ?? 0, b.signal_bucket ?? '', b.research_score ?? 0, b.regime ?? '',
      // ── New score system
      b.clean_entry_score ?? 0, b.shakeout_absorb_score ?? 0, b.rocket_score ?? 0,
      b.extra_bull_score ?? 0, b.experimental_score ?? 0,
      b.hard_bear_score ?? 0, b.volatility_risk_score ?? 0,
      b.final_bull_score ?? 0, b.final_regime ?? '', b.final_score_bucket ?? '',
      // ── Model booleans
      b.mdl_um_gog1 ?? 0, b.mdl_bh_gog1 ?? 0, b.mdl_f8_gog1 ?? 0,
      b.mdl_f8_bct ?? 0,  b.mdl_f8_lrp ?? 0,
      b.mdl_l22_bct ?? 0, b.mdl_l22_lrp ?? 0, b.mdl_be_gog1 ?? 0,
      b.mdl_bo_gog1 ?? 0, b.mdl_z10_gog1 ?? 0,
      b.mdl_load_gog1 ?? 0, b.mdl_260_gog1 ?? 0, b.mdl_rkt_gog1 ?? 0,
      b.mdl_f8_svs ?? 0, b.mdl_f8_cons ?? 0,
      b.mdl_l22_sqb ?? 0, b.mdl_3up_gog1 ?? 0, b.mdl_blue_gog1 ?? 0,
      b.mdl_bx_gog1 ?? 0, b.mdl_um_lrp ?? 0,
      b.has_elite_model ?? 0, b.has_bear_model ?? 0,
      // ── Backward compat
      b.bearish_risk_score ?? 0,
      // ── Score Sub-Components
      b.gog_base_score ?? 0, b.premium_context_score ?? 0, b.load_context_score ?? 0,
      b.l_reclaim_score ?? 0, b.compression_context_score ?? 0, b.sq_bct_score ?? 0,
      b.base_setup_score ?? 0, b.raw_support_score ?? 0,
      b.risk_penalty ?? 0, b.research_forward_score ?? 0,
      // ── Setup / GOG booleans
      (b.setup ?? []).includes('A')  ? 1 : 0,
      (b.setup ?? []).includes('SM') ? 1 : 0,
      (b.setup ?? []).includes('N')  ? 1 : 0,
      (b.setup ?? []).includes('MX') ? 1 : 0,
      b.gog1 ?? 0, b.gog2 ?? 0, b.gog3 ?? 0,
      b.g1p ?? 0, b.g2p ?? 0, b.g3p ?? 0,
      b.g1l ?? 0, b.g2l ?? 0, b.g3l ?? 0,
      b.g1c ?? 0, b.g2c ?? 0, b.g3c ?? 0,
      // ── Context signals
      ctx(b,'LD'), ctx(b,'LDS'), ctx(b,'LDC'), ctx(b,'LDP'),
      ctx(b,'LRC'), ctx(b,'LRP'), ctx(b,'WRC'), ctx(b,'F8C'),
      ctx(b,'SQB'), ctx(b,'BCT'), ctx(b,'SVS'),
      // ── Raw signals
      b.raw_load ?? 0, b.raw_sq ?? 0, b.raw_w ?? 0, b.raw_f8 ?? 0,
      b.raw_l34 ?? 0, b.raw_l43 ?? 0, b.raw_l64 ?? 0, b.raw_l22 ?? 0,
      b.raw_vbo_up ?? 0, b.raw_bo_up ?? 0, b.raw_be_up ?? 0, b.raw_bx_up ?? 0,
      b.raw_t10 ?? 0, b.raw_t11 ?? 0, b.raw_t12 ?? 0,
      b.raw_z10 ?? 0, b.raw_z11 ?? 0, b.raw_z12 ?? 0,
      b.raw_z4 ?? 0, b.raw_z6 ?? 0, b.raw_z9 ?? 0,
      b.raw_f3 ?? 0, b.raw_f4 ?? 0, b.raw_f6 ?? 0, b.raw_f11 ?? 0,
      b.raw_bf4 ?? 0, b.raw_sig260308 ?? 0, b.raw_l88 ?? 0, b.raw_um ?? 0,
      b.raw_svs_raw ?? 0, b.raw_cons ?? 0,
      b.raw_buy_here ?? 0, b.raw_atr_brk ?? 0, b.raw_bb_brk ?? 0,
      b.raw_hilo_buy ?? 0, b.raw_rtv ?? 0, b.raw_three_g ?? 0, b.raw_rocket ?? 0,
      // ── Diagnostics
      b.already_extended ?? 0,
      b.pct_change_3d ?? '', b.pct_change_5d ?? '', b.pct_change_10d ?? '',
      b.pct_from_20d_high ?? '', b.pct_from_20d_low ?? '',
      b.distance_to_20d_high_pct ?? '', b.volume_ratio_20d ?? '',
      b.dollar_volume ?? '', b.gap_pct ?? '',
      // ── BETA Score
      b.beta_score ?? '', b.beta_raw ?? '', b.beta_setup ?? '', b.beta_momentum ?? '',
      b.beta_excess ?? '', b.beta_zone ?? '', b.beta_auto_buy ? 1 : 0,
      // ── Forward returns
      b.fwd_close_1d ?? '', b.fwd_close_3d ?? '', b.fwd_close_5d ?? '', b.fwd_close_10d ?? '',
      b.max_high_5d_pct ?? '', b.max_high_10d_pct ?? '',
      b.hit_5pct_5d ?? 0, b.hit_10pct_5d ?? 0, b.hit_5pct_10d ?? 0, b.hit_10pct_10d ?? 0,
      // ── Next event
      b.bars_to_next_vbo ?? '', b.bars_to_next_gog ?? '',
      b.vbo_within_5 ?? 0, b.vbo_within_10 ?? 0, b.gog_within_5 ?? 0, b.gog_within_10 ?? 0,
      b.ret_to_next_vbo_close ?? '', b.ret_to_next_vbo_high ?? '',
      b.ret_to_next_gog_close ?? '', b.ret_to_next_gog_high ?? '',
      // ── All TurboScan signal booleans
      s(b,'sig_best'), s(b,'sig_strong'), s(b,'sig_vbo_dn'),
      s(b,'sig_ns_vabs'), s(b,'sig_nd_vabs'), s(b,'sig_sc'), s(b,'sig_bc'), s(b,'sig_abs'), s(b,'sig_clm'),
      s(b,'sig_best_up'), s(b,'sig_fbo_up'), s(b,'sig_eb_up'), s(b,'sig_3up'),
      s(b,'sig_fbo_dn'), s(b,'sig_eb_dn'), s(b,'sig_4bf_dn'),
      s(b,'sig_fri34'), s(b,'sig_fri43'), s(b,'sig_fri64'),
      s(b,'sig_l555'), s(b,'sig_l2l4'), s(b,'sig_blue'),
      s(b,'sig_cci'), s(b,'sig_cci0r'), s(b,'sig_ccib'),
      s(b,'sig_bo_dn'), s(b,'sig_bx_dn'), s(b,'sig_be_dn'),
      s(b,'sig_rl'), s(b,'sig_rh'), s(b,'sig_pp'),
      s(b,'sig_g1'), s(b,'sig_g2'), s(b,'sig_g4'), s(b,'sig_g6'), s(b,'sig_g11'),
      s(b,'sig_b1'), s(b,'sig_b2'), s(b,'sig_b3'), s(b,'sig_b4'), s(b,'sig_b5'), s(b,'sig_b6'),
      s(b,'sig_b7'), s(b,'sig_b8'), s(b,'sig_b9'), s(b,'sig_b10'), s(b,'sig_b11'),
      s(b,'sig_f1'), s(b,'sig_f2'), s(b,'sig_f3'), s(b,'sig_f4'), s(b,'sig_f5'), s(b,'sig_f6'),
      s(b,'sig_f7'), s(b,'sig_f8'), s(b,'sig_f9'), s(b,'sig_f10'), s(b,'sig_f11'),
      s(b,'sig_fly_abcd'), s(b,'sig_fly_cd'), s(b,'sig_fly_bd'), s(b,'sig_fly_ad'),
      s(b,'sig_wk_up'), s(b,'sig_wk_dn'), s(b,'sig_x1'), s(b,'sig_x2'), s(b,'sig_x1g'), s(b,'sig_x3'),
      s(b,'sig_bias_up'), s(b,'sig_bias_dn'), s(b,'sig_svs'), s(b,'sig_conso'),
      s(b,'sig_p2'), s(b,'sig_p3'), s(b,'sig_p50'), s(b,'sig_p89'), s(b,'sig_buy'), s(b,'sig_3g'),
      s(b,'sig_va'), s(b,'sig_vol_5x'), s(b,'sig_vol_10x'), s(b,'sig_vol_20x'),
      s(b,'sig_tz'), s(b,'sig_t'), s(b,'sig_z'),
      s(b,'sig_tz3'), s(b,'sig_tz2'), s(b,'sig_tz_flip'),
      s(b,'sig_cd'), s(b,'sig_ca'), s(b,'sig_cw'), s(b,'sig_seq_bcont'),
      // ── NS/ND Delta
      s(b,'sig_ns_delta'), s(b,'sig_nd_delta'),
      // ── Meta flags
      s(b,'sig_any_f'), s(b,'sig_any_b'), s(b,'sig_any_p'), s(b,'sig_any_d'),
      s(b,'sig_l_any'), s(b,'sig_be_any'), s(b,'sig_gog_plus'), s(b,'sig_not_ext'),
      // ── Price vs EMA
      s(b,'sig_price_gt_20'), s(b,'sig_price_gt_50'),
      s(b,'sig_price_gt_89'), s(b,'sig_price_gt_200'),
      s(b,'sig_price_lt_20'), s(b,'sig_price_lt_50'),
      s(b,'sig_price_lt_89'), s(b,'sig_price_lt_200'),
      // ── RSI
      s(b,'sig_rsi_le_35'), s(b,'sig_rsi_ge_70'),
      // ── Source / cross
      s(b,'sig_yf_source'),
      s(b,'sig_cross_2plus'), s(b,'sig_cross_3plus'),
      s(b,'sig_cross_4plus'), s(b,'sig_early_e'),
      // ── P66/P55
      s(b,'sig_p66'), s(b,'sig_p55'),
      // ── D-family
      s(b,'sig_d66'), s(b,'sig_d55'), s(b,'sig_d89'),
      s(b,'sig_d50'), s(b,'sig_d3'),  s(b,'sig_d2'),
      // ── Delta extras
      s(b,'sig_flp_up'),    s(b,'sig_org_up'),
      s(b,'sig_dd_up_red'), s(b,'sig_d_up_red'),
      s(b,'sig_d_dn_green'), s(b,'sig_dd_dn_green'),
      // ── CISD
      s(b,'sig_cisd_cplus'), s(b,'sig_cisd_cplus_minus'), s(b,'sig_cisd_cplus_mm'),
      // ── PARA context
      s(b,'sig_para_prep'), s(b,'sig_para_start'),
      s(b,'sig_para_plus'), s(b,'sig_para_retest'),
    ])
    const csv = [headers, ...rows]
      .map(r => r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','))
      .join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${ticker}_${tf}_signals.csv`
    a.click()
    URL.revokeObjectURL(a.href)
  }, [bars, ticker, tf])

  useEffect(() => { load(ticker, tf) }, [ticker, tf, load])

  useEffect(() => {
    if (showStats) { setStatsData(null); loadStats(ticker, tf) }
  }, [ticker, tf])

  const handleSubmit = (e) => {
    e.preventDefault()
    const t = inputVal.trim().toUpperCase()
    if (t && t !== ticker) setTicker(t)
  }

  return (
    <div className="p-2 flex flex-col gap-2">
      {/* Controls */}
      <div className="flex items-center gap-2 flex-wrap">
        <form onSubmit={handleSubmit} className="flex gap-1">
          <input
            className="bg-md-surface-high text-md-on-surface text-sm px-2 py-1 rounded border border-white/[0.12] w-24 uppercase placeholder:text-md-on-surface-var/40 focus:outline-none focus:border-white/30 transition-colors"
            value={inputVal}
            onChange={e => setInputVal(e.target.value.toUpperCase())}
            placeholder="TICKER"
          />
          <button type="submit" className="text-xs px-3 py-1 bg-md-primary text-md-on-primary rounded font-medium hover:opacity-90 transition-opacity">
            Go
          </button>
        </form>
        <div className="flex gap-0.5 border border-white/[0.10] rounded p-0.5 bg-md-surface-con">
          {TF_OPTIONS.map(t => (
            <button key={t} onClick={() => setTf(t)}
              className={`text-xs px-2 py-1 rounded transition-colors
                ${tf === t ? 'bg-md-surface-high text-md-on-surface font-semibold' : 'text-md-on-surface-var hover:text-md-on-surface'}`}>
              {t}
            </button>
          ))}
        </div>
        <button
          onClick={() => {
            const next = !showStats
            setShowStats(next)
            if (next && !statsData) loadStats(ticker, tf)
          }}
          className={`text-xs px-2 py-1 rounded transition-colors border
            ${showStats
              ? 'bg-violet-900/60 border-violet-600/50 text-violet-200'
              : 'bg-md-surface-high border-white/[0.10] text-md-on-surface-var hover:text-md-on-surface'}`}>
          📊 Stats
        </button>
        {bars.length > 0 && (
          <button
            onClick={exportCsv}
            title={`Download ${ticker} ${tf.toUpperCase()} signal data as CSV`}
            className="text-xs px-2 py-1 rounded border border-white/[0.10] bg-md-surface-high text-md-on-surface-var hover:text-md-on-surface transition-colors">
            ⬇ CSV
          </button>
        )}
        {loading && <span className="text-xs text-md-on-surface-var/60 animate-pulse">loading…</span>}
        {error   && <span className="text-xs text-red-400">{error}</span>}
      </div>

      {/* Matrix */}
      {bars.length > 0 && (
        <div className="bg-md-surface-con rounded-md-md border border-white/[0.08] overflow-hidden">
          <div
            ref={matrixRef}
            className="overflow-x-auto overflow-y-hidden"
          >
            <table className="text-xs border-collapse" style={{ tableLayout: 'fixed' }}>
              <thead>
                {/* Mini-candle row */}
                <tr className="bg-md-surface">
                  <th style={{ width: HDR_W, minWidth: HDR_W }}
                      className="sticky left-0 z-10 bg-md-surface border-r border-white/[0.08]" />
                  {bars.map((b, i) => (
                    <th key={i} style={{ width: CELL_W, minWidth: CELL_W, padding: 0 }}
                        className="border-r border-white/[0.05]">
                      <MiniCandle b={b} globalMin={globalMin} globalRange={globalRange} />
                    </th>
                  ))}
                </tr>
                {/* Date + vol bucket row */}
                <tr className="bg-md-surface">
                  <th style={{ width: HDR_W, minWidth: HDR_W }}
                      className="sticky left-0 z-10 bg-md-surface border-r border-white/[0.08]" />
                  {bars.map((b, i) => (
                    <th key={i} style={{ width: CELL_W, minWidth: CELL_W }}
                        className="font-normal px-0 py-0 text-center border-r border-white/[0.05]">
                      <div className="flex flex-col items-center gap-px pb-0.5">
                        <span className="text-md-on-surface-var/70 font-mono" style={{ fontSize: 11 }}>
                          {fmtDate(b.date, isIntraday)}
                        </span>
                        <div className="rounded-sm"
                          style={{ width: 28, height: 2, backgroundColor: BUCKET_HEX[b.vol_bucket] ?? '#374151' }} />
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>

              <tbody>
                {ROWS.map(row => (
                  <tr key={row.key} className="border-t border-white/[0.06] hover:bg-md-surface-high/20">
                    <td
                      className="sticky left-0 z-10 bg-md-surface-con text-md-on-surface-var px-1
                                 text-right border-r border-white/[0.08] font-mono whitespace-nowrap"
                      style={{ width: HDR_W, minWidth: HDR_W, fontSize: 11, lineHeight: 1 }}>
                      {row.label}
                    </td>
                    {bars.map((b, i) => {
                      const sigs = row.getSigs(b)
                      return (
                        <td key={i}
                          className="px-0 py-px text-center border-r border-white/[0.05] align-top"
                          style={{ width: CELL_W, minWidth: CELL_W }}>
                          <div className="flex flex-col gap-px items-center">
                            {sigs.map(s => (
                              <span key={s}
                                className={`px-1 py-px rounded border border-white/10 font-mono leading-none ${row.chipCls(s)}`}
                                style={{ fontSize: 10 }}>
                                {s}
                              </span>
                            ))}
                          </div>
                        </td>
                      )
                    })}
                  </tr>
                ))}

                {/* Turbo score row */}
                <tr className="border-t border-white/[0.06]">
                  <td className="sticky left-0 z-10 bg-md-surface-con text-md-on-surface-var px-1
                                 text-right border-r border-white/[0.08] font-mono"
                      style={{ width: HDR_W, minWidth: HDR_W, fontSize: 11 }}>
                    turbo
                  </td>
                  {bars.map((b, i) => {
                    const s = b.turbo_score ?? 0
                    const cls = s >= 65 ? 'text-lime-300 font-bold'
                              : s >= 50 ? 'text-green-400 font-bold'
                              : s >= 35 ? 'text-yellow-400'
                              : s >= 20 ? 'text-md-on-surface'
                              : s > 0   ? 'text-md-on-surface-var'
                              : 'text-gray-700'
                    return (
                      <td key={i}
                        className={`px-0 py-0.5 text-center border-r border-white/[0.05] font-mono ${cls}`}
                        style={{ fontSize: 11, width: CELL_W, minWidth: CELL_W }}>
                        {s > 0 ? s : ''}
                      </td>
                    )
                  })}
                </tr>

                {/* RTB v4 phase row */}
                <tr className="border-t border-white/[0.06]">
                  <td className="sticky left-0 z-10 bg-md-surface-con text-md-on-surface-var px-1
                                 text-right border-r border-white/[0.08] font-mono"
                      style={{ width: HDR_W, minWidth: HDR_W, fontSize: 11 }}>
                    rtb
                  </td>
                  {bars.map((b, i) => {
                    const ph = b.rtb_phase
                    if (!ph || ph === '0') return (
                      <td key={i} style={{ width: CELL_W, minWidth: CELL_W }}
                          className="border-r border-white/[0.05]" />
                    )
                    const bgCls =
                      ph === 'C' ? 'bg-lime-700/80 text-lime-100 ring-1 ring-lime-500' :
                      ph === 'B' ? 'bg-sky-800/80  text-sky-200  ring-1 ring-sky-600' :
                      ph === 'A' ? 'bg-gray-700    text-md-on-surface' :
                      /* D */      'bg-orange-800/70 text-orange-200'
                    const isTransition = b.rtb_transition && b.rtb_transition.includes('TO')
                    return (
                      <td key={i}
                        className="px-0 py-px text-center border-r border-white/[0.05]"
                        style={{ width: CELL_W, minWidth: CELL_W }}
                        title={b.rtb_transition ? `${ph} — ${b.rtb_transition} (${b.rtb_total})` : `Phase ${ph} (${b.rtb_total})`}>
                        <div className="flex flex-col items-center gap-px">
                          <span className={`inline-block font-bold px-0.5 rounded font-mono leading-none ${bgCls} ${isTransition ? 'ring-2' : ''}`}
                            style={{ fontSize: 11 }}>
                            {ph}
                          </span>
                          <span className="font-mono text-md-on-surface-var leading-none" style={{ fontSize: 9 }}>
                            {b.rtb_total > 0 ? b.rtb_total.toFixed(0) : ''}
                          </span>
                        </div>
                      </td>
                    )
                  })}
                </tr>

                {/* BETA Score row */}
                <tr className="border-t border-white/[0.06]">
                  <td className="sticky left-0 z-10 bg-md-surface-con text-md-on-surface-var px-1
                                 text-right border-r border-white/[0.08] font-mono"
                      style={{ width: HDR_W, minWidth: HDR_W, fontSize: 11 }}>
                    β
                  </td>
                  {bars.map((b, i) => {
                    const sc   = b.beta_score
                    const zone = b.beta_zone ?? 'NEUTRAL'
                    const auto = b.beta_auto_buy
                    if (!sc) return <td key={i} style={{ width: CELL_W, minWidth: CELL_W }} className="border-r border-white/[0.05]" />
                    const cls = BETA_ZONE_CLS[zone] ?? 'text-md-on-surface-var'
                    return (
                      <td key={i}
                        className={`px-0 py-px text-center border-r border-white/[0.05] font-mono ${cls}`}
                        style={{ fontSize: 11, width: CELL_W, minWidth: CELL_W }}
                        title={`BETA ${sc} · ${zone}${auto ? ' · AUTO-BUY ★' : ''}`}>
                        <div className="flex flex-col items-center leading-none gap-px">
                          <span>{sc}{auto ? '★' : ''}</span>
                          <span style={{ fontSize: 9 }} className="text-md-on-surface-var font-mono">
                            {BETA_ZONE_SHORT[zone] ?? ''}
                          </span>
                        </div>
                      </td>
                    )
                  })}
                </tr>

                {/* Close price row */}
                <tr className="border-t border-white/[0.08]">
                  <td className="sticky left-0 z-10 bg-md-surface-con text-md-on-surface-var px-1
                                 text-right border-r border-white/[0.08] font-mono"
                      style={{ width: HDR_W, minWidth: HDR_W, fontSize: 11 }}>
                    close
                  </td>
                  {bars.map((b, i) => {
                    const prev = i > 0 ? bars[i - 1].close : b.close
                    const up   = b.close >= prev
                    return (
                      <td key={i}
                        className={`px-0 py-0.5 text-center border-r border-white/[0.05] font-mono
                                    ${up ? 'text-green-400' : 'text-red-400'}`}
                        style={{ fontSize: 11, width: CELL_W, minWidth: CELL_W }}>
                        {b.close >= 1000 ? b.close.toFixed(0)
                          : b.close >= 100 ? b.close.toFixed(1)
                          : b.close.toFixed(2)}
                      </td>
                    )
                  })}
                </tr>

                {/* RSI row */}
                <tr className="border-t border-white/[0.06]">
                  <td className="sticky left-0 z-10 bg-md-surface-con text-md-on-surface-var px-1
                                 text-right border-r border-white/[0.08] font-mono"
                      style={{ width: HDR_W, minWidth: HDR_W, fontSize: 11 }}>
                    RSI
                  </td>
                  {bars.map((b, i) => {
                    const v = b.rsi ?? b.RSI
                    if (v == null || v === 0) return <td key={i} style={{ width: CELL_W, minWidth: CELL_W }} className="border-r border-white/[0.05]" />
                    const cls = v <= 35 ? 'text-lime-300 font-bold' : v >= 70 ? 'text-red-400 font-bold' : 'text-md-on-surface-var'
                    return (
                      <td key={i}
                        className={`px-0 py-0.5 text-center border-r border-white/[0.05] font-mono ${cls}`}
                        style={{ fontSize: 11, width: CELL_W, minWidth: CELL_W }}>
                        {Math.round(v)}
                      </td>
                    )
                  })}
                </tr>

                {/* CCI row */}
                <tr className="border-t border-white/[0.06]">
                  <td className="sticky left-0 z-10 bg-md-surface-con text-md-on-surface-var px-1
                                 text-right border-r border-white/[0.08] font-mono"
                      style={{ width: HDR_W, minWidth: HDR_W, fontSize: 11 }}>
                    CCI
                  </td>
                  {bars.map((b, i) => {
                    const v = b.cci ?? b.CCI
                    if (v == null) return <td key={i} style={{ width: CELL_W, minWidth: CELL_W }} className="border-r border-white/[0.05]" />
                    const cls = v >= 100 ? 'text-lime-300 font-bold' : v <= -100 ? 'text-red-400 font-bold' : 'text-md-on-surface-var'
                    return (
                      <td key={i}
                        className={`px-0 py-0.5 text-center border-r border-white/[0.05] font-mono ${cls}`}
                        style={{ fontSize: 11, width: CELL_W, minWidth: CELL_W }}>
                        {Math.round(v)}
                      </td>
                    )
                  })}
                </tr>

                {/* Pf Score row */}
                <tr className="border-t border-white/[0.06]">
                  <td className="sticky left-0 z-10 bg-md-surface-con text-md-on-surface-var px-1
                                 text-right border-r border-white/[0.08] font-mono"
                      style={{ width: HDR_W, minWidth: HDR_W, fontSize: 11 }}>
                    Pf
                  </td>
                  {bars.map((b, i) => {
                    const v = b.profile_score
                    if (!v) return <td key={i} style={{ width: CELL_W, minWidth: CELL_W }} className="border-r border-white/[0.05]" />
                    const cls = v >= 20 ? 'text-lime-300 font-bold' : v >= 12 ? 'text-yellow-400' : 'text-md-on-surface-var'
                    return (
                      <td key={i}
                        className={`px-0 py-0.5 text-center border-r border-white/[0.05] font-mono ${cls}`}
                        style={{ fontSize: 11, width: CELL_W, minWidth: CELL_W }}>
                        {v}
                      </td>
                    )
                  })}
                </tr>

                {/* Category row */}
                <tr className="border-t border-white/[0.08]">
                  <td className="sticky left-0 z-10 bg-md-surface-con text-md-on-surface-var px-1
                                 text-right border-r border-white/[0.08] font-mono"
                      style={{ width: HDR_W, minWidth: HDR_W, fontSize: 11 }}>
                    Cat
                  </td>
                  {bars.map((b, i) => {
                    const cat = b.profile_category
                    if (!cat || cat === 'WATCH') return <td key={i} style={{ width: CELL_W, minWidth: CELL_W }} className="border-r border-white/[0.05]" />
                    const cls =
                      cat === 'SWEET_SPOT' ? 'text-green-300 font-bold' :
                      cat === 'BUILDING'   ? 'text-yellow-400' :
                      cat === 'LATE'       ? 'text-amber-500' : 'text-md-on-surface-var/70'
                    const label =
                      cat === 'SWEET_SPOT' ? '⭐' :
                      cat === 'BUILDING'   ? '↑' :
                      cat === 'LATE'       ? '⚠' : ''
                    return (
                      <td key={i}
                        className={`px-0 py-0.5 text-center border-r border-white/[0.05] ${cls}`}
                        title={cat}
                        style={{ fontSize: 11, width: CELL_W, minWidth: CELL_W }}>
                        {label}
                      </td>
                    )
                  })}
                </tr>

              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Signal Statistics Panel ── */}
      {showStats && (
        <div className="bg-md-surface-con rounded-md-md border border-white/[0.08] overflow-hidden">
          <div className="flex items-center gap-3 px-3 py-2 border-b border-white/[0.07] bg-md-surface">
            <span className="text-xs font-semibold text-violet-300">Signal Performance — {ticker} {tf.toUpperCase()}</span>
            <span className="text-xs text-md-on-surface-var">avg max-high over next N bars · sorted by</span>
            {statsLoading && <span className="text-xs text-md-on-surface-var animate-pulse ml-auto">loading…</span>}
          </div>

          {statsLoading ? (
            <div className="p-6 text-xs text-md-on-surface-var/70 text-center animate-pulse">Computing stats for all signals…</div>
          ) : !statsData || statsData.error ? (
            <div className="p-4 text-xs text-red-400">Could not load stats — {statsData?.error ?? 'unknown error'}</div>
          ) : sortedStats.length === 0 ? (
            <div className="p-4 text-xs text-md-on-surface-var">Not enough data (need ≥3 occurrences per signal)</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs border-collapse">
                <thead>
                  <tr className="border-b border-white/[0.07] bg-md-surface text-md-on-surface-var select-none">
                    <th className="text-left px-3 py-1.5 sticky left-0 bg-md-surface font-normal">Signal</th>
                    {[
                      ['n',         'N',     'occurrences'],
                      ['bull_rate', 'Bull%', 'next bar closed higher'],
                      ['avg_1bar',  '+1bar', 'avg % close next bar'],
                      ['avg_3bar',  'max3',  'avg max-high over 3 bars'],
                      ['avg_5bar',  'max5',  'avg max-high over 5 bars ★'],
                      ['mae_3',     'DD3',   'avg max drawdown over 3 bars'],
                      ['false_rate','False%','% fires with no gain over 3 bars'],
                    ].map(([col, label, title]) => (
                      <th key={col}
                        title={title}
                        onClick={() => setStatsSort(col)}
                        className={`text-right px-2 py-1.5 cursor-pointer whitespace-nowrap font-normal hover:text-white transition-colors
                          ${statsSort === col ? 'text-violet-300 bg-violet-950/40' : ''}`}>
                        {label}{statsSort === col ? ' ▼' : ''}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sortedStats.map(([key, st], idx) => {
                    const label = statsData.labels?.[key] ?? key
                    return (
                      <tr key={key}
                        className={`border-b border-white/[0.06] hover:bg-md-surface-high/30 ${idx === 0 && statsSort === 'avg_5bar' ? 'bg-violet-950/20' : ''}`}>
                        <td className="px-3 py-1 sticky left-0 bg-md-surface-con text-md-on-surface whitespace-nowrap font-mono" style={{ fontSize: 11 }}>
                          {label}
                        </td>
                        <td className="px-2 py-1 text-right font-mono text-md-on-surface-var">{st.n}</td>
                        <td className={`px-2 py-1 text-right font-mono
                          ${st.bull_rate >= 0.65 ? 'text-lime-300' : st.bull_rate >= 0.55 ? 'text-green-400' : st.bull_rate >= 0.45 ? 'text-yellow-400' : 'text-red-400'}`}>
                          {Math.round(st.bull_rate * 100)}%
                        </td>
                        <td className={`px-2 py-1 text-right font-mono ${st.avg_1bar > 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {st.avg_1bar > 0 ? '+' : ''}{st.avg_1bar?.toFixed(1)}%
                        </td>
                        <td className={`px-2 py-1 text-right font-mono ${st.avg_3bar > 1.5 ? 'text-green-400' : st.avg_3bar > 0 ? 'text-md-on-surface' : 'text-md-on-surface-var/70'}`}>
                          {st.avg_3bar > 0 ? '+' : ''}{st.avg_3bar?.toFixed(1)}%
                        </td>
                        <td className={`px-2 py-1 text-right font-mono font-semibold
                          ${statsSort === 'avg_5bar' ? 'bg-violet-950/20' : ''}
                          ${st.avg_5bar > 4 ? 'text-lime-300' : st.avg_5bar > 2 ? 'text-green-400' : st.avg_5bar > 0 ? 'text-md-on-surface' : 'text-md-on-surface-var/70'}`}>
                          {st.avg_5bar > 0 ? '+' : ''}{st.avg_5bar?.toFixed(1)}%
                        </td>
                        <td className="px-2 py-1 text-right font-mono text-red-400/80">
                          {st.mae_3?.toFixed(1)}%
                        </td>
                        <td className={`px-2 py-1 text-right font-mono
                          ${st.false_rate < 0.25 ? 'text-green-400' : st.false_rate < 0.4 ? 'text-yellow-400' : 'text-red-400'}`}>
                          {Math.round(st.false_rate * 100)}%
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              <div className="px-3 py-2 text-xs text-md-on-surface-var/70">
                {statsData.bars} bars analysed · signals with &lt;3 occurrences hidden · click column header to re-sort
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

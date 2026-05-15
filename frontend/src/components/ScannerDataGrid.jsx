import { useState } from 'react'
import { pwlHas } from './PersonalWatchlistPanel'
import SharedSignalChip from './SignalChip'
import SignalChipList from './SignalChipList'
import TickerCell from './TickerCell'
import TableScrollContainer from './TableScrollContainer'

// ── Colour helpers (shared) ───────────────────────────────────────────────────
const TZ_STRONG = new Set(['T4','T6','T1G','T2G'])
const TZ_BEAR   = new Set(['Z4','Z6','Z1G','Z2G','Z1','Z2','Z3','Z5','Z7','Z9','Z10','Z11','Z12'])

function scoreColor(s) {
  if (s >= 65) return 'text-lime-300 font-bold'
  if (s >= 50) return 'text-yellow-300 font-semibold'
  if (s >= 35) return 'text-blue-300'
  if (s >= 20) return 'text-md-on-surface'
  return 'text-md-on-surface-var/70'
}

function ultraScoreCls(s) {
  if (s == null) return 'text-gray-700'
  if (s >= 90) return 'text-emerald-200 font-extrabold'
  if (s >= 80) return 'text-emerald-300 font-bold'
  if (s >= 65) return 'text-teal-300 font-semibold'
  if (s >= 50) return 'text-yellow-200/90'
  return 'text-md-on-surface-var'
}

function ultraBandV2Label(s, fallback) {
  if (s == null) return fallback || ''
  if (s >= 90) return 'A+'
  if (s >= 80) return 'A'
  if (s >= 65) return 'B'
  if (s >= 50) return 'C'
  return 'D'
}

function betaZoneCls(zone) {
  switch (zone) {
    case 'ELITE':       return 'text-amber-200 font-bold'
    case 'OPTIMAL':     return 'text-emerald-300 font-bold'
    case 'BUY':         return 'text-blue-300 font-semibold'
    case 'WATCH':       return 'text-violet-300'
    case 'BUILDING':    return 'text-yellow-400'
    case 'EXTENDED':    return 'text-amber-400'
    case 'SHORT_WATCH': return 'text-red-400'
    default:            return 'text-md-on-surface-var/70'
  }
}

function gogTierCls(tier) {
  if (!tier) return ''
  if (tier.endsWith('P')) return 'bg-green-800 text-green-100 ring-1 ring-green-400 font-bold'
  if (tier.endsWith('L')) return 'bg-emerald-800 text-emerald-100 ring-1 ring-emerald-400'
  if (tier.endsWith('C')) return 'bg-teal-800 text-teal-100 ring-1 ring-teal-400'
  return 'bg-fuchsia-800 text-fuchsia-100 ring-1 ring-fuchsia-400'
}

function ctxTokCls(tok) {
  if (tok === 'LDP' || tok === 'LRP') return 'bg-green-900 text-green-200 font-semibold'
  if (tok === 'LDC' || tok === 'LRC') return 'bg-teal-900 text-teal-200'
  if (tok === 'LDS' || tok === 'LD')  return 'bg-cyan-900 text-cyan-300'
  if (tok === 'BCT')                  return 'bg-blue-900 text-blue-200 font-semibold'
  if (tok === 'SQB')                  return 'bg-blue-900 text-blue-300'
  return 'bg-md-surface-high text-md-on-surface-var'
}

const CTX_PRIO = [
  ['ctx_ldp','LDP'],['ctx_lrp','LRP'],
  ['ctx_ldc','LDC'],['ctx_lrc','LRC'],
  ['ctx_lds','LDS'],['ctx_ld','LD'],
  ['ctx_bct','BCT'],['ctx_sqb','SQB'],
  ['ctx_wrc','WRC'],['ctx_f8c','F8C'],['ctx_svs','SVS'],
]
function ctxTokens(r) {
  return CTX_PRIO.filter(([k]) => r[k]).map(([, t]) => t)
}

function scoreCls(n) {
  if (n >= 120) return 'text-yellow-300 font-bold'
  if (n >= 100) return 'text-lime-300 font-bold'
  if (n >= 80)  return 'text-green-300 font-semibold'
  if (n >= 60)  return 'text-teal-300'
  return 'text-md-on-surface-var'
}

const fmt = (v, d = 2) => v == null ? '—' : Number(v).toFixed(d)

// ── Row left-border by profile category ──────────────────────────────────────
function profileBorderCls(cat) {
  switch (cat) {
    case 'SWEET_SPOT':  return 'border-l-2 border-l-emerald-500'
    case 'WATCH':       return 'border-l-2 border-l-yellow-500'
    case 'SHORT_WATCH': return 'border-l-2 border-l-red-500'
    case 'BUILDING':    return 'border-l-2 border-l-blue-500'
    default:            return 'border-l-2 border-l-transparent'
  }
}

// ── T/Z cell chip — delegates to the shared SignalChip so colors match Superchart
function TZChip({ label }) {
  return <SharedSignalChip signal={label} size="sm" />
}

// ── StatusChip for profile category ──────────────────────────────────────────
function StatusChip({ cat }) {
  const cfgMap = {
    SWEET_SPOT:  'bg-emerald-900/50 text-emerald-300 text-[10px] px-1 py-0.5 rounded',
    WATCH:       'bg-yellow-900/50 text-yellow-300 text-[10px] px-1 py-0.5 rounded',
    BUILDING:    'bg-blue-900/40 text-blue-300 text-[10px] px-1 py-0.5 rounded',
    SHORT_WATCH: 'bg-red-900/40 text-red-400 text-[10px] px-1 py-0.5 rounded',
  }
  const cls = cfgMap[cat] || 'bg-md-surface-high text-md-on-surface-var text-[10px] px-1 py-0.5 rounded'
  const short = { SWEET_SPOT: 'SWEET', WATCH: 'WATCH', BUILDING: 'BUILD', SHORT_WATCH: 'SHORT' }
  return <span className={cls}>{short[cat] || cat}</span>
}

// ── Collect all active signals for a row in priority order ───────────────────
// type: 'bull' | 'bear' | 'info'
function collectSignals(r) {
  const sigs = []
  const bull = (label, priority) => sigs.push({ label, priority, type: 'bull' })
  const bear = (label, priority) => sigs.push({ label, priority, type: 'bear' })
  const info = (label, priority) => sigs.push({ label, priority, type: 'info' })

  // VABS / Vol
  if (r.vol_spike_20x) info('V×20', 10)
  else if (r.vol_spike_10x) info('V×10', 9)
  else if (r.vol_spike_5x)  info('V×5', 8)
  if (r.best_sig)   bull('BEST★', 10)
  if (r.strong_sig && !r.best_sig) bull('STR', 7)
  if (r.vbo_up)     bull('VBO↑', 7)
  if (r.abs_sig)    bull('ABS', 6)
  if (r.load_sig)   bull('LD', 5)
  if (r.climb_sig)  bull('CLB', 5)

  // Delta
  if (r.d_spring)      bull('dSPR', 9)
  if (r.d_blast_bull)  bull('ΔΔ↑', 8)
  else if (r.d_surge_bull) bull('Δ↑', 7)
  if (r.d_strong_bull) bull('B/S↑', 6)
  if (r.d_absorb_bull) bull('Ab↑', 5)

  // Combo
  if (r.rocket)    bull('🚀', 10)
  if (r.buy_2809)  bull('BUY', 8)
  if (r.para_plus) bull('PARA+', 9)
  else if (r.para_start) bull('PARA', 8)
  if (r.smx)       info('SMX', 7)
  if (r.akan_sig)  info('A', 7)
  if (r.smx_sig)   info('SM', 7)
  if (r.nnn_sig)   info('N', 7)
  if (r.mx_sig)    info('MX', 7)
  if (r.gog_sig)   bull('GOG', 7)
  if (r.rs_strong) bull('RS+', 7)
  else if (r.rs)   bull('RS', 6)
  if (r.fly_abcd)  bull('ABCD', 8)
  if (r.sig_l88)   bull('L88', 6)

  // TZ transitions
  if (r.tz_bull_flip) bull('TZ→3', 8)
  else if (r.tz_attempt) bull('TZ→2', 7)

  // Wyckoff
  if (r.ns) info('NS', 6)
  if (r.sq) info('SQ', 5)

  // L-signals
  if (r.fri34) bull('FRI34', 7)
  else if (r.fri43) bull('FRI43', 7)
  if (r.l34 && !r.fri34) bull('L34', 6)
  if (r.be_up) bull('BE↑', 6)

  // Breakout
  if (r.best_long)  bull('BEST↑', 9)
  else if (r.fbo_bull) bull('FBO↑', 7)
  if (r.x2g_wick) bull('X2G', 8)

  // PREUP
  if (r.preup66)      bull('P66', 6)
  else if (r.preup55) bull('P55', 5)
  else if (r.preup89) bull('P89', 5)
  else if (r.preup3)  bull('P3', 4)

  // B signals (first ones only)
  for (let i = 1; i <= 11; i++) {
    if (r[`b${i}`]) { bull(`B${i}`, 4); break }
  }

  // G signals
  for (const k of ['g1','g2','g4','g6','g11']) {
    if (r[k]) { bull(k.toUpperCase(), 4); break }
  }

  sigs.sort((a, b) => b.priority - a.priority)
  return sigs
}

// ── Small neutral badge (used outside Signals column) ─────────────────────────
function SmallBadge({ label, cls = '' }) {
  return (
    <span className={`text-[10px] px-1 py-0.5 rounded bg-md-surface-high text-md-on-surface-var ${cls}`}>
      {label}
    </span>
  )
}

// ── StarBtn ───────────────────────────────────────────────────────────────────
function StarBtn({ ticker, tf, onToggle }) {
  const [saved, setSaved] = useState(() => pwlHas(ticker, tf))
  return (
    <button
      title={saved ? 'Remove from watchlist' : 'Save to watchlist'}
      className={`text-sm transition-colors ${saved ? 'text-yellow-400' : 'text-gray-700 hover:text-yellow-400'}`}
      onClick={e => {
        e.stopPropagation()
        onToggle?.()
        setSaved(s => !s)
      }}>
      ★
    </button>
  )
}

// ── Row expansion detail panel ────────────────────────────────────────────────
function ExpandedRow({ r, colSpan }) {
  const allSigs = collectSignals(r)
  const ctx = ctxTokens(r)

  return (
    <tr className="bg-md-surface-con/80 border-b border-white/[0.06]">
      <td colSpan={colSpan} className="px-4 py-3">
        <div className="grid grid-cols-2 gap-4 text-[11px] md:grid-cols-3 lg:grid-cols-4">

          {/* All signals */}
          <div>
            <div className="text-md-on-surface-var/70 mb-1 font-semibold uppercase tracking-wide text-[9px]">All Signals</div>
            <SignalChipList signals={allSigs.map(s => s.label)} mode="table" />
          </div>

          {/* GOG / Context */}
          {(r.gog_tier || ctx.length > 0 || (r.signal_score ?? 0) > 0) && (
            <div>
              <div className="text-md-on-surface-var/70 mb-1 font-semibold uppercase tracking-wide text-[9px]">GOG / Context</div>
              <div className="flex flex-wrap gap-0.5">
                {r.gog_tier && (
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${gogTierCls(r.gog_tier)}`}>{r.gog_tier}</span>
                )}
                {ctx.map(tok => (
                  <span key={tok} className={`text-[10px] px-1 py-0.5 rounded ${ctxTokCls(tok)}`}>{tok}</span>
                ))}
                {(r.signal_score ?? 0) > 0 && (
                  <span className={`font-mono text-[10px] ${scoreCls(r.signal_score)}`}>{r.signal_score}</span>
                )}
              </div>
            </div>
          )}

          {/* Score breakdown */}
          <div>
            <div className="text-md-on-surface-var/70 mb-1 font-semibold uppercase tracking-wide text-[9px]">Score Breakdown</div>
            <div className="space-y-0.5 text-md-on-surface-var">
              {r.turbo_score != null && (
                <div>Turbo: <span className={scoreColor(r.turbo_score)}>{fmt(r.turbo_score, 1)}</span></div>
              )}
              {r.ultra_score != null && (
                <div>Ultra: <span className={ultraScoreCls(r.ultra_score)}>{r.ultra_score}</span>
                  {' '}<span className="text-md-on-surface-var/60 text-[9px]">
                    {r.ultra_score_band_v2 || ultraBandV2Label(r.ultra_score, r.ultra_score_band) || ''}
                  </span>
                </div>
              )}
              {r.beta_score > 0 && (
                <div>Beta: <span className={betaZoneCls(r.beta_zone)}>{r.beta_score} {r.beta_zone}</span></div>
              )}
              {r.rtb_phase && r.rtb_phase !== '0' && (
                <div>RTB: Phase {r.rtb_phase} · {(r.rtb_total ?? 0).toFixed(0)}</div>
              )}
              {r.profile_score != null && (
                <div>Profile: {r.profile_score} {r.profile_category || ''}</div>
              )}
            </div>
          </div>

          {/* EMA levels */}
          <div>
            <div className="text-md-on-surface-var/70 mb-1 font-semibold uppercase tracking-wide text-[9px]">EMA Levels</div>
            <div className="space-y-0.5 font-mono text-md-on-surface-var">
              {r.ema20  > 0 && <div>EMA20: ${fmt(r.ema20)}</div>}
              {r.ema50  > 0 && <div>EMA50: ${fmt(r.ema50)}</div>}
              {r.ema89  > 0 && <div>EMA89: ${fmt(r.ema89)}</div>}
              {r.ema200 > 0 && <div>EMA200: ${fmt(r.ema200)}</div>}
            </div>
          </div>

          {/* RSI / CCI / Volume */}
          <div>
            <div className="text-md-on-surface-var/70 mb-1 font-semibold uppercase tracking-wide text-[9px]">Indicators</div>
            <div className="space-y-0.5 font-mono text-md-on-surface-var">
              <div>RSI: <span className={r.rsi <= 35 ? 'text-lime-400' : r.rsi >= 70 ? 'text-red-400' : ''}>{r.rsi != null ? fmt(r.rsi, 0) : '—'}</span></div>
              <div>CCI: <span className={r.cci >= 100 ? 'text-lime-400' : r.cci <= -100 ? 'text-red-400' : ''}>{r.cci != null ? fmt(r.cci, 0) : '—'}</span></div>
              {r.avg_vol > 0 && (
                <div>Vol: {r.avg_vol >= 1_000_000 ? `${(r.avg_vol/1_000_000).toFixed(1)}M`
                  : r.avg_vol >= 1_000 ? `${Math.round(r.avg_vol/1_000)}K`
                  : Math.round(r.avg_vol)}</div>
              )}
            </div>
          </div>

          {/* ABR / ULTRA enrichment (if present) */}
          {r.abr?.category && (
            <div>
              <div className="text-md-on-surface-var/70 mb-1 font-semibold uppercase tracking-wide text-[9px]">ABR</div>
              <div className="space-y-0.5 text-md-on-surface-var">
                <div>Category: <span className={
                  r.abr.category === 'A'  ? 'text-emerald-300' :
                  r.abr.category === 'B+' ? 'text-cyan-300' :
                  r.abr.category === 'B'  ? 'text-blue-300' :
                  r.abr.category === 'R'  ? 'text-red-400' : ''
                }>{r.abr.category}</span></div>
                {r.abr.med10d_pct  != null && <div>Med10d: {r.abr.med10d_pct}</div>}
                {r.abr.fail10d_pct != null && <div>Fail10d: {r.abr.fail10d_pct}</div>}
                {r.abr.action_hint && <div>Hint: {r.abr.action_hint}</div>}
              </div>
            </div>
          )}

          {/* Pullback */}
          {r.pullback?.evidence_tier && (
            <div>
              <div className="text-md-on-surface-var/70 mb-1 font-semibold uppercase tracking-wide text-[9px]">Pullback</div>
              <div className="text-md-on-surface-var space-y-0.5">
                <div>{r.pullback.evidence_tier}</div>
                {r.pullback.pattern_key && <div>Key: {r.pullback.pattern_key}</div>}
                {r.pullback.score != null && <div>Score: {r.pullback.score}</div>}
                {r.pullback.median_10d_return != null && <div>Med10d: {r.pullback.median_10d_return}</div>}
              </div>
            </div>
          )}

          {/* Rare Reversal */}
          {r.rare_reversal?.evidence_tier && (
            <div>
              <div className="text-md-on-surface-var/70 mb-1 font-semibold uppercase tracking-wide text-[9px]">Rare Reversal</div>
              <div className="text-md-on-surface-var space-y-0.5">
                <div>{r.rare_reversal.evidence_tier}</div>
                {r.rare_reversal.base4_key && <div>Key: {r.rare_reversal.base4_key}</div>}
                {r.rare_reversal.score != null && <div>Score: {r.rare_reversal.score}</div>}
                {r.rare_reversal.median_10d_return != null && <div>Med10d: {r.rare_reversal.median_10d_return}</div>}
              </div>
            </div>
          )}

        </div>
      </td>
    </tr>
  )
}

// ── Skeleton loading row ───────────────────────────────────────────────────────
function SkeletonRow({ colSpan }) {
  return (
    <tr className="border-b border-white/[0.06] animate-pulse">
      {Array.from({ length: colSpan }).map((_, i) => (
        <td key={i} className="px-2 py-2">
          <div className="h-3 bg-md-surface-high/60 rounded" style={{ width: i === 2 ? '70%' : i === 3 ? '40%' : '60%' }} />
        </td>
      ))}
    </tr>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// MAIN COMPONENT
// ─────────────────────────────────────────────────────────────────────────────

/**
 * ScannerDataGrid
 *
 * Props:
 *   results        - array of scanner rows (already filtered + sorted by parent)
 *   onSelectTicker - fn(ticker) called when row is clicked
 *   onWatchlistToggle - fn(row) called when star is clicked
 *   localTf        - current timeframe string (for watchlist key)
 *   pickedTickers  - Set of selected ticker strings
 *   onTogglePicked - fn(ticker, e) toggle row checkbox
 *   sortBy         - current sort column key
 *   sortDir        - 'asc' | 'desc'
 *   onSort         - fn(col) toggle sort
 *   isLoading      - bool: show skeleton
 *   error          - string: show error state
 *   effectiveScoreCol - column key to use for score display
 *   universe       - current universe key (for split column)
 *   variant        - 'ultra' | 'turbo' (controls extra columns)
 *   onPickAll      - fn(checked) select/deselect all
 *   allPicked      - bool: header checkbox state
 */
export default function ScannerDataGrid({
  results = [],
  onSelectTicker,
  onWatchlistToggle,
  localTf = '1d',
  pickedTickers = new Set(),
  onTogglePicked,
  sortBy,
  sortDir,
  onSort,
  isLoading = false,
  error = null,
  effectiveScoreCol = 'turbo_score',
  universe = 'sp500',
  variant = 'turbo',
  onPickAll,
  allPicked = false,
  handleRowEnter,
  handleRowLeave,
}) {
  const [expandedTicker, setExpandedTicker] = useState(null)

  const toggleExpand = (ticker) => {
    setExpandedTicker(prev => prev === ticker ? null : ticker)
  }

  // Number of columns for colSpan calculation
  const baseColCount = variant === 'ultra' ? 18 : 16
  const colCount = universe === 'split' ? baseColCount + 1 : baseColCount

  const SortTh = ({ col, children, cls = '' }) => (
    <th
      className={`px-2 py-1.5 font-medium cursor-pointer select-none hover:text-white transition-colors whitespace-nowrap ${cls}`}
      onClick={() => onSort?.(col)}>
      {children}{sortBy === col ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''}
    </th>
  )

  return (
    <div className="overflow-auto flex-1">
      <table className="w-full border-collapse text-xs">
        <thead className="sticky top-0 z-10 bg-md-surface-con text-md-on-surface-var text-left [&>tr>th]:shadow-[0_1px_0_0_rgba(255,255,255,0.07)]">
          <tr>
            {/* Checkbox col */}
            <th className="px-2 py-1.5 w-5 sticky left-0 z-20 bg-md-surface-con">
              <input type="checkbox" className="accent-indigo-500 cursor-pointer"
                title="Select/deselect all visible"
                checked={allPicked}
                onChange={e => onPickAll?.(e.target.checked)} />
            </th>
            {/* Star col */}
            <th className="px-1 py-1.5 w-5 sticky left-[28px] z-20 bg-md-surface-con font-medium text-md-on-surface-var" title="Watchlist">★</th>
            {/* Ticker */}
            <SortTh col="ticker" cls="sticky left-[52px] z-20 bg-md-surface-con min-w-[72px] max-w-[100px]">Ticker</SortTh>
            {/* Score */}
            <SortTh col="turbo_score" cls="text-right min-w-[50px]">Score</SortTh>
            {/* ULTRA Score — only in ultra variant */}
            {variant === 'ultra' && (
              <SortTh col="ultra_score" cls="text-right min-w-[50px]" title="ULTRA Score — independent confluence ranking">ULTRA</SortTh>
            )}
            {/* BETA */}
            <SortTh col="beta_score" cls="text-center min-w-[60px]" title="BETA Score — non-linear quality rank">BETA</SortTh>
            {/* RTB */}
            <SortTh col="rtb_total" cls="text-center min-w-[40px]">RTB</SortTh>
            {/* T/Z */}
            <SortTh col="tz_sig" cls="text-center min-w-[44px]">T/Z</SortTh>
            {/* Category */}
            <th className="px-2 py-1.5 font-medium text-center min-w-[60px]">Cat</th>
            {/* Signals — all chips shown, may wrap or grow row height */}
            <th className="px-2 py-1.5 font-medium min-w-[180px]">Signals</th>
            {/* ABR */}
            <th className="px-2 py-1.5 font-medium text-center min-w-[36px]">ABR</th>
            {/* RSI */}
            <SortTh col="rsi" cls="text-right min-w-[36px]">RSI</SortTh>
            {/* CCI */}
            <SortTh col="cci" cls="text-right min-w-[44px]">CCI</SortTh>
            {/* Price */}
            <SortTh col="last_price" cls="text-right min-w-[64px]">Price</SortTh>
            {/* % */}
            <SortTh col="change_pct" cls="text-right min-w-[52px]">%</SortTh>
            {/* Split (only for split universe) */}
            {universe === 'split' && (
              <th className="px-2 py-1.5 font-medium text-amber-300 min-w-[64px]" title="Split ratio + phase">Split</th>
            )}
          </tr>
        </thead>

        <tbody>
          {/* Loading skeleton */}
          {isLoading && results.length === 0 && (
            Array.from({ length: 8 }).map((_, i) => (
              <SkeletonRow key={i} colSpan={colCount} />
            ))
          )}

          {/* Error state */}
          {error && !isLoading && (
            <tr>
              <td colSpan={colCount} className="px-4 py-8 text-center text-red-400">
                {error}
              </td>
            </tr>
          )}

          {/* Empty state */}
          {!isLoading && !error && results.length === 0 && (
            <tr>
              <td colSpan={colCount} className="px-4 py-10 text-center text-md-on-surface-var">
                No tickers match current filters. Try relaxing filters.
              </td>
            </tr>
          )}

          {/* Data rows */}
          {results.map((r, rowIdx) => {
            const sc = r[effectiveScoreCol] ?? r.turbo_score ?? 0
            const isExpanded = expandedTicker === r.ticker
            const isEven = rowIdx % 2 === 0
            const rowBg = isEven ? 'bg-md-surface-con' : ''

            // Collect all signals — every chip is shown in the Signals column.
            const allSigs = collectSignals(r)

            const chg = r.change_pct ?? 0

            return [
              <tr key={r.ticker}
                className={`border-b border-white/[0.06] cursor-pointer transition-colors
                  hover:bg-white/5
                  ${rowBg}
                  ${profileBorderCls(r.profile_category)}`}
                onClick={() => {
                  toggleExpand(r.ticker)
                  onSelectTicker?.(r.ticker)
                }}
                onMouseEnter={handleRowEnter ? (e => handleRowEnter(e, r)) : undefined}
                onMouseLeave={handleRowLeave || undefined}
              >

                {/* Checkbox */}
                <td className={`px-2 py-1 w-5 sticky left-0 z-10 ${isEven ? 'bg-md-surface-con' : 'bg-md-surface'}`}
                  onClick={e => e.stopPropagation()}>
                  <input type="checkbox" className="accent-indigo-500 cursor-pointer"
                    checked={pickedTickers.has(r.ticker)}
                    onChange={e => onTogglePicked?.(r.ticker, e)} />
                </td>

                {/* Star */}
                <td className={`px-1 py-1 w-5 sticky left-[28px] z-10 ${isEven ? 'bg-md-surface-con' : 'bg-md-surface'}`}>
                  <StarBtn ticker={r.ticker} tf={localTf} onToggle={() => onWatchlistToggle?.(r)} />
                </td>

                {/* Ticker — compact symbol-only cell. Sector lives in tooltip + expanded row. */}
                <td className={`px-2 py-1 sticky left-[52px] z-10 ${isEven ? 'bg-md-surface-con' : 'bg-md-surface'}`}
                    title={[r.ticker, r.sector || r.vol_bucket].filter(Boolean).join(' · ')}>
                  <TickerCell
                    symbol={r.ticker}
                    company={r.company || r.name}
                    sector={r.sector || r.vol_bucket}
                    compact
                  />
                  {r.data_source === 'yfinance' && (
                    <span className="text-[8px] text-orange-400/60 ml-0.5">yf</span>
                  )}
                </td>

                {/* Score */}
                <td className="px-2 py-1 text-right" title={`Score: ${sc}`}>
                  <span className={`font-mono text-xs ${scoreColor(sc)}`}>{fmt(sc, 0)}</span>
                </td>

                {/* ULTRA Score */}
                {variant === 'ultra' && (
                  <td className="px-2 py-1 text-right"
                    title={r.ultra_score_reasons || (r.ultra_score != null ? `ULTRA ${r.ultra_score}` : '')}>
                    {r.ultra_score != null ? (
                      <span className={`font-mono text-xs ${ultraScoreCls(r.ultra_score)}`}>{r.ultra_score}</span>
                    ) : <span className="text-gray-700">—</span>}
                  </td>
                )}

                {/* BETA */}
                <td className="px-2 py-1 text-center"
                  title={r.beta_zone ? `BETA ${r.beta_score} · ${r.beta_zone}${r.beta_auto_buy ? ' ★AUTO-BUY' : ''}` : 'No BETA data'}>
                  {r.beta_score > 0 ? (
                    <div className="leading-none">
                      <span className={`font-mono text-xs ${betaZoneCls(r.beta_zone)}`}>
                        {r.beta_auto_buy ? '★ ' : ''}{r.beta_score}
                      </span>
                      <div className={`text-[9px] ${betaZoneCls(r.beta_zone)} opacity-80`}>{r.beta_zone}</div>
                    </div>
                  ) : <span className="text-gray-700">—</span>}
                </td>

                {/* RTB */}
                <td className="px-2 py-1 text-center"
                  title={r.rtb_phase ? `RTB Phase ${r.rtb_phase} · ${(r.rtb_total ?? 0).toFixed(0)}` : ''}>
                  {r.rtb_phase && r.rtb_phase !== '0' ? (
                    <div className="leading-none">
                      <span className={`inline-block font-bold text-[10px] px-1 rounded ${
                        r.rtb_phase === 'C' ? 'bg-lime-700/80 text-lime-100 ring-1 ring-lime-400' :
                        r.rtb_phase === 'B' ? 'bg-sky-800/80 text-sky-200 ring-1 ring-sky-500' :
                        r.rtb_phase === 'A' ? 'bg-gray-700 text-md-on-surface' :
                        'bg-orange-800/70 text-orange-200'
                      }`}>{r.rtb_phase}</span>
                    </div>
                  ) : <span className="text-gray-700">—</span>}
                </td>

                {/* T/Z */}
                <td className="px-2 py-1 text-center">
                  {r.tz_sig ? (
                    <TZChip label={r.tz_sig} />
                  ) : <span className="text-gray-700">—</span>}
                </td>

                {/* Category */}
                <td className="px-2 py-1 text-center">
                  {r.profile_category ? (
                    <StatusChip cat={r.profile_category} />
                  ) : <span className="text-gray-700">—</span>}
                </td>

                {/* Signals — every chip rendered, wraps inside cell. No static "+N more". */}
                <td className="px-2 py-1 align-middle">
                  <SignalChipList signals={allSigs.map(s => s.label)} mode="table" />
                </td>

                {/* ABR */}
                <td className="px-2 py-1 text-center">
                  {r.abr?.category && r.abr.category !== 'UNKNOWN' ? (
                    <span className={`text-[10px] font-mono ${
                      r.abr.category === 'A'  ? 'text-emerald-300' :
                      r.abr.category === 'B+' ? 'text-cyan-300' :
                      r.abr.category === 'B'  ? 'text-blue-300' :
                      r.abr.category === 'R'  ? 'text-red-400' :
                      'text-md-on-surface-var'
                    }`}>{r.abr.category}</span>
                  ) : <span className="text-[10px] text-md-on-surface-var/50 italic">n/a</span>}
                </td>

                {/* RSI */}
                <td className={`px-2 py-1 text-right font-mono text-xs ${
                  r.rsi <= 35 ? 'text-lime-400' : r.rsi >= 70 ? 'text-red-400' : 'text-md-on-surface-var'
                }`}>
                  {r.rsi != null ? fmt(r.rsi, 0) : '—'}
                </td>

                {/* CCI */}
                <td className={`px-2 py-1 text-right font-mono text-xs ${
                  r.cci >= 100 ? 'text-lime-400' : r.cci <= -100 ? 'text-red-400' : 'text-md-on-surface-var'
                }`}>
                  {r.cci != null ? fmt(r.cci, 0) : '—'}
                </td>

                {/* Price */}
                <td className="px-2 py-1 text-right font-mono text-xs text-md-on-surface">
                  ${fmt(r.last_price)}
                </td>

                {/* % change */}
                <td className={`px-2 py-1 text-right font-mono text-xs ${chg >= 0 ? 'text-lime-400' : 'text-red-400'}`}>
                  {chg >= 0 ? '+' : ''}{fmt(chg)}%
                </td>

                {/* Split */}
                {universe === 'split' && (
                  <td className="px-2 py-1 text-center font-mono text-xs">
                    {r.split_date ? (() => {
                      const ph = r.split_phase || ''
                      const wave = r.split_wave || ''
                      const doff = r.split_days_offset ?? 0
                      const dLabel = doff === 0 ? 'D0' : doff > 0 ? `D+${doff}` : `D${doff}`
                      return (
                        <span className="text-md-on-surface-var">
                          {r.split_ratio} <span className="opacity-60">{wave}</span> <span>{dLabel}</span>
                        </span>
                      )
                    })() : '—'}
                  </td>
                )}
              </tr>,

              // Expanded detail row
              isExpanded && (
                <ExpandedRow key={`${r.ticker}-expand`} r={r} colSpan={colCount} />
              )
            ]
          })}
        </tbody>
      </table>
    </div>
  )
}

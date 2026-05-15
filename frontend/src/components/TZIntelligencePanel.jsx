import { useState, useRef, useMemo, useEffect } from 'react'
import { createPortal } from 'react-dom'

const BASE = import.meta.env.VITE_API_URL || ''
const TOOLTIP_W = 520

const CSV_COLS = [
  'ticker','date','bar_datetime','close','volume',
  'final_signal','composite_pattern','seq4','lane1','lane3',
  'role','score','quality','action',
  'vol_bucket','wick_suffix',
  'above_ema20','above_ema50','above_ema89',
  'ema20_reclaim','ema50_reclaim','ema89_reclaim',
  'conflict_flag','conflict_resolution','conflicting_rule_ids',
  'good_flags','reject_flags',
  'price_position_4bar','breaks_4bar_high','breaks_4bar_low',
  'final_volume_vs_prev1','final_volume_vs_prev2','final_volume_vs_prev3',
  'matched_rule_id','matched_rule_type','matched_universe','matched_status',
  'matched_med10d_pct','matched_fail10d_pct','matched_avg10d_pct',
  'matched_source_file','matched_rule_notes',
  'matched_composite_rule_id','matched_seq4_rule_id','matched_reject_rule_id',
  // PULLBACK_GO proof fields
  'prior_pullback_ready_found','prior_pullback_ready_bars_ago',
  'prior_pullback_ready_signal','prior_pullback_ready_composite',
  'prior_pullback_ready_role','pullback_high','current_close_above_pullback_high',
  // Liquidity fields
  'dollar_volume','liquidity_tier',
  'explanation',
  // ABR overlay fields
  'abr_category','abr_sequence','abr_prev1_composite','abr_prev2_composite',
  'abr_prev1_comp_med10d','abr_prev2_comp_med10d',
  'abr_prev1_quality','abr_prev2_quality','abr_gate_pass','abr_rule_found',
  'abr_n','abr_med10d_pct','abr_avg10d_pct','abr_fail10d_pct','abr_win10d_pct',
  'abr_action_hint','abr_role_suggestion',
  'abr_conflict_flag','abr_confirmation_flag','abr_context_type',
]

function exportCSV(rows, universe, tf, batch = '', scanMode = 'latest') {
  const lines = [CSV_COLS.join(',')]
  for (const r of rows) {
    lines.push(CSV_COLS.map(c => {
      let v = r[c] ?? ''
      if (Array.isArray(v)) v = v.join(';')
      v = String(v)
      // Neutralise CSV formula injection (Excel/Sheets execute =, +, -, @ as formulas)
      if (/^[=+\-@]/.test(v)) v = "'" + v
      return v.includes(',') || v.includes('"') || v.includes('\n')
        ? `"${v.replace(/"/g, '""')}"` : v
    }).join(','))
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  const batchPart = batch ? `_${batch}` : ''
  a.download = `tz_intelligence_${universe}${batchPart}_${tf}_${scanMode}_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

async function apiGet(path) {
  const res = await fetch(BASE + path)
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json()
}

// ── Sort helpers ──────────────────────────────────────────────────────────────

const ROLE_SORT_ORDER = {
  BULL_A: 1, BULL_CONTINUATION_A: 2, BULL_B: 3, BULL_CONTINUATION_B: 4,
  PULLBACK_GO: 5, PULLBACK_CONFIRMING: 6,
  PULLBACK_READY_A: 7, PULLBACK_READY_B: 8,
  PULLBACK_WATCH: 9, EXTENDED_WATCH: 10,
  DEEP_PULLBACK_WATCH: 11, MIXED_WATCH: 12, BULL_WATCH: 13,
  SHORT_WATCH: 14, SHORT_GO: 15,
  REJECT_LONG: 16, REJECT: 17, NO_EDGE: 18,
}
const QUALITY_SORT_ORDER = { A: 1, B: 2, Watch: 3, Reject: 4, '—': 5 }
const RULE_TYPE_SHORT = {
  COMPOSITE: 'COMP', REJECT_COMPOSITE: 'R-COMP',
  SEQ4: 'SEQ4', REJECT_SEQ4: 'R-SEQ4', BASELINE: 'BASE',
}

function getSortValue(row, key) {
  switch (key) {
    case 'close':               return parseFloat(row.close) || 0
    case 'score':               return parseFloat(row.score) || 0
    case 'price_position_4bar': return parseFloat(row.price_position_4bar) || 0
    case 'matched_med10d_pct':  return parseFloat(row.matched_med10d_pct) || -999
    case 'matched_fail10d_pct': return parseFloat(row.matched_fail10d_pct) || 9999
    case 'abr_med10d_pct':      return parseFloat(row.abr_med10d_pct) || -999
    case 'abr_fail10d_pct':     return parseFloat(row.abr_fail10d_pct) || 9999
    case 'abr_category': {
      const o = { 'B+': 0, 'B': 1, 'A': 2, 'R': 3, 'UNKNOWN': 4 }
      return o[row.abr_category] ?? 99
    }
    case 'role':                return ROLE_SORT_ORDER[row.role] ?? 99
    case 'quality':             return QUALITY_SORT_ORDER[row.quality] ?? 99
    case 'ema':
      return (row.above_ema20 ? 1 : 0) + (row.above_ema50 ? 1 : 0) + (row.above_ema89 ? 1 : 0)
    default:
      return String(row[key] ?? '').toLowerCase()
  }
}

function defaultSort(rows) {
  return [...rows].sort((a, b) => {
    const rp = (ROLE_SORT_ORDER[a.role] ?? 99) - (ROLE_SORT_ORDER[b.role] ?? 99)
    if (rp !== 0) return rp
    const sd = (parseFloat(b.score) || 0) - (parseFloat(a.score) || 0)
    if (sd !== 0) return sd
    return (a.ticker ?? '').localeCompare(b.ticker ?? '')
  })
}

// ── Constants ─────────────────────────────────────────────────────────────────

const UNIVERSES = [
  { key: 'sp500',      label: 'S&P 500'     },
  { key: 'nasdaq',     label: 'NASDAQ'      },
  { key: 'nasdaq_gt5', label: 'NASDAQ > $5' },
  { key: 'russell2k',  label: 'Russell 2K'  },
  { key: 'all_us',     label: 'All US'      },
  { key: 'split',      label: '✂️ SPLIT'    },
]

const NASDAQ_BATCHES = [
  { key: 'a_m', label: 'A–M (½)' },
  { key: 'n_z', label: 'N–Z (½)' },
]

const NASDAQ_GT5_BATCHES = [
  { key: 'a_f', label: 'A–F' },
  { key: 'g_m', label: 'G–M' },
  { key: 'n_s', label: 'N–S' },
  { key: 't_z', label: 'T–Z' },
]

const TF_OPTS = ['1d', '4h', '1h', '1wk']

const ROLES = [
  { key: 'all',                    label: 'All'                  },
  { key: 'BULL_A',                 label: '🟢 BULL A'            },
  { key: 'BULL_CONTINUATION_A',    label: '🟢 Continuation A'    },
  { key: 'BULL_B',                 label: '🟩 BULL B'            },
  { key: 'BULL_CONTINUATION_B',    label: '🟩 Continuation B'    },
  { key: 'PULLBACK_GO',            label: '🚀 PB GO'             },
  { key: 'PULLBACK_CONFIRMING',    label: '⏳ PB Confirming'     },
  { key: 'PULLBACK_READY_A',       label: '🔵 PB Ready A'        },
  { key: 'PULLBACK_READY_B',       label: '🔵 PB Ready B'        },
  { key: 'PULLBACK_WATCH',         label: '🔷 PB Watch'          },
  { key: 'EXTENDED_WATCH',         label: '⚡ Extended Watch'    },
  { key: 'DEEP_PULLBACK_WATCH',    label: '⬇ Deep PB'            },
  { key: 'BULL_WATCH',             label: '👀 Bull Watch'        },
  { key: 'MIXED_WATCH',            label: '⚖ Mixed Watch'       },
  { key: 'SHORT_WATCH',            label: '🔴 Short Watch'       },
  { key: 'SHORT_GO',               label: '🔴 SHORT GO'          },
  { key: 'REJECT_LONG',            label: '🚫 Reject Long'       },
  { key: 'REJECT',                 label: '❌ Reject'            },
]

const ROLE_COLORS = {
  BULL_A:               'bg-green-800/60 text-green-200 border-green-600/50',
  BULL_CONTINUATION_A:  'bg-teal-700/60 text-teal-100 border-teal-500/60',
  BULL_B:               'bg-green-900/50 text-green-300 border-green-700/40',
  BULL_CONTINUATION_B:  'bg-teal-900/50 text-teal-300 border-teal-700/40',
  EXTENDED_WATCH:       'bg-amber-900/50 text-amber-300 border-amber-700/40',
  PULLBACK_GO:          'bg-indigo-700/60 text-indigo-100 border-indigo-500/60',
  PULLBACK_CONFIRMING:  'bg-indigo-900/50 text-indigo-300 border-indigo-700/40',
  PULLBACK_READY_A:     'bg-blue-800/60 text-blue-200 border-blue-600/50',
  PULLBACK_READY_B:     'bg-blue-900/50 text-blue-300 border-blue-700/40',
  PULLBACK_WATCH:       'bg-cyan-900/50 text-cyan-300 border-cyan-700/40',
  DEEP_PULLBACK_WATCH:  'bg-slate-800/60 text-slate-400 border-slate-600/40',
  BULL_WATCH:           'bg-teal-900/50 text-teal-300 border-teal-700/40',
  MIXED_WATCH:          'bg-yellow-900/50 text-yellow-300 border-yellow-700/40',
  SHORT_WATCH:          'bg-red-900/50 text-red-300 border-red-700/40',
  SHORT_GO:             'bg-red-700/70 text-red-100 border-red-500/60',
  REJECT_LONG:          'bg-orange-900/50 text-orange-300 border-orange-700/40',
  REJECT:               'bg-md-surface-high/60 text-md-on-surface-var border-gray-600/40',
  NO_EDGE:              'bg-md-surface-con/40 text-md-on-surface-var/70 border-md-outline-var/30',
}

const QUALITY_COLORS = {
  A:      'text-green-300',
  B:      'text-teal-300',
  Watch:  'text-yellow-400',
  Reject: 'text-md-on-surface-var',
  '—':    'text-md-on-surface-var/70',
}

const ABR_COLORS = {
  'A':       'bg-green-800/60 text-green-200 border-green-600/50',
  'B':       'bg-green-900/40 text-green-400 border-green-700/40',
  'B+':      'bg-teal-700/60 text-teal-100 border-teal-500/50',
  'R':       'bg-red-900/40 text-red-400 border-red-700/40',
  'UNKNOWN': 'bg-md-surface-con/30 text-md-on-surface-var/70 border-md-outline-var/20',
}

const ABR_QUALITY_COLORS = {
  STRONG:  'text-green-400',
  GOOD:    'text-teal-400',
  AVERAGE: 'text-yellow-500',
  REJECT:  'text-red-400',
  UNKNOWN: 'text-md-on-surface-var/70',
}

const ABR_HINT_SHORT = {
  PRIMARY_LONG_CONTEXT:            'PRIMARY',
  SECONDARY_LONG_CONTEXT:          'SECONDARY',
  MOMENTUM_CONTINUATION_CONTEXT:   'MOMENTUM',
  DO_NOT_BUY_OR_SHORT_WATCH_IF_NEGATIVE: 'NO-BUY',
  NO_ABR_EDGE:                     '—',
}

const ABR_FILTER_OPTS = [
  { key: 'all',                          label: 'All ABR'              },
  { key: 'B+',                           label: '🟩 B+'                },
  { key: 'B',                            label: '🟢 B'                 },
  { key: 'A',                            label: '🌿 A'                 },
  { key: 'R',                            label: '🔴 R'                 },
]

const ABR_CTX_FILTER_OPTS = [
  { key: 'all',                            label: 'All Context'                   },
  { key: 'ABR_PULLBACK_CONFIRMED',         label: '✓ PB Confirmed'               },
  { key: 'ABR_BULLISH_CONTEXT_CONFLICT',   label: '⚠ Short Conflict'             },
  { key: 'ABR_SHORT_CONFIRMED',            label: '✓ Short Confirmed'            },
  { key: 'BULL_CONTINUATION_CANDIDATE',    label: '🚀 Continuation Candidate'    },
]

const ACTION_COLORS = {
  BUY_TRIGGER:               'text-green-400 font-semibold',
  WAIT_FOR_T_CONFIRMATION:   'text-teal-400',
  PULLBACK_ENTRY_READY:      'text-indigo-300 font-semibold',
  WATCH_PULLBACK:            'text-blue-300',
  WAIT_FOR_CONFIRMATION:     'text-md-on-surface-var',
  WAIT_FOR_BREAKDOWN:        'text-orange-400',
  SHORT_TRIGGER:             'text-red-400 font-semibold',
  DO_NOT_BUY:                'text-orange-500',
  IGNORE:                    'text-md-on-surface-var/70',
}

// ── Sub-components ────────────────────────────────────────────────────────────

function RoleBadge({ role }) {
  const color = ROLE_COLORS[role] || 'bg-md-surface-high text-md-on-surface-var'
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-bold border ${color}`}>
      {role?.replace(/_/g, ' ') || '—'}
    </span>
  )
}

function ScoreBar({ score }) {
  const pct = Math.min(100, Math.max(0, ((score + 40) / 140) * 100))
  const color = score >= 80 ? 'bg-green-500' : score >= 60 ? 'bg-teal-500' : score >= 35 ? 'bg-yellow-500' : score >= 0 ? 'bg-gray-500' : 'bg-red-500'
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-14 h-1.5 bg-md-surface-high rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`text-xs font-mono ${score > 0 ? 'text-md-on-surface' : 'text-red-400'}`}>
        {score > 0 ? `+${score}` : score}
      </span>
    </div>
  )
}

function EMADots({ above20, above50, above89 }) {
  return (
    <div className="flex gap-0.5 text-xs">
      <span className={above20 ? 'text-green-400' : 'text-gray-700'} title="EMA20">20</span>
      <span className="text-gray-700">/</span>
      <span className={above50 ? 'text-green-400' : 'text-gray-700'} title="EMA50">50</span>
      <span className="text-gray-700">/</span>
      <span className={above89 ? 'text-green-400' : 'text-gray-700'} title="EMA89">89</span>
    </div>
  )
}

function SortTh({ label, colKey, sortKey, sortDir, onSort, align = 'left', className = '' }) {
  const active = sortKey === colKey
  return (
    <th
      className={`p-1 font-medium cursor-pointer select-none whitespace-nowrap
        text-${align} hover:text-md-on-surface transition-colors
        ${active ? 'text-blue-400' : 'text-md-on-surface-var'} ${className}`}
      onClick={() => onSort(colKey)}
    >
      {label}
      <span className={`ml-0.5 text-xs ${active ? 'text-blue-400' : 'opacity-20'}`}>
        {active ? (sortDir === 'asc' ? '▲' : '▼') : '⇅'}
      </span>
    </th>
  )
}

// Portal tooltip — opens LEFT of icon, stays inside viewport
function ReasonTooltip({ codes, explanation }) {
  const [pos, setPos] = useState(null)
  const btnRef = useRef(null)
  const timerRef = useRef(null)

  function showTooltip() {
    clearTimeout(timerRef.current)
    if (!btnRef.current) return
    const rect = btnRef.current.getBoundingClientRect()
    const margin = 8
    let left
    // Try left of icon first
    if (rect.left - TOOLTIP_W - margin >= 4) {
      left = rect.left - TOOLTIP_W - margin
    // Fallback: right of icon
    } else if (rect.right + TOOLTIP_W + margin <= window.innerWidth - 4) {
      left = rect.right + margin
    // Last resort: clamp to left edge
    } else {
      left = Math.max(4, rect.left - TOOLTIP_W)
    }
    const top = Math.max(4, Math.min(rect.top, window.innerHeight - 340))
    setPos({ left, top })
  }

  function hideTooltip() {
    timerRef.current = setTimeout(() => setPos(null), 120)
  }

  if (!codes?.length && !explanation) return null
  return (
    <>
      <button
        ref={btnRef}
        onMouseEnter={showTooltip}
        onMouseLeave={hideTooltip}
        className="text-md-on-surface-var hover:text-blue-400 text-xs px-1 transition-colors"
        aria-label="Show reason codes"
      >
        ℹ
      </button>
      {pos && createPortal(
        <div
          style={{
            position: 'fixed',
            left: pos.left,
            top: pos.top,
            zIndex: 9999,
            width: TOOLTIP_W,
            maxHeight: 340,
          }}
          className="bg-md-surface-con border border-md-outline-var rounded shadow-2xl overflow-y-auto"
          onMouseEnter={() => clearTimeout(timerRef.current)}
          onMouseLeave={hideTooltip}
        >
          <div className="p-3">
            {explanation && (
              <div className="mb-2 text-xs text-md-on-surface font-medium leading-relaxed border-b border-md-outline-var pb-2">
                {explanation}
              </div>
            )}
            <div className="space-y-0.5">
              {codes?.map((c, i) => (
                <div key={i} className="text-xs text-md-on-surface-var font-mono break-all leading-4">{c}</div>
              ))}
            </div>
          </div>
        </div>,
        document.body
      )}
    </>
  )
}

function AbrBadge({ category }) {
  const color = ABR_COLORS[category] || ABR_COLORS['UNKNOWN']
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-bold border ${color}`}>
      {category || '—'}
    </span>
  )
}

// ── Main panel ────────────────────────────────────────────────────────────────

export default function TZIntelligencePanel({ onSelectTicker }) {
  const [universe, setUniverse]       = useState('sp500')
  const [nasdaqBatch, setNasdaqBatch] = useState('a_m')
  const [gt5Batch, setGt5Batch]       = useState('')   // '' = full scan (no batch)
  const [tf, setTf]                   = useState('1d')
  const [scanMode, setScanMode]       = useState('latest')
  const [roleFilter, setRoleFilter]   = useState('all')
  const [abrFilter, setAbrFilter]     = useState('all')
  const [abrCtxFilter, setAbrCtxFilter] = useState('all')
  const [pbConfirmedOnly, setPbConfirmedOnly]   = useState(false)
  const [shortConflictOnly, setShortConflictOnly] = useState(false)
  const [minPrice, setMinPrice]       = useState('')
  const [maxPrice, setMaxPrice]       = useState('')
  const [minVolume, setMinVolume]     = useState('')

  const [results, setResults]           = useState([])
  const [total, setTotal]               = useState(0)
  const [scanDebug, setScanDebug]       = useState(null)  // debug counters from last scan
  const [loading, setLoading]           = useState(false)
  const [error, setError]               = useState(null)
  const [splitAudit, setSplitAudit]     = useState(null)
  const [splitAuditLoading, setSplitAuditLoading] = useState(false)

  // Sort state — null/null = default order
  const [sortKey, setSortKey] = useState(null)
  const [sortDir, setSortDir] = useState(null)

  // NASDAQ > $5 enforces min price = 5
  useEffect(() => {
    if (universe === 'nasdaq_gt5') {
      if (minPrice === '' || parseFloat(minPrice) < 5) {
        setMinPrice('5')
      }
    }
  }, [universe])

  // Reset gt5Batch when leaving nasdaq_gt5 4H mode
  useEffect(() => {
    if (universe !== 'nasdaq_gt5' || tf !== '4h') setGt5Batch('')
  }, [universe, tf])

  async function handleScan() {
    setLoading(true)
    setError(null)
    setSortKey(null)
    setSortDir(null)
    setPbConfirmedOnly(false)
    setShortConflictOnly(false)
    try {
      const qs = new URLSearchParams({ universe, tf, role_filter: roleFilter, scan_mode: scanMode })
      if (universe === 'nasdaq') qs.set('nasdaq_batch', nasdaqBatch)
      if (universe === 'nasdaq_gt5' && gt5Batch) qs.set('nasdaq_batch', gt5Batch)
      // nasdaq_gt5 always enforces min_price >= 5
      const effectiveMinPrice = universe === 'nasdaq_gt5'
        ? String(Math.max(5, parseFloat(minPrice) || 5))
        : minPrice
      if (effectiveMinPrice) qs.set('min_price', effectiveMinPrice)
      if (maxPrice)  qs.set('max_price', maxPrice)
      if (minVolume) qs.set('min_volume', minVolume)
      const data = await apiGet(`/api/tz-intelligence/scan?${qs}`)
      if (data.error) { setError(data.error); setResults([]) }
      else {
        setResults(data.results || [])
        setTotal(data.total || 0)
        setScanDebug(data.debug || null)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  function handleSort(key) {
    if (sortKey === key) {
      if (sortDir === 'asc') setSortDir('desc')
      else { setSortKey(null); setSortDir(null) }   // third click → reset
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  // Sorted rows — used for both table display and CSV export
  const displayRows = useMemo(() => {
    let filtered = results

    // ABR category filter
    if (abrFilter !== 'all')
      filtered = filtered.filter(r => r.abr_category === abrFilter)

    // ABR context filter (confirmation/conflict/suggestion)
    if (abrCtxFilter !== 'all') {
      filtered = filtered.filter(r =>
        r.abr_confirmation_flag === abrCtxFilter ||
        r.abr_conflict_flag     === abrCtxFilter ||
        r.abr_role_suggestion   === abrCtxFilter
      )
    }

    // Quick toggle: only ABR-confirmed pullbacks
    if (pbConfirmedOnly)
      filtered = filtered.filter(r =>
        ['PULLBACK_READY_B', 'PULLBACK_WATCH'].includes(r.role) &&
        r.abr_confirmation_flag === 'ABR_PULLBACK_CONFIRMED'
      )

    // Quick toggle: shorts with ABR bullish conflict
    if (shortConflictOnly)
      filtered = filtered.filter(r =>
        r.role === 'SHORT_WATCH' &&
        r.abr_conflict_flag === 'ABR_BULLISH_CONTEXT_CONFLICT'
      )

    if (!sortKey) return defaultSort(filtered)
    return [...filtered].sort((a, b) => {
      const va = getSortValue(a, sortKey)
      const vb = getSortValue(b, sortKey)
      const cmp = (typeof va === 'number' && typeof vb === 'number')
        ? va - vb
        : String(va).localeCompare(String(vb))
      return sortDir === 'desc' ? -cmp : cmp
    })
  }, [results, sortKey, sortDir, abrFilter, abrCtxFilter, pbConfirmedOnly, shortConflictOnly])

  const thProps = { sortKey, sortDir, onSort: handleSort }

  return (
    <div className="bg-md-surface text-md-on-surface p-3 flex flex-col gap-3">
      {/* Header */}
      <div className="flex items-center gap-2">
        <span className="text-lg font-bold text-white">🧠 TZ Signal Intelligence</span>
        <span className="text-xs text-md-on-surface-var">Matrix-based role classifier — BULL_A / SHORT_GO / REJECT / …</span>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap gap-2 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">Universe</label>
          <select value={universe} onChange={e => setUniverse(e.target.value)}
            className="bg-md-surface-high text-md-on-surface text-xs px-2 py-1 rounded border border-md-outline-var">
            {UNIVERSES.map(u => <option key={u.key} value={u.key}>{u.label}</option>)}
          </select>
        </div>

        {universe === 'nasdaq' && (
          <div className="flex flex-col gap-1">
            <label className="text-xs text-md-on-surface-var">Batch</label>
            <div className="flex gap-1">
              {NASDAQ_BATCHES.map(b => (
                <button key={b.key} onClick={() => setNasdaqBatch(b.key)}
                  className={`text-xs px-2 py-1 rounded transition-colors
                    ${nasdaqBatch === b.key ? 'bg-amber-600 text-white font-semibold' : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}>
                  {b.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {universe === 'nasdaq_gt5' && tf === '4h' && (
          <div className="flex flex-col gap-1">
            <label className="text-xs text-md-on-surface-var">Batch (4H)</label>
            <div className="flex gap-1">
              <button onClick={() => setGt5Batch('')}
                className={`text-xs px-2 py-1 rounded transition-colors
                  ${gt5Batch === '' ? 'bg-amber-600 text-white font-semibold' : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}>
                Full
              </button>
              {NASDAQ_GT5_BATCHES.map(b => (
                <button key={b.key} onClick={() => setGt5Batch(b.key)}
                  className={`text-xs px-2 py-1 rounded transition-colors
                    ${gt5Batch === b.key ? 'bg-amber-600 text-white font-semibold' : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}>
                  {b.label}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">Timeframe</label>
          <div className="flex gap-1">
            {TF_OPTS.map(t => (
              <button key={t} onClick={() => setTf(t)}
                className={`text-xs px-2 py-1 rounded transition-colors
                  ${tf === t ? 'bg-blue-600 text-white font-semibold' : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}>
                {t}
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">Mode</label>
          <div className="flex gap-1">
            {[{k:'latest',l:'Latest'},{k:'history',l:'History'}].map(({k,l}) => (
              <button key={k} onClick={() => setScanMode(k)}
                className={`text-xs px-2 py-1 rounded transition-colors
                  ${scanMode === k ? 'bg-indigo-600 text-white font-semibold' : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}>
                {l}
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">Role</label>
          <select value={roleFilter} onChange={e => setRoleFilter(e.target.value)}
            className="bg-md-surface-high text-md-on-surface text-xs px-2 py-1 rounded border border-md-outline-var">
            {ROLES.map(r => <option key={r.key} value={r.key}>{r.label}</option>)}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">ABR</label>
          <select value={abrFilter} onChange={e => setAbrFilter(e.target.value)}
            className="bg-md-surface-high text-md-on-surface text-xs px-2 py-1 rounded border border-md-outline-var">
            {ABR_FILTER_OPTS.map(o => <option key={o.key} value={o.key}>{o.label}</option>)}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">ABR Context</label>
          <select value={abrCtxFilter} onChange={e => setAbrCtxFilter(e.target.value)}
            className="bg-md-surface-high text-md-on-surface text-xs px-2 py-1 rounded border border-md-outline-var">
            {ABR_CTX_FILTER_OPTS.map(o => <option key={o.key} value={o.key}>{o.label}</option>)}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">Quick</label>
          <div className="flex gap-1 flex-wrap">
            <button
              onClick={() => { setPbConfirmedOnly(v => !v); setShortConflictOnly(false) }}
              title="PULLBACK_READY_B or PULLBACK_WATCH with ABR_PULLBACK_CONFIRMED"
              className={`text-xs px-2 py-1 rounded transition-colors whitespace-nowrap
                ${pbConfirmedOnly
                  ? 'bg-blue-700 text-white font-semibold border border-blue-500'
                  : 'bg-md-surface-high text-md-on-surface-var hover:text-white border border-md-outline-var'}`}>
              ✓ PB only
            </button>
            <button
              onClick={() => { setShortConflictOnly(v => !v); setPbConfirmedOnly(false) }}
              title="SHORT_WATCH with ABR_BULLISH_CONTEXT_CONFLICT"
              className={`text-xs px-2 py-1 rounded transition-colors whitespace-nowrap
                ${shortConflictOnly
                  ? 'bg-orange-700 text-white font-semibold border border-orange-500'
                  : 'bg-md-surface-high text-md-on-surface-var hover:text-white border border-md-outline-var'}`}>
              ⚠ Short Conflict
            </button>
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">
            Min ${universe === 'nasdaq_gt5' && <span className="text-amber-400 ml-1" title="NASDAQ > $5 requires min price 5 — analytics matrix built on price>$5 data">⚠ ≥5</span>}
          </label>
          <input type="number" value={minPrice}
            onChange={e => {
              const v = parseFloat(e.target.value)
              if (universe === 'nasdaq_gt5' && !isNaN(v) && v < 5) return
              setMinPrice(e.target.value)
            }}
            placeholder={universe === 'nasdaq_gt5' ? '5' : '0'}
            className="bg-md-surface-high text-md-on-surface text-xs px-2 py-1 rounded border border-md-outline-var w-20" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">Max $</label>
          <input type="number" value={maxPrice} onChange={e => setMaxPrice(e.target.value)}
            placeholder="∞" className="bg-md-surface-high text-md-on-surface text-xs px-2 py-1 rounded border border-md-outline-var w-20" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">Min Vol</label>
          <input type="number" value={minVolume} onChange={e => setMinVolume(e.target.value)}
            placeholder="0" className="bg-md-surface-high text-md-on-surface text-xs px-2 py-1 rounded border border-md-outline-var w-24" />
        </div>

        <button onClick={handleScan} disabled={loading}
          className="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 disabled:bg-gray-700 text-white text-xs font-semibold rounded transition-colors self-end">
          {loading ? 'Classifying…' : '🧠 Classify'}
        </button>

        {universe === 'split' && (
          <button
            onClick={async () => {
              setSplitAuditLoading(true)
              setSplitAudit(null)
              try {
                const res = await apiGet(`/api/split-universe/audit?tf=${tf}`)
                setSplitAudit(res)
              } catch (e) { setSplitAudit({ error: e.message }) }
              finally { setSplitAuditLoading(false) }
            }}
            disabled={splitAuditLoading}
            className="px-2 py-1 bg-gray-700 hover:bg-gray-600 disabled:bg-md-surface-high text-md-on-surface text-xs rounded transition-colors self-end"
            title="Audit split universe consistency between Turbo and WLNBB/TZ">
            {splitAuditLoading ? '…' : '🔍 Audit Split'}
          </button>
        )}
      </div>

      <div className="text-xs text-md-on-surface-var/70 px-1">
        Requires TZ/WLNBB stock_stat CSV — run <span className="text-md-on-surface-var">📡 TZ/WLNBB → Generate Stock Stat</span> first.
      </div>

      {universe === 'split' && splitAudit && !splitAudit.error && (
        <div className="p-2 bg-md-surface-con border border-md-outline-var rounded text-xs flex flex-col gap-1">
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-md-on-surface-var">✂️ Split universe:</span>
            <span className="text-white font-semibold">{splitAudit.counts?.live_split_universe ?? '—'} tickers</span>
            <span className="text-md-on-surface-var">·</span>
            <span className="text-md-on-surface-var">shared: <span className="text-green-400">{splitAudit.counts?.shared}</span></span>
            <span className="text-md-on-surface-var">·</span>
            <span className={splitAudit.counts?.only_in_turbo > 0 ? 'text-amber-400' : 'text-md-on-surface-var'}>
              only in live: {splitAudit.counts?.only_in_turbo}
            </span>
            <span className={splitAudit.counts?.only_in_wlnbb > 0 ? 'text-amber-400' : 'text-md-on-surface-var'}>
              stale in CSV: {splitAudit.counts?.only_in_wlnbb}
            </span>
          </div>
          <div className="text-md-on-surface-var/70 text-xs">
            source: {splitAudit.debug?.source} · window: {splitAudit.debug?.start_date} → {splitAudit.debug?.end_date} · generated: {splitAudit.debug?.generated_at?.slice(0, 16)}
          </div>
          {splitAudit.counts?.only_in_wlnbb > 0 && (
            <div className="text-amber-400/80 text-xs">⚠ {splitAudit.counts.only_in_wlnbb} CSV ticker(s) no longer in live split universe — re-generate stock stat to sync.</div>
          )}
        </div>
      )}
      {universe === 'split' && splitAudit?.error && (
        <div className="p-2 bg-red-900/30 border border-red-700 rounded text-red-300 text-xs">Audit error: {splitAudit.error}</div>
      )}

      {error && (
        <div className="p-2 bg-red-900/30 border border-red-700 rounded text-red-300 text-xs">{error}</div>
      )}

      {universe === 'nasdaq_gt5' && tf === '4h' && gt5Batch === '' && (
        <div className="p-2 bg-amber-900/30 border border-amber-700 rounded text-amber-300 text-xs">
          ⚠ Full NASDAQ &gt; $5 4H may be too large for Railway. Batch mode is recommended.
        </div>
      )}

      {scanMode === 'history' && (
        <div className="p-2 bg-indigo-900/30 border border-indigo-700 rounded text-indigo-300 text-xs">
          ⏳ Historical events — not a live watchlist. Shows all classified bars across history.
        </div>
      )}

      {/* ── Split universe consistency badge ─────────────────────────────── */}
      {universe === 'split' && scanDebug && scanMode === 'latest' && (() => {
        const stockStat = scanDebug.stock_stat_unique_tickers ?? 0
        const classified = scanDebug.classified_tickers ?? 0
        const dropped = scanDebug.dropped_tickers_count ?? 0
        const errors = scanDebug.classification_errors?.length ?? 0
        const mismatch = dropped > 0 || errors > 0
        return (
          <div className={`p-2 rounded border text-xs flex flex-col gap-1
            ${mismatch ? 'bg-red-900/30 border-red-700' : 'bg-md-surface-con border-md-outline-var'}`}>
            <div className="flex items-center gap-3 flex-wrap">
              <span className="text-md-on-surface-var">✂️ Split consistency:</span>
              <span className="text-md-on-surface">stock_stat: <span className="text-white font-semibold">{stockStat}</span></span>
              <span className="text-md-on-surface-var">·</span>
              <span className="text-md-on-surface">classified: <span className={classified === stockStat ? 'text-green-400 font-semibold' : 'text-amber-400 font-semibold'}>{classified}</span></span>
              {dropped > 0 && <span className="text-amber-400">dropped: {dropped}</span>}
              {errors > 0  && <span className="text-red-400">errors: {errors}</span>}
              {!mismatch && <span className="text-green-400 text-xs">✓ consistent</span>}
            </div>
            {mismatch && (
              <div className="text-red-300 text-xs font-semibold">
                ⚠ Split universe mismatch detected — classified {classified} / {stockStat} stock_stat tickers.
                {dropped > 0 && ` ${dropped} ticker(s) dropped.`}
                {errors > 0  && ` ${errors} classification error(s).`}
              </div>
            )}
            {scanDebug.dropped_tickers?.length > 0 && (
              <div className="text-amber-400/70 text-xs font-mono">
                dropped: {scanDebug.dropped_tickers.slice(0, 20).join(', ')}{scanDebug.dropped_tickers.length > 20 ? '…' : ''}
              </div>
            )}
          </div>
        )
      })()}

      {displayRows.length > 0 && (
        <div className="flex items-center gap-3">
          <span className="text-xs text-md-on-surface-var">
            Showing {displayRows.length} of {total} classified {scanMode === 'history' ? 'bars' : 'tickers'}
            {sortKey && <span className="ml-1 text-blue-400/70">· sorted by {sortKey} {sortDir === 'asc' ? '▲' : '▼'}</span>}
          </span>
          <button
            onClick={() => {
              const activeBatch = universe === 'nasdaq' ? nasdaqBatch
                : (universe === 'nasdaq_gt5' ? gt5Batch : '')
              exportCSV(displayRows, universe, tf, activeBatch, scanMode)
            }}
            className="px-2 py-1 bg-md-surface-high hover:bg-gray-700 text-md-on-surface text-xs rounded border border-md-outline-var transition-colors"
          >
            ⬇ CSV (current sort)
          </button>
        </div>
      )}

      {displayRows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs border-collapse">
            <thead className="sticky top-0 z-10 bg-md-surface">
              <tr className="border-b border-md-outline-var">
                <SortTh label="Ticker"    colKey="ticker"             {...thProps} />
                <SortTh label="Date"      colKey="date"               {...thProps} />
                <SortTh label="Close"     colKey="close"              {...thProps} align="right" />
                <SortTh label="Signal"    colKey="final_signal"       {...thProps} />
                <SortTh label="Composite" colKey="composite_pattern"  {...thProps} />
                <SortTh label="Seq4"      colKey="seq4"               {...thProps} />
                <SortTh label="Role"      colKey="role"               {...thProps} />
                <SortTh label="Score"     colKey="score"              {...thProps} />
                <SortTh label="Qual"      colKey="quality"            {...thProps} />
                <SortTh label="Action"    colKey="action"             {...thProps} />
                <SortTh label="Vol"       colKey="vol_bucket"         {...thProps} />
                <SortTh label="EMA"       colKey="ema"                {...thProps} />
                <SortTh label="Pos%"      colKey="price_position_4bar" {...thProps} align="right" />
                <SortTh label="Med10d"    colKey="matched_med10d_pct"  {...thProps} align="right" />
                <SortTh label="Fail10d"   colKey="matched_fail10d_pct" {...thProps} align="right" />
                <SortTh label="Rule"      colKey="matched_rule_type"   {...thProps} />
                <SortTh label="ABR"       colKey="abr_category"        {...thProps} />
                <SortTh label="ABR Med"   colKey="abr_med10d_pct"      {...thProps} align="right" />
                <SortTh label="ABR Fail"  colKey="abr_fail10d_pct"     {...thProps} align="right" />
                <SortTh label="Prev1Q"    colKey="abr_prev1_quality"   {...thProps} />
                <SortTh label="Prev2Q"    colKey="abr_prev2_quality"   {...thProps} />
                <SortTh label="Hint"      colKey="abr_action_hint"     {...thProps} />
                <SortTh label="ABR Ctx"   colKey="abr_conflict_flag"   {...thProps} />
                <th className="p-1 text-md-on-surface-var/70 font-normal w-6"></th>
              </tr>
            </thead>
            <tbody>
              {displayRows.map((row, i) => (
                <tr key={`${row.ticker}-${row.date}-${i}`}
                  className="border-b border-md-outline-var/50 hover:bg-md-surface-con/40 transition-colors cursor-pointer"
                  onClick={() => onSelectTicker?.(row.ticker)}
                >
                  <td className="p-1 font-semibold text-white">{row.ticker}</td>
                  <td className="p-1 text-md-on-surface-var">{row.date}</td>
                  <td className="p-1 text-right text-md-on-surface">
                    {row.close ? Number(row.close).toFixed(2) : '—'}
                  </td>
                  <td className="p-1">
                    <span className={`font-mono text-xs
                      ${row.final_signal?.startsWith('T') ? 'text-blue-300'
                        : row.final_signal?.startsWith('Z') ? 'text-red-300'
                        : 'text-yellow-300'}`}>
                      {row.final_signal || '—'}
                    </span>
                  </td>
                  <td className="p-1 text-md-on-surface-var font-mono text-xs">{row.composite_pattern || '—'}</td>
                  <td className="p-1 font-mono text-xs" title={row.seq4 || ''}>
                    {row.seq4
                      ? row.seq4.split('|').map((s, idx, arr) => (
                          <span key={idx}>
                            <span className={
                              idx === arr.length - 1
                                ? (s.startsWith('T') ? 'text-blue-300' : s.startsWith('Z') ? 'text-red-300' : 'text-yellow-300')
                                : 'text-md-on-surface-var/70'
                            }>{s}</span>
                            {idx < arr.length - 1 && <span className="text-gray-700">|</span>}
                          </span>
                        ))
                      : <span className="text-gray-700">—</span>
                    }
                  </td>
                  <td className="p-1"><RoleBadge role={row.role} /></td>
                  <td className="p-1"><ScoreBar score={row.score || 0} /></td>
                  <td className={`p-1 font-semibold ${QUALITY_COLORS[row.quality] || 'text-md-on-surface-var'}`}>
                    {row.quality}
                  </td>
                  <td className={`p-1 text-xs ${ACTION_COLORS[row.action] || 'text-md-on-surface-var'}`}>
                    {row.action?.replace(/_/g, ' ')}
                  </td>
                  <td className="p-1">
                    <span className={`text-xs
                      ${row.vol_bucket === 'VB' ? 'text-red-300'
                        : row.vol_bucket === 'B' ? 'text-orange-300'
                        : 'text-md-on-surface-var'}`}>
                      {row.vol_bucket || ''}
                    </span>
                  </td>
                  <td className="p-1">
                    <EMADots above20={row.above_ema20} above50={row.above_ema50} above89={row.above_ema89} />
                  </td>
                  <td className="p-1 text-right text-md-on-surface-var tabular-nums">
                    {row.price_position_4bar != null
                      ? `${(row.price_position_4bar * 100).toFixed(0)}%`
                      : '—'}
                  </td>
                  <td className="p-1 text-right tabular-nums">
                    {row.matched_med10d_pct != null && row.matched_med10d_pct !== ''
                      ? <span className={parseFloat(row.matched_med10d_pct) >= 0.8 ? 'text-green-400' : parseFloat(row.matched_med10d_pct) >= 0 ? 'text-md-on-surface' : 'text-red-400'}>
                          {parseFloat(row.matched_med10d_pct).toFixed(2)}
                        </span>
                      : <span className="text-gray-700">—</span>
                    }
                  </td>
                  <td className="p-1 text-right tabular-nums">
                    {row.matched_fail10d_pct != null && row.matched_fail10d_pct !== ''
                      ? <span className={parseFloat(row.matched_fail10d_pct) < 25 ? 'text-green-400' : parseFloat(row.matched_fail10d_pct) < 28 ? 'text-yellow-400' : 'text-red-400'}>
                          {parseFloat(row.matched_fail10d_pct).toFixed(1)}%
                        </span>
                      : <span className="text-gray-700">—</span>
                    }
                  </td>
                  <td className="p-1 text-md-on-surface-var text-xs font-mono">
                    {RULE_TYPE_SHORT[row.matched_rule_type] || row.matched_rule_type || '—'}
                  </td>
                  <td className="p-1">
                    <AbrBadge category={row.abr_category} />
                  </td>
                  <td className="p-1 text-right tabular-nums">
                    {row.abr_med10d_pct != null && row.abr_med10d_pct !== ''
                      ? <span className={parseFloat(row.abr_med10d_pct) >= 0.4 ? 'text-green-400' : parseFloat(row.abr_med10d_pct) >= 0 ? 'text-md-on-surface' : 'text-red-400'}>
                          {parseFloat(row.abr_med10d_pct).toFixed(2)}
                        </span>
                      : <span className="text-gray-700">—</span>
                    }
                  </td>
                  <td className="p-1 text-right tabular-nums">
                    {row.abr_fail10d_pct != null && row.abr_fail10d_pct !== ''
                      ? <span className={parseFloat(row.abr_fail10d_pct) < 25 ? 'text-green-400' : parseFloat(row.abr_fail10d_pct) < 35 ? 'text-yellow-400' : 'text-red-400'}>
                          {parseFloat(row.abr_fail10d_pct).toFixed(1)}%
                        </span>
                      : <span className="text-gray-700">—</span>
                    }
                  </td>
                  <td className={`p-1 text-xs font-mono ${ABR_QUALITY_COLORS[row.abr_prev1_quality] || 'text-md-on-surface-var/70'}`}>
                    {row.abr_prev1_quality || '—'}
                  </td>
                  <td className={`p-1 text-xs font-mono ${ABR_QUALITY_COLORS[row.abr_prev2_quality] || 'text-md-on-surface-var/70'}`}>
                    {row.abr_prev2_quality || '—'}
                  </td>
                  <td className="p-1 text-xs text-md-on-surface-var font-mono whitespace-nowrap">
                    {ABR_HINT_SHORT[row.abr_action_hint] ?? row.abr_action_hint ?? '—'}
                  </td>
                  <td className="p-1 whitespace-nowrap" title={row.abr_context_type || ''}>
                    {row.abr_conflict_flag
                      ? <span className="text-xs px-1 py-0.5 rounded bg-orange-900/50 text-orange-300 border border-orange-700/40 font-mono">⚠ CONFLICT</span>
                      : row.abr_confirmation_flag === 'ABR_SHORT_CONFIRMED'
                        ? <span className="text-xs px-1 py-0.5 rounded bg-red-900/40 text-red-400 border border-red-700/30 font-mono">✓ SHORT</span>
                        : row.abr_confirmation_flag === 'ABR_PULLBACK_CONFIRMED'
                          ? <span className="text-xs px-1 py-0.5 rounded bg-blue-900/40 text-blue-300 border border-blue-700/30 font-mono">✓ PB</span>
                          : <span className="text-gray-700">—</span>
                    }
                  </td>
                  <td className="p-1 text-right" onClick={e => e.stopPropagation()}>
                    <ReasonTooltip codes={row.reason_codes} explanation={row.explanation} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && !error && displayRows.length === 0 && (
        <div className="text-md-on-surface-var/70 text-xs py-6 text-center">
          No results. Run "🧠 Classify" to start — needs TZ/WLNBB stock_stat CSV.
        </div>
      )}
    </div>
  )
}

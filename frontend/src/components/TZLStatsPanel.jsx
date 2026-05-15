import { useEffect, useState } from 'react'
import { api } from '../api'
import { Card, Spinner, EmptyState } from '../design-system'
import SignalChip from './SignalChip'
import TableScrollContainer from './TableScrollContainer'

const L_COLS = ["L1", "L2", "L3", "L4", "L5", "L6", "L34", "L22", "L64", "L43", "L1L2", "L2L5"]

function CellNormal({ meta }) {
  if (!meta || meta.count === 0)
    return <span className="text-md-on-surface-var/40 text-xs">—</span>
  const { count, pct, color } = meta
  const bg =
    color === 'gold'  ? 'bg-yellow-900/60 text-yellow-300 ring-1 ring-yellow-500' :
    color === 'green' ? 'bg-green-900/50 text-green-300' :
    color === 'red'   ? 'bg-red-900/50 text-red-300' :
    'bg-md-surface-high text-md-on-surface-var'
  return (
    <div className={`px-1 py-0.5 rounded-md-sm text-center text-xs font-mono ${bg}`}>
      <div className="font-bold">{count}</div>
      <div className="text-xs opacity-70">{pct}%</div>
    </div>
  )
}

function CellDiff({ tickerMeta, benchMeta }) {
  const tp   = tickerMeta?.pct ?? 0
  const bp   = benchMeta?.pct  ?? 0
  const diff = tp - bp
  if (tp === 0 && bp === 0)
    return <span className="text-md-on-surface-var/40 text-xs">—</span>
  const cls =
    diff > 8  ? 'bg-green-900/70 text-green-200 ring-1 ring-green-600' :
    diff > 3  ? 'bg-green-900/40 text-green-400' :
    diff < -8 ? 'bg-red-900/70 text-red-200 ring-1 ring-red-600' :
    diff < -3 ? 'bg-red-900/40 text-red-400' :
    'bg-md-surface-high/60 text-md-on-surface-var'
  const sign = diff > 0 ? '+' : ''
  return (
    <div className={`px-1 py-0.5 rounded-md-sm text-center text-xs font-mono ${cls}`}>
      <div className="font-bold">{tp}%</div>
      <div className="text-[10px] opacity-80">{sign}{diff}pp</div>
    </div>
  )
}

function MatrixTable({ matrix, benchMatrix, view, ticker }) {
  if (!matrix?.length)
    return <EmptyState compact message="No data" />

  const isDiff = view === 'vs_spy' || view === 'vs_qqq'

  const benchLookup = {}
  if (isDiff && benchMatrix?.length) {
    for (const row of benchMatrix) benchLookup[row.sig_id] = row
  }

  const displayMatrix = isDiff ? matrix : (
    view === 'spy' || view === 'qqq' ? benchMatrix : matrix
  )

  if (!displayMatrix?.length)
    return <EmptyState compact message="Benchmark data loading…" />

  return (
    <TableScrollContainer className="flex-1">
      {isDiff && (
        <div className="px-3 py-1.5 bg-md-surface-con border-b border-md-outline-var text-[10px] text-md-on-surface-var">
          Cell: <span className="text-md-on-surface font-medium">ticker %</span>{' '}
          / <span className="text-md-positive">+diff</span> or{' '}
          <span className="text-md-negative">−diff</span> vs benchmark (pp = percentage points)
        </div>
      )}
      <table className="text-xs w-full border-separate border-spacing-0">
        <thead>
          <tr>
            <th className="text-left px-3 py-2 text-md-on-surface-var sticky left-0 bg-md-surface-con z-10 border-b border-md-outline-var min-w-[60px]">
              Signal
            </th>
            <th className="text-center px-1 py-2 text-md-on-surface-var border-b border-md-outline-var min-w-[32px]">n</th>
            {L_COLS.map(col => (
              <th key={col} className="text-center px-1 py-2 text-md-on-surface-var border-b border-md-outline-var min-w-[52px] font-mono">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(isDiff ? matrix : displayMatrix).map((row, idx) => {
            const benchRow = isDiff ? benchLookup[row.sig_id] : null
            return (
              <>
                {idx === 12 && (
                  <tr key="sep">
                    <td colSpan={L_COLS.length + 2} className="py-1">
                      <div className="border-t border-md-outline-var" />
                    </td>
                  </tr>
                )}
                <tr key={row.sig_id} className="border-b border-md-outline-var/40 hover:bg-md-surface-high/30 transition-colors">
                  <td className="sticky left-0 bg-md-surface-con z-10 px-3 py-1.5 border-b border-md-outline-var/40">
                    <SignalChip signal={row.sig_name} size="sm" />
                  </td>
                  <td className="text-center px-1 py-1.5 text-md-on-surface-var">{row.total}</td>
                  {L_COLS.map(col => (
                    <td key={col} className="text-center px-1 py-1.5">
                      {isDiff
                        ? <CellDiff tickerMeta={row.cols?.[col]} benchMeta={benchRow?.cols?.[col]} />
                        : <CellNormal meta={row.cols?.[col]} />
                      }
                    </td>
                  ))}
                </tr>
              </>
            )
          })}
        </tbody>
      </table>
    </TableScrollContainer>
  )
}

const VIEWS = [
  { key: 'ticker', label: 'Ticker' },
  { key: 'spy',    label: 'SPY'    },
  { key: 'qqq',    label: 'QQQ'    },
  { key: 'vs_spy', label: 'vs SPY' },
  { key: 'vs_qqq', label: 'vs QQQ' },
]

const TF_OPTS = ['1wk', '1d', '4h', '1h']

export default function TZLStatsPanel({ ticker, tf }) {
  const [localTf, setLocalTf] = useState(tf || '1d')
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)
  const [view,    setView]    = useState('ticker')

  useEffect(() => {
    if (!ticker) return
    setLoading(true)
    setError(null)
    api.tzLStats(ticker, localTf)
      .then(d => setData(d))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [ticker, localTf])

  const benchMatrix =
    (view === 'spy' || view === 'vs_spy') ? data?.bench_spy :
    (view === 'qqq' || view === 'vs_qqq') ? data?.bench_qqq :
    null

  return (
    <Card variant="outlined" padding="none" className="flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-md-outline-var flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <span className="font-semibold text-sm text-md-on-surface">T/Z × L Co-occurrence — {ticker}</span>
          <div className="flex rounded-md-sm overflow-hidden border border-md-outline-var">
            {TF_OPTS.map((t, i) => (
              <button key={t} onClick={() => setLocalTf(t)}
                className={[
                  'px-2.5 py-1 text-xs font-medium transition-colors',
                  i > 0 ? 'border-l border-md-outline-var' : '',
                  localTf === t
                    ? 'bg-md-primary-container text-md-on-primary-container'
                    : 'text-md-on-surface-var hover:bg-white/5',
                ].join(' ')}>
                {t.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          {/* Legend */}
          <div className="flex items-center gap-2 text-xs">
            <span className="px-1.5 py-0.5 rounded-md-sm bg-yellow-900/60 text-yellow-300 ring-1 ring-yellow-500">Gold</span>
            <span className="text-md-on-surface-var">= best match</span>
            <span className="px-1.5 py-0.5 rounded-md-sm bg-green-900/50 text-green-300">Green</span>
            <span className="text-md-on-surface-var">= aligned</span>
            <span className="px-1.5 py-0.5 rounded-md-sm bg-red-900/50 text-red-300">Red</span>
            <span className="text-md-on-surface-var">= conflict</span>
          </div>

          {/* View toggle */}
          <div className="flex rounded-md-sm overflow-hidden border border-md-outline-var">
            {VIEWS.map((v, i) => (
              <button key={v.key} onClick={() => setView(v.key)}
                className={[
                  'px-2.5 py-1 text-xs font-medium transition-colors',
                  i > 0 ? 'border-l border-md-outline-var' : '',
                  view === v.key
                    ? v.key.startsWith('vs')
                      ? 'bg-violet-900/60 text-violet-200'
                      : 'bg-md-primary-container text-md-on-primary-container'
                    : 'text-md-on-surface-var hover:bg-white/5',
                ].join(' ')}>
                {v.label}
              </button>
            ))}
          </div>

          {loading && <Spinner size={12} />}
          {error   && <span className="text-xs text-md-error">{error}</span>}
        </div>
      </div>

      {/* Body */}
      {!data && !loading && (
        <EmptyState compact message="Select a ticker to load stats" icon="📊" />
      )}
      {data && (
        <MatrixTable
          matrix={data.matrix}
          benchMatrix={benchMatrix}
          view={view}
          ticker={ticker}
        />
      )}
    </Card>
  )
}

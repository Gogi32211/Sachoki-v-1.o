import { useEffect, useState, useCallback } from 'react'
import { api } from '../api'
import SignalChip from './SignalChip'

// Volume bucket colors per spec
const BUCKET_COLORS = {
  W:  { bg: '#c3c0d3', text: '#1a1a2e' },
  L:  { bg: '#0099ff', text: '#ffffff' },
  N:  { bg: '#ffd000', text: '#1a1a1a' },
  B:  { bg: '#e48100', text: '#ffffff' },
  VB: { bg: '#b02020', text: '#ffffff' },
}

function BucketBadge({ bucket }) {
  if (!bucket) return null
  const c = BUCKET_COLORS[bucket]
  if (!c) return null
  return (
    <span
      className="text-xs font-bold px-1.5 py-0.5 rounded font-mono leading-none"
      style={{ backgroundColor: c.bg, color: c.text }}
    >
      {bucket}
    </span>
  )
}

function CandleDirBadge({ dir }) {
  if (!dir) return null
  const cfg = {
    U: { label: '▲', cls: 'text-green-400' },
    D: { label: '▼', cls: 'text-red-400' },
    O: { label: '●', cls: 'text-md-on-surface-var' },
  }
  const c = cfg[dir]
  if (!c) return null
  return <span className={`text-xs font-bold ${c.cls}`}>{c.label}</span>
}

function LComboBadge({ combo }) {
  if (!combo || combo === 'NONE') return null
  return (
    <span className="text-xs text-cyan-300 font-mono bg-cyan-950/30 px-1 py-0.5 rounded">
      {combo}
    </span>
  )
}

function SigBadge({ sig_name }) {
  if (!sig_name || sig_name === 'NONE') return null
  return <SignalChip signal={sig_name} size="sm" />
}

function ScoreBar({ bull, bear }) {
  if (!bull && !bear) return null
  const score = bull || bear
  const isBull = !!bull
  const pct = Math.min(score / 10, 1) * 100
  return (
    <div className="flex items-center gap-1 mt-0.5">
      <div className="flex-1 bg-md-surface-high rounded-full h-1 overflow-hidden">
        <div
          className={`h-full rounded-full ${isBull ? 'bg-green-500' : 'bg-red-500'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={`text-xs font-bold ${isBull ? 'text-green-400' : 'text-red-400'}`}>
        {score}
      </span>
    </div>
  )
}

export default function WatchlistPanel({ tickers, tf, selected, onSelect, onRemove }) {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    if (!tickers.length) return
    setLoading(true)
    try {
      const res = await api.watchlist(tickers, tf)
      setData(res)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [tickers, tf])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 60_000)
    return () => clearInterval(id)
  }, [refresh])

  return (
    <div className="bg-md-surface-con rounded-xl border border-md-outline-var h-full overflow-auto">
      <div className="flex items-center justify-between px-4 py-3 border-b border-md-outline-var">
        <span className="font-semibold text-sm text-white">Watchlist</span>
        {loading && <span className="text-xs text-md-on-surface-var animate-pulse">refreshing…</span>}
      </div>

      <div className="divide-y divide-gray-800">
        {data.length === 0 && !loading && (
          <div className="px-4 py-6 text-center text-md-on-surface-var/70 text-sm">No tickers</div>
        )}
        {data.map((row) => (
          <div
            key={row.ticker}
            className={`relative flex items-stretch hover:bg-md-surface-high transition-colors group
              ${selected === row.ticker ? 'bg-md-surface-high border-l-2 border-blue-500' : ''}`}
          >
            <button
              onClick={() => onSelect(row.ticker)}
              className="flex-1 text-left px-4 py-2.5"
            >
            {/* Row 1: ticker + badge cluster */}
            <div className="flex items-center justify-between gap-1 flex-wrap">
              <span className="font-bold text-sm text-white">{row.ticker}</span>
              <div className="flex items-center gap-1">
                <BucketBadge bucket={row.vol_bucket} />
                <CandleDirBadge dir={row.candle_dir} />
                <SigBadge {...row} />
              </div>
            </div>

            {row.error ? (
              <div className="text-xs text-red-400 mt-0.5">{row.error}</div>
            ) : (
              <>
                {/* Row 2: price + change + l_combo */}
                <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                  <span className="text-sm text-md-on-surface">${row.price?.toFixed(2)}</span>
                  <span className={`text-xs font-medium
                    ${(row.change_pct ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {(row.change_pct ?? 0) >= 0 ? '+' : ''}
                    {row.change_pct?.toFixed(2)}%
                  </span>
                  {row.l_signal && !row.l_combo && (
                    <span className="text-xs text-cyan-400 font-mono">{row.l_signal}</span>
                  )}
                  <LComboBadge combo={row.l_combo} />
                </div>
                {/* Row 3: score bar */}
                <ScoreBar bull={row.bull_score} bear={row.bear_score} />
              </>
            )}
            </button>
            {onRemove && (
              <button
                onClick={e => { e.stopPropagation(); onRemove(row.ticker) }}
                className="opacity-0 group-hover:opacity-100 px-2 text-md-on-surface-var/70 hover:text-red-400 transition-all text-lg leading-none"
                title="Remove from watchlist"
              >×</button>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

import { useState, useEffect, useCallback, useMemo } from 'react'
import { api } from '../api'
import { exportToTV } from '../utils/exportTickers'
import { Button, Alert, Spinner, EmptyState, AssistChip } from '../design-system'
import SignalChip from './SignalChip'

// ── Score sub-tabs ────────────────────────────────────────────────────────────
const SCORE_TABS = [
  { id: 'all',    label: 'All',       min: 0 },
  { id: 'bull',   label: 'Bull ≥4',   min: 4 },
  { id: 'strong', label: 'Strong ≥6', min: 6 },
  { id: 'fire',   label: 'Fire ≥8',   min: 8 },
  { id: 'bear',   label: 'Bear ≥3',   min: 0, bear: true },
]

// ── Signal badge definitions ──────────────────────────────────────────────────
const VABS_BULL = [
  { key: 'best_sig',   label: 'BEST',  cls: 'bg-lime-800 text-lime-200 ring-1 ring-lime-400' },
  { key: 'strong_sig', label: 'STR',   cls: 'bg-emerald-800 text-emerald-200' },
  { key: 'vbo_up',     label: 'VBO↑', cls: 'bg-green-700 text-white' },
  { key: 'abs_sig',    label: 'ABS',   cls: 'bg-teal-800 text-teal-200' },
  { key: 'climb_sig',  label: 'CLB',   cls: 'bg-cyan-800 text-cyan-200' },
  { key: 'load_sig',   label: 'LD',    cls: 'bg-blue-800 text-blue-200' },
]

const WYK_BULL = [
  { key: 'ns', label: 'NS', cls: 'bg-teal-900 text-teal-300' },
  { key: 'sq', label: 'SQ', cls: 'bg-cyan-900 text-cyan-300' },
  { key: 'sc', label: 'SC', cls: 'bg-orange-900 text-orange-300' },
]

const WYK_BEAR = [
  { key: 'bc',     label: 'BC',   cls: 'bg-red-900 text-red-300' },
  { key: 'nd',     label: 'ND',   cls: 'bg-pink-900 text-pink-300' },
  { key: 'vbo_dn', label: 'VBO↓', cls: 'bg-red-800 text-red-200' },
]

const COMBO_BULL = [
  { key: 'rocket',   label: '🚀',     cls: 'bg-red-900 text-red-200 font-bold' },
  { key: 'buy_2809', label: 'BUY',    cls: 'bg-lime-800 text-lime-200' },
  { key: 'sig3g',    label: '3G',     cls: 'bg-cyan-800 text-cyan-200' },
  { key: 'rtv',      label: 'RTV',    cls: 'bg-blue-800 text-blue-200' },
  { key: 'hilo_buy', label: 'HILO↑', cls: 'bg-green-800 text-green-200' },
  { key: 'atr_brk',  label: 'ATR↑',  cls: 'bg-emerald-800 text-emerald-200' },
  { key: 'bb_brk',   label: 'BB↑',   cls: 'bg-teal-800 text-teal-200' },
]

const WLNBB_SIGS = [
  { key: 'FRI34',     label: 'FRI34', cls: 'text-cyan-300' },
  { key: 'L34',       label: 'L34',   cls: 'text-blue-300' },
  { key: 'L43',       label: 'L43',   cls: 'text-teal-300' },
  { key: 'L64',       label: 'L64',   cls: 'text-orange-400' },
  { key: 'L22',       label: 'L22',   cls: 'text-red-400' },
  { key: 'CCI_READY', label: 'CCI',   cls: 'text-violet-300' },
  { key: 'BLUE',      label: 'BL',    cls: 'text-sky-300' },
  { key: 'BO_UP',     label: 'BO↑',  cls: 'text-lime-300' },
  { key: 'BX_UP',     label: 'BX↑',  cls: 'text-lime-400' },
  { key: 'BO_DN',     label: 'BO↓',  cls: 'text-rose-400' },
  { key: 'BX_DN',     label: 'BX↓',  cls: 'text-rose-500' },
]

const BUCKET_COLORS = {
  W:  { bg: '#3a3a4a', text: '#c3c0d3' },
  L:  { bg: '#0a3a5a', text: '#88ccff' },
  N:  { bg: '#3a3000', text: '#ffd000' },
  B:  { bg: '#3a1800', text: '#e48100' },
  VB: { bg: '#3a0000', text: '#ff6060' },
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function scoreBg(score, isBear = false) {
  if (isBear) return score >= 3 ? 'bg-red-950/30' : ''
  if (score >= 8) return 'bg-yellow-950/40'
  if (score >= 6) return 'bg-green-950/30'
  if (score >= 4) return 'bg-green-950/15'
  return ''
}

function ScoreBadge({ score, isBear = false }) {
  const base = isBear
    ? 'bg-red-900/70 text-red-200'
    : score >= 8 ? 'bg-yellow-800/80 text-yellow-200 ring-1 ring-yellow-400'
    : score >= 6 ? 'bg-green-800/80 text-green-200 ring-1 ring-green-400'
    : score >= 4 ? 'bg-green-900/60 text-green-300'
    : 'bg-md-surface-high text-md-on-surface-var'
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded-md-sm text-xs font-bold font-mono ${base}`}>
      {score}
    </span>
  )
}

function TZBadge({ sig_id, sig_name }) {
  if (!sig_name || sig_name === 'NONE') return null
  const bull = sig_id >= 1 && sig_id <= 12
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded-md-sm font-mono font-semibold
      ${bull ? 'bg-green-900/60 text-green-300' : 'bg-red-900/60 text-red-300'}`}>
      {sig_name}
    </span>
  )
}

function BucketBadge({ bucket }) {
  if (!bucket) return null
  const c = BUCKET_COLORS[bucket]
  if (!c) return <span className="text-xs text-md-on-surface-var">{bucket}</span>
  return (
    <span className="text-xs font-bold px-1 rounded-md-sm font-mono"
      style={{ backgroundColor: c.bg, color: c.text }}>
      {bucket}
    </span>
  )
}

function WLNBBBadges({ l_signal, l_combo }) {
  const active = WLNBB_SIGS.filter(s => l_signal === s.key || (l_combo && l_combo.includes(s.key)))
  if (!active.length) return null
  return (
    <span className="flex flex-wrap gap-0.5">
      {active.map(s => (
        <span key={s.key} className={`text-xs font-mono ${s.cls}`}>{s.label}</span>
      ))}
    </span>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function CombinedScanPanel({ tf, onSelectTicker }) {
  const [tab, setTab]           = useState('bull')
  const [allResults, setAll]    = useState([])
  const [lastScan, setLastScan] = useState(null)
  const [loading, setLoading]   = useState(false)
  const [scanning, setScanning] = useState(false)
  const [error, setError]       = useState(null)

  const activeTabDef = SCORE_TABS.find(t => t.id === tab) || SCORE_TABS[0]

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    api.combinedScan(tf, 0, 'all', 500)
      .then(d => {
        setAll(d.results || [])
        setLastScan(d.last_scan)
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [tf])

  useEffect(() => { load() }, [load])

  const results = useMemo(() => {
    if (tab === 'bear')   return allResults.filter(r => (r.bear_score || 0) >= 3)
    if (tab === 'fire')   return allResults.filter(r => (r.bull_score || 0) >= 8)
    if (tab === 'strong') return allResults.filter(r => (r.bull_score || 0) >= 6)
    if (tab === 'bull')   return allResults.filter(r => (r.bull_score || 0) >= 4)
    return allResults
  }, [allResults, tab])

  const _poll = () => {
    const iv = setInterval(() => {
      api.scanStatus()
        .then(s => {
          if (!s.running) { clearInterval(iv); setScanning(false); load() }
        })
        .catch(() => { clearInterval(iv); setScanning(false) })
    }, 2000)
    setTimeout(() => { clearInterval(iv); setScanning(false); load() }, 300_000)
  }

  const scan = () => {
    setScanning(true)
    setError(null)
    api.scanTrigger(tf)
      .then(() => _poll())
      .catch(e => { setError(e.message); setScanning(false) })
  }

  const fmtTime = iso => {
    if (!iso) return null
    try { return new Date(iso).toLocaleString() } catch { return iso }
  }

  return (
    <div className="flex flex-col h-full bg-md-surface text-md-on-surface text-xs">

      {/* ── Toolbar ── */}
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-md-outline-var flex-wrap shrink-0">
        <div className="flex items-center gap-1.5 flex-wrap">
          <Button
            variant="filled"
            size="sm"
            onClick={scan}
            disabled={scanning}
            loading={scanning}
          >
            {scanning ? 'Scanning…' : '▶ Scan'}
          </Button>

          {/* Score sub-tabs */}
          <div className="flex rounded-md-sm overflow-hidden border border-md-outline-var">
            {SCORE_TABS.map((t, i) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={[
                  'px-2.5 py-1.5 text-xs font-medium transition-colors duration-100 select-none',
                  i > 0 ? 'border-l border-md-outline-var' : '',
                  tab === t.id
                    ? t.bear
                      ? 'bg-red-900/70 text-red-200'
                      : 'bg-md-primary-container text-md-on-primary-container'
                    : 'text-md-on-surface-var hover:bg-white/5',
                ].join(' ')}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2">
          {results.length > 0 && (
            <AssistChip
              label="Export TV"
              icon="⬇"
              onClick={() => exportToTV(results.map(r => r.ticker), 'combined_scan.txt')}
            />
          )}
          <span className="text-md-on-surface-var text-xs">
            {results.length}/{allResults.length}
            {lastScan && ` · ${fmtTime(lastScan)}`}
          </span>
        </div>
      </div>

      {/* ── Legend ── */}
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 px-3 py-1.5 border-b border-md-outline-var/50 bg-md-surface-con shrink-0">
        <span className="text-lime-300/70">BEST=vol+NS/SQ+L34</span>
        <span className="text-emerald-300/70">STR=2×vol</span>
        <span className="text-green-300/70">VBO↑=price break</span>
        <span className="text-teal-300/70">ABS=bucket jump</span>
        <span className="text-cyan-300/70">CLB=gradual rise</span>
        <span className="text-blue-300/70">LD=vol spike</span>
        <span className="text-teal-400/70">NS=no supply</span>
        <span className="text-red-400/70">BC=buy climax</span>
      </div>

      {scanning && (
        <div className="px-4 py-1.5 border-b border-md-outline-var bg-md-primary-container/20 text-md-on-primary-container text-xs shrink-0">
          <span className="animate-pulse">●</span> Scanning — gamocdileba 1–3 wuti…
        </div>
      )}
      {error && (
        <div className="px-3 py-2 shrink-0">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      {/* ── Table ── */}
      <div className="overflow-auto flex-1">
        {loading ? (
          <div className="flex items-center justify-center gap-2 py-12 text-md-on-surface-var text-xs">
            <Spinner size={14} />
            <span>Loading…</span>
          </div>
        ) : results.length === 0 ? (
          <EmptyState
            icon="📊"
            message="No results"
            sub="Press ▶ Scan or lower the score filter"
            compact
          />
        ) : (
          <table className="w-full border-collapse">
            <thead className="sticky top-0 bg-md-surface-con z-10">
              <tr className="border-b border-md-outline-var text-md-on-surface-var text-left">
                <th className="px-2 py-1.5 font-medium">Ticker</th>
                <th className="px-2 py-1.5 font-medium text-center">Score</th>
                <th className="px-2 py-1.5 font-medium text-center">T/Z</th>
                <th className="px-2 py-1.5 font-medium text-center hidden sm:table-cell">Bkt</th>
                <th className="px-2 py-1.5 font-medium">VABS</th>
                <th className="px-2 py-1.5 font-medium">Wyck</th>
                <th className="px-2 py-1.5 font-medium">Combo</th>
                <th className="px-2 py-1.5 font-medium hidden md:table-cell">L-Sig</th>
                <th className="px-2 py-1.5 font-medium text-right">Price</th>
                <th className="px-2 py-1.5 font-medium text-right">%</th>
              </tr>
            </thead>
            <tbody>
              {results.map((row, i) => {
                const isBear = tab === 'bear'
                const displayScore = isBear ? (row.bear_score || 0) : (row.bull_score || 0)
                return (
                  <tr
                    key={i}
                    onClick={() => onSelectTicker?.(row.ticker)}
                    className={[
                      'border-b border-md-outline-var/40 cursor-pointer',
                      'hover:bg-md-surface-high/50 transition-colors',
                      scoreBg(displayScore, isBear),
                    ].join(' ')}
                  >
                    <td className="px-2 py-1 font-mono font-semibold text-md-primary">
                      {row.ticker}
                    </td>
                    <td className="px-2 py-1 text-center">
                      <ScoreBadge score={displayScore} isBear={isBear} />
                    </td>
                    <td className="px-2 py-1 text-center">
                      <TZBadge sig_id={row.sig_id} sig_name={row.sig_name} />
                    </td>
                    <td className="px-1 py-1 text-center hidden sm:table-cell">
                      <BucketBadge bucket={row.vol_bucket} />
                    </td>
                    <td className="px-2 py-1">
                      <div className="flex flex-wrap gap-0.5">
                        {VABS_BULL.filter(d => row[d.key]).map(d => (
                          <SignalChip key={d.key} signal={d.label} size="sm" />
                        ))}
                      </div>
                    </td>
                    <td className="px-2 py-1">
                      <div className="flex flex-wrap gap-0.5">
                        {[...WYK_BULL, ...WYK_BEAR].filter(d => row[d.key]).map(d => (
                          <SignalChip key={d.key} signal={d.label} size="sm" />
                        ))}
                      </div>
                    </td>
                    <td className="px-2 py-1">
                      <div className="flex flex-wrap gap-0.5">
                        {COMBO_BULL.filter(d => row[d.key]).map(d => (
                          <SignalChip key={d.key} signal={d.label} size="sm" />
                        ))}
                        {row.wick_bull ? <SignalChip signal="WP↑" size="sm" /> : null}
                      </div>
                    </td>
                    <td className="px-2 py-1 hidden md:table-cell">
                      <WLNBBBadges l_signal={row.l_signal} l_combo={row.l_combo} />
                    </td>
                    <td className="px-2 py-1 text-right font-mono text-md-on-surface">
                      ${(row.last_price || 0).toFixed(2)}
                    </td>
                    <td className={`px-2 py-1 text-right font-mono ${
                      (row.change_pct || 0) >= 0 ? 'text-md-positive' : 'text-md-negative'
                    }`}>
                      {(row.change_pct || 0) >= 0 ? '+' : ''}{(row.change_pct || 0).toFixed(2)}%
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

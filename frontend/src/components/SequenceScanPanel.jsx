import { useState, useEffect, useMemo, useRef } from 'react'
import { api } from '../api'

const UNIVERSES = [
  { key: 'sp500',      label: 'S&P 500' },
  { key: 'nasdaq',     label: 'NASDAQ' },
  { key: 'nasdaq_gt5', label: 'NASDAQ > $5' },
  { key: 'russell2k',  label: 'Russell 2K' },
  { key: 'all_us',     label: 'All US' },
  { key: 'split',      label: 'SPLIT' },
]
const TF_OPTS    = ['1d', '4h', '1h']
const SEQ_LENGTHS = [2, 3, 4, 5, 6]
const MODES = [
  { key: 'type', label: 'T/Z type', help: 'Each bar contributes "T" or "Z" — e.g. "TZTZ"' },
  { key: 'full', label: 'Full label', help: 'Each bar contributes its full signal name — e.g. "T4|Z2|T1G|Z3"' },
]
const SORTS = [
  { key: 'score',        label: 'Score (WR × log count)' },
  { key: 'win_rate',     label: 'Win rate (1D)' },
  { key: 'win_rate_3d',  label: 'Win rate (3D)' },
  { key: 'win_rate_5d',  label: 'Win rate (5D)' },
  { key: 'win_rate_9d',  label: 'Win rate (9D)' },
  { key: 'avg_ret_1d',   label: 'Avg Ret 1D' },
  { key: 'avg_ret_3d',   label: 'Avg Ret 3D' },
  { key: 'avg_ret_5d',   label: 'Avg Ret 5D' },
  { key: 'avg_ret_9d',   label: 'Avg Ret 9D' },
  { key: 'count',        label: 'Occurrences' },
  { key: 'ticker_count', label: 'Breadth (ticker count)' },
]

function fmtPct(v) {
  if (v == null || isNaN(Number(v))) return '—'
  return (Number(v) * 100).toFixed(1) + '%'
}
function fmtNum(v, d = 4) {
  if (v == null || isNaN(Number(v))) return '—'
  return Number(v).toFixed(d)
}

function _csvCell(v) {
  if (v == null) return ''
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : ''
  let s = Array.isArray(v) ? v.join(';') : String(v)
  const isNumeric = s !== '' && !isNaN(Number(s)) && /^-?\d/.test(s)
  if (!isNumeric && /^[=+\-@]/.test(s)) s = "'" + s
  return s.includes(',') || s.includes('"') || s.includes('\n')
    ? `"${s.replace(/"/g, '""')}"` : s
}

export default function SequenceScanPanel() {
  const [universe,  setUniverse]  = useState('sp500')
  const [tf,        setTf]        = useState('1d')
  const [seqLen,    setSeqLen]    = useState(4)
  const [mode,      setMode]      = useState('type')
  const [minCount,  setMinCount]  = useState(10)
  const [sortBy,    setSortBy]    = useState('score')
  const [limit,     setLimit]     = useState(100)
  const [filter,    setFilter]    = useState('')

  const [status,    setStatus]    = useState({ status: 'idle', progress: 0, total: 0, pct: 0 })
  const [results,   setResults]   = useState([])
  const [meta,      setMeta]      = useState(null)
  const [error,     setError]     = useState('')
  const [loading,   setLoading]   = useState(false)
  const pollRef = useRef(null)

  const params = useMemo(
    () => ({ universe, tf, seq_len: seqLen, mode, min_count: minCount }),
    [universe, tf, seqLen, mode, minCount]
  )

  const fetchStatus = async () => {
    try {
      const s = await api.sequenceScanStatus(params)
      setStatus(s || {})
      return s
    } catch (e) { setError(String(e)); return null }
  }

  const fetchResults = async () => {
    try {
      setLoading(true)
      const r = await api.sequenceScanResults({ ...params, sort_by: sortBy, limit })
      setResults(r?.results || [])
      setMeta({
        total_sequences: r?.total_sequences ?? 0,
        tickers_total:   r?.tickers_total ?? null,
        stat_path:       r?.stat_path ?? null,
        completed_at:    r?.completed_at,
        cache_key:       r?.cache_key,
      })
    } catch (e) { setError(String(e)) }
    finally    { setLoading(false) }
  }

  // Initial load + reload on params change
  useEffect(() => {
    fetchStatus().then(s => { if (s?.status === 'done') fetchResults() })
    return () => clearInterval(pollRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [universe, tf, seqLen, mode, minCount])

  // Re-sort/relimit without re-querying
  useEffect(() => { if (status.status === 'done') fetchResults() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  , [sortBy, limit])

  // Poll while running
  useEffect(() => {
    clearInterval(pollRef.current)
    if (status.status === 'running') {
      pollRef.current = setInterval(async () => {
        const s = await fetchStatus()
        if (!s || s.status !== 'running') {
          clearInterval(pollRef.current)
          if (s?.status === 'done') fetchResults()
        }
      }, 2000)
    }
    return () => clearInterval(pollRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status.status])

  const trigger = async () => {
    setError('')
    try {
      const r = await api.sequenceScanTrigger(params)
      // Optimistically flip to 'running' so the polling effect kicks in
      // immediately. FastAPI BackgroundTasks runs the worker AFTER the
      // response is sent, and the worker only writes status='running' to
      // SQLite once it starts; if we fetched /status right now we'd see
      // 'not_run' and the polling effect (gated on status==='running')
      // would never start. Optimistic flag avoids that race entirely.
      setStatus({
        status:    'running',
        cache_key: r?.cache_key,
        progress:  0, total: 0, pct: 0,
        params:    { ...params },
      })
    } catch (e) {
      const msg = e?.detail || e?.message || String(e)
      if (msg.includes('409') || msg.toLowerCase().includes('already running')) {
        setError('Another sequence scan is already running — wait for it to finish.')
      } else if (msg.toLowerCase().includes('no stock_stat')
                 || msg.toLowerCase().includes('no_data')) {
        setError('No Stock Stat CSV found — run Admin → Stock Stat or TZ/WLNBB → Generate Stock Stat first.')
      } else {
        setError(msg)
      }
    }
  }

  const filteredRows = useMemo(() => {
    if (!filter) return results
    const f = filter.toUpperCase()
    return results.filter(r =>
      (r.sequence || '').toUpperCase().includes(f)
      || (r.type_seq || '').toUpperCase().includes(f)
    )
  }, [results, filter])

  const exportCsv = () => {
    if (!filteredRows.length) return
    const cols = [
      'sequence', 'type_seq', 'count', 'wins', 'win_rate',
      'ticker_count', 'score',
      'avg_ret_1d', 'med_ret_1d', 'std_ret',
      // Multi-horizon stats
      'win_rate_3d', 'avg_ret_3d', 'med_ret_3d', 'count_3d',
      'win_rate_5d', 'avg_ret_5d', 'med_ret_5d', 'count_5d',
      'win_rate_9d', 'avg_ret_9d', 'med_ret_9d', 'count_9d',
    ]
    const lines = [cols.join(',')]
    for (const r of filteredRows) {
      lines.push(cols.map(c => _csvCell(r[c])).join(','))
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `sequences_${universe}_${tf}_${seqLen}bar_${mode}_${new Date().toISOString().slice(0,10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const isRunning = status.status === 'running'
  const pct = Math.max(0, Math.min(100, Number(status.pct ?? 0)))

  return (
    <div className="text-sm">
      {/* ── Controls ─────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-end gap-3 p-2 border border-md-outline-var rounded-md-md bg-md-surface-con">
        <div className="flex flex-col">
          <label className="text-[10px] uppercase text-md-on-surface-var">Universe</label>
          <select value={universe} onChange={e => setUniverse(e.target.value)}
                  className="bg-md-surface-high border border-md-outline-var text-md-on-surface text-xs rounded-md-sm px-2 py-1">
            {UNIVERSES.map(u => <option key={u.key} value={u.key}>{u.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col">
          <label className="text-[10px] uppercase text-md-on-surface-var">TF</label>
          <select value={tf} onChange={e => setTf(e.target.value)}
                  className="bg-md-surface-high border border-md-outline-var text-md-on-surface text-xs rounded-md-sm px-2 py-1">
            {TF_OPTS.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div className="flex flex-col">
          <label className="text-[10px] uppercase text-md-on-surface-var">Seq length</label>
          <select value={seqLen} onChange={e => setSeqLen(Number(e.target.value))}
                  className="bg-md-surface-high border border-md-outline-var text-md-on-surface text-xs rounded-md-sm px-2 py-1">
            {SEQ_LENGTHS.map(n => <option key={n} value={n}>{n} bars</option>)}
          </select>
        </div>
        <div className="flex flex-col">
          <label className="text-[10px] uppercase text-md-on-surface-var">Mode</label>
          <select value={mode} onChange={e => setMode(e.target.value)}
                  title={MODES.find(m => m.key === mode)?.help}
                  className="bg-md-surface-high border border-md-outline-var text-md-on-surface text-xs rounded-md-sm px-2 py-1">
            {MODES.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col">
          <label className="text-[10px] uppercase text-md-on-surface-var">Min count</label>
          <input type="number" min={1} value={minCount}
                 onChange={e => setMinCount(Number(e.target.value) || 1)}
                 className="bg-md-surface-high border border-md-outline-var text-md-on-surface text-xs rounded-md-sm px-2 py-1 w-20" />
        </div>
        <div className="flex flex-col">
          <label className="text-[10px] uppercase text-md-on-surface-var">Sort by</label>
          <select value={sortBy} onChange={e => setSortBy(e.target.value)}
                  className="bg-md-surface-high border border-md-outline-var text-md-on-surface text-xs rounded-md-sm px-2 py-1">
            {SORTS.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col">
          <label className="text-[10px] uppercase text-md-on-surface-var">Limit</label>
          <input type="number" min={1} max={10000} value={limit}
                 onChange={e => setLimit(Number(e.target.value) || 100)}
                 className="bg-md-surface-high border border-md-outline-var text-md-on-surface text-xs rounded-md-sm px-2 py-1 w-20" />
        </div>
        <button onClick={trigger} disabled={isRunning}
                className="bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white text-xs px-3 py-1 rounded font-semibold">
          {isRunning ? '⏳ Scanning…' : '▶ Run Sequence Scan'}
        </button>
        <button onClick={fetchResults}
                disabled={loading || status.status !== 'done'}
                className="bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white text-xs px-3 py-1 rounded">
          ↻ Refresh
        </button>
        <button onClick={exportCsv} disabled={!filteredRows.length}
                className="bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 text-white text-xs px-3 py-1 rounded">
          ⬇ Export CSV
        </button>
        <input type="text" value={filter} placeholder="Filter sequences…"
               onChange={e => setFilter(e.target.value)}
               className="bg-md-surface-high border border-md-outline-var text-md-on-surface text-xs rounded-md-sm px-2 py-1 w-48" />
      </div>

      {/* ── Status / progress ─────────────────────────────────────────── */}
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
        <span className={`px-1.5 py-0.5 rounded font-medium
          ${status.status === 'running' ? 'bg-amber-900/50 text-amber-200' :
            status.status === 'done'    ? 'bg-emerald-900/50 text-emerald-200' :
            status.status === 'no_data' ? 'bg-red-900/40 text-red-200' :
            status.status === 'error'   ? 'bg-red-900/40 text-red-200' :
            'bg-md-surface-high text-md-on-surface-var'}`}>
          status: {status.status || 'idle'}
        </span>
        {isRunning && (
          <>
            <span className="text-md-on-surface">
              {status.progress ?? 0} / {status.total ?? 0} tickers ({pct}%)
            </span>
            <div className="flex-1 max-w-md h-1.5 bg-md-surface-high rounded-md-full overflow-hidden">
              <div className="bg-violet-500 h-full transition-all"
                   style={{ width: `${pct}%` }} />
            </div>
          </>
        )}
        {meta?.total_sequences != null && status.status === 'done' && (
          <span className="text-md-on-surface-var">
            {meta.total_sequences.toLocaleString()} sequences ≥ min_count
            {meta.completed_at && <> · {meta.completed_at.slice(0, 19).replace('T', ' ')}</>}
          </span>
        )}
        {status.stat_path && (
          <span className="text-md-on-surface-var text-[10px] font-mono"
                title="CSV used by the scan">
            CSV: {status.stat_path}
          </span>
        )}
        {status.error && (
          <span className="text-red-400">⚠ {status.error}</span>
        )}
      </div>

      {error && (
        <div className="mt-2 text-xs text-md-error bg-md-error-container border border-md-error/30 rounded-md-md p-2">
          {error}
        </div>
      )}

      {/* ── Table ─────────────────────────────────────────────────────── */}
      <div className="mt-2 overflow-auto border border-md-outline-var rounded-md-md">
        <table className="min-w-full text-xs">
          <thead className="bg-md-surface-con text-md-on-surface-var">
            <tr>
              <th className="px-2 py-1.5 text-left">Sequence</th>
              <th className="px-2 py-1.5 text-left">Type</th>
              <th className="px-2 py-1.5 text-right">Win 1D</th>
              <th className="px-2 py-1.5 text-right">Win 3D</th>
              <th className="px-2 py-1.5 text-right">Win 5D</th>
              <th className="px-2 py-1.5 text-right">Win 9D</th>
              <th className="px-2 py-1.5 text-right">Count</th>
              <th className="px-2 py-1.5 text-right">Breadth</th>
              <th className="px-2 py-1.5 text-right">Avg 1D</th>
              <th className="px-2 py-1.5 text-right">Avg 3D</th>
              <th className="px-2 py-1.5 text-right">Avg 5D</th>
              <th className="px-2 py-1.5 text-right">Avg 9D</th>
              <th className="px-2 py-1.5 text-right">Med 1D</th>
              <th className="px-2 py-1.5 text-right">Score</th>
            </tr>
          </thead>
          <tbody>
            {filteredRows.length === 0 && (
              <tr><td colSpan={14} className="text-center text-md-on-surface-var py-6">
                {status.status === 'not_run'
                  ? 'No scan run yet — press ▶ Run Sequence Scan'
                  : status.status === 'no_data'
                    ? (status.error
                        || 'No Stock Stat CSV found for this universe/tf — run Admin → Stock Stat (Bulk Signal CSV) or TZ/WLNBB → Generate Stock Stat first.')
                    : status.status === 'error'
                      ? `Scan error: ${status.error || 'unknown'}`
                      : (meta?.tickers_total ?? 0) === 0
                        ? 'Scan ran but the chosen CSV had 0 ticker rows. Re-run Stock Stat and try again.'
                        : `No sequences match current filter / min_count (${meta?.tickers_total} tickers seen).`}
              </td></tr>
            )}
            {filteredRows.map((r, i) => {
              const wrCls = (v) =>
                v == null            ? 'text-gray-700' :
                v >= 0.6             ? 'text-emerald-300 font-semibold' :
                v >= 0.5             ? 'text-teal-200' :
                v >= 0.4             ? 'text-yellow-300' :
                                       'text-red-300'
              const retCls = (v) =>
                v == null ? 'text-gray-700' :
                v > 0     ? 'text-emerald-300' :
                v < 0     ? 'text-red-300'     : 'text-md-on-surface-var'
              return (
                <tr key={`${r.sequence}-${i}`}
                    className="border-t border-md-outline-var hover:bg-md-surface-high/40">
                  <td className="px-2 py-1 font-mono text-blue-300">{r.sequence}</td>
                  <td className="px-2 py-1 font-mono text-md-on-surface-var">{r.type_seq}</td>
                  <td className={`px-2 py-1 text-right font-mono ${wrCls(r.win_rate)}`}
                      title={`1D win rate from ${r.count} occurrences`}>
                    {fmtPct(r.win_rate)}
                  </td>
                  <td className={`px-2 py-1 text-right font-mono ${wrCls(r.win_rate_3d)}`}
                      title={r.count_3d != null ? `3D from ${r.count_3d} events` : ''}>
                    {fmtPct(r.win_rate_3d)}
                  </td>
                  <td className={`px-2 py-1 text-right font-mono ${wrCls(r.win_rate_5d)}`}
                      title={r.count_5d != null ? `5D from ${r.count_5d} events` : ''}>
                    {fmtPct(r.win_rate_5d)}
                  </td>
                  <td className={`px-2 py-1 text-right font-mono ${wrCls(r.win_rate_9d)}`}
                      title={r.count_9d != null ? `9D from ${r.count_9d} events` : ''}>
                    {fmtPct(r.win_rate_9d)}
                  </td>
                  <td className="px-2 py-1 text-right font-mono text-md-on-surface">{r.count}</td>
                  <td className="px-2 py-1 text-right font-mono text-md-on-surface">{r.ticker_count}</td>
                  <td className={`px-2 py-1 text-right font-mono ${retCls(r.avg_ret_1d)}`}>
                    {fmtNum(r.avg_ret_1d, 4)}
                  </td>
                  <td className={`px-2 py-1 text-right font-mono ${retCls(r.avg_ret_3d)}`}>
                    {fmtNum(r.avg_ret_3d, 4)}
                  </td>
                  <td className={`px-2 py-1 text-right font-mono ${retCls(r.avg_ret_5d)}`}>
                    {fmtNum(r.avg_ret_5d, 4)}
                  </td>
                  <td className={`px-2 py-1 text-right font-mono ${retCls(r.avg_ret_9d)}`}>
                    {fmtNum(r.avg_ret_9d, 4)}
                  </td>
                  <td className={`px-2 py-1 text-right font-mono ${retCls(r.med_ret_1d)}`}>
                    {fmtNum(r.med_ret_1d, 4)}
                  </td>
                  <td className="px-2 py-1 text-right font-mono text-amber-200">{fmtNum(r.score, 4)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="mt-2 text-[10px] text-md-on-surface-var">
        Score = win_rate × log1p(count) — balances rare high-WR vs common low-WR sequences.
        Excludes T7, T8, Z8 by spec. Reads either Admin → Stock Stat (Bulk Signal CSV)
        or TZ/WLNBB → Generate Stock Stat output. Generate one of those first.
      </div>
    </div>
  )
}

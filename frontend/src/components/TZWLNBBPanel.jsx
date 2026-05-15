import { useState, useEffect, useRef } from 'react'

const BASE = import.meta.env.VITE_API_URL || ''

async function apiGet(path) {
  const res = await fetch(BASE + path)
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json()
}

async function apiPost(path, params = {}) {
  const qs = new URLSearchParams(params).toString()
  const url = qs ? `${BASE}${path}?${qs}` : BASE + path
  const res = await fetch(url, { method: 'POST' })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json()
}

const UNIVERSES = [
  { key: 'sp500',      label: 'S&P 500'    },
  { key: 'nasdaq',     label: 'NASDAQ'     },
  { key: 'nasdaq_gt5', label: 'NASDAQ > $5'},
  { key: 'russell2k',  label: 'Russell 2K' },
  { key: 'all_us',     label: 'All US'     },
  { key: 'split',      label: '✂️ SPLIT'   },
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

const SIG_TYPES = [
  { key: 'all',   label: 'All'    },
  { key: 'T',     label: 'T'      },
  { key: 'Z',     label: 'Z'      },
  { key: 'L',     label: 'L'      },
  { key: 'PREUP', label: 'PREUP'  },
  { key: 'PREDN', label: 'PREDN'  },
  { key: 'Combo', label: 'Combo'  },
]

function Badge({ text, color }) {
  if (!text) return null
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-semibold ${color}`}>
      {text}
    </span>
  )
}

function tBadgeColor(sig) {
  if (!sig) return ''
  if (sig === 'T4' || sig === 'T6') return 'bg-blue-700 text-blue-100'
  if (sig.startsWith('T1G') || sig.startsWith('T2G')) return 'bg-blue-600 text-blue-100'
  return 'bg-blue-800 text-blue-200'
}

function zBadgeColor(sig) {
  if (!sig) return ''
  if (sig === 'Z4' || sig === 'Z6') return 'bg-red-700 text-red-100'
  if (sig.startsWith('Z1G') || sig.startsWith('Z2G')) return 'bg-red-600 text-red-100'
  return 'bg-red-800 text-red-200'
}

function lBadgeColor() {
  return 'bg-yellow-700 text-yellow-100'
}

function preBadgeColor(sig) {
  if (!sig) return ''
  return sig.startsWith('P') ? 'bg-emerald-700 text-emerald-100' : 'bg-orange-700 text-orange-100'
}

function DebugModal({ ticker, date, tf, onClose }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    const qs = new URLSearchParams({ ticker, tf })
    if (date) qs.set('date', date)
    apiGet(`/api/tz-wlnbb/debug?${qs}`)
      .then(d => setData(d))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [ticker, date, tf])

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-md-surface-con border border-md-outline-var rounded-lg p-4 max-w-2xl w-full max-h-[80vh] overflow-auto">
        <div className="flex justify-between items-center mb-3">
          <h3 className="text-white font-semibold">{ticker} — Debug ({date || 'latest'})</h3>
          <button onClick={onClose} className="text-md-on-surface-var hover:text-white text-xl">✕</button>
        </div>
        {loading && <div className="text-md-on-surface-var">Loading…</div>}
        {error   && <div className="text-red-400">{error}</div>}
        {data && data.error && <div className="text-red-400">{data.error}</div>}
        {data && data.rows && data.rows.map((row, i) => (
          <div key={i} className="text-xs text-md-on-surface space-y-1">
            {Object.entries(row).map(([k, v]) => (
              <div key={k} className="flex gap-2">
                <span className="text-md-on-surface-var w-40 shrink-0">{k}</span>
                <span className="text-white break-all">{String(v)}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

export default function TZWLNBBPanel() {
  const [universe, setUniverse]         = useState('sp500')
  const [nasdaqBatch, setNasdaqBatch]   = useState('a_m')
  const [gt5Batch, setGt5Batch]         = useState('')   // '' = full scan (no batch)
  const [tf, setTf]                     = useState('1d')
  const [signalType, setSignalType] = useState('all')
  const [signalName, setSignalName] = useState('')
  const [minPrice, setMinPrice] = useState('')
  const [maxPrice, setMaxPrice] = useState('')
  const [minVolume, setMinVolume] = useState('')
  const [recentWindow, setRecentWindow] = useState(1)

  const [results, setResults]   = useState([])
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState(null)
  const [genStatus, setGenStatus]               = useState(null)
  const [genError, setGenError]                 = useState(null)
  const [splitAudit, setSplitAudit]             = useState(null)
  const [splitAuditLoading, setSplitAuditLoading] = useState(false)
  const [splitGenCount, setSplitGenCount]       = useState(null)  // ticker count used at generation
  const [status, setStatus]     = useState(null)

  const [debugRow, setDebugRow] = useState(null)

  // Replay state
  const [replayState, setReplayState]     = useState(null)  // {running, output, error}
  const [replayTopRows, setReplayTopRows] = useState([])
  const [replayTab, setReplayTab]         = useState('signal') // 'signal'|'combo'|'sequence'

  const pollRef       = useRef(null)
  const replayPollRef = useRef(null)

  // Reset gt5Batch when leaving nasdaq_gt5 4H mode
  useEffect(() => {
    if (universe !== 'nasdaq_gt5' || tf !== '4h') setGt5Batch('')
  }, [universe, tf])

  // Poll status while running
  useEffect(() => {
    const poll = async () => {
      try {
        const s = await apiGet('/api/tz-wlnbb/status')
        setStatus(s)
        if (!s.running) {
          clearInterval(pollRef.current)
          pollRef.current = null
        }
      } catch {}
    }
    poll()
  }, [])

  function startPolling() {
    if (pollRef.current) return
    pollRef.current = setInterval(async () => {
      try {
        const s = await apiGet('/api/tz-wlnbb/status')
        setStatus(s)
        if (!s.running) {
          clearInterval(pollRef.current)
          pollRef.current = null
          // After split generation completes, fetch ticker count from audit
          if (universe === 'split' && s.output && !s.error) {
            try {
              const audit = await apiGet(`/api/split-universe/audit?tf=${tf}`)
              setSplitGenCount({
                shared: audit.shared_count,
                stock_stat: audit.stock_stat_count,
                is_consistent: audit.is_consistent,
              })
            } catch {}
          }
        }
      } catch {}
    }, 2000)
  }

  async function handleGenerate() {
    setGenStatus('starting')
    setGenError(null)
    setSplitGenCount(null)
    try {
      const params = { universe, tf, bars: 252 }
      if (universe === 'nasdaq') params.nasdaq_batch = nasdaqBatch
      if (universe === 'nasdaq_gt5' && gt5Batch) params.nasdaq_batch = gt5Batch
      await apiPost('/api/tz-wlnbb/generate-stock-stat', params)
      setGenStatus('started')
      startPolling()
    } catch (e) {
      setGenError(e.message)
      setGenStatus(null)
    }
  }

  async function handleStop() {
    try {
      await apiPost('/api/tz-wlnbb/stop')
    } catch {}
  }

  async function handleScan() {
    setLoading(true)
    setError(null)
    try {
      const qs = new URLSearchParams({
        universe, tf,
        signal_type: signalType,
        recent_window: recentWindow,
      })
      if (universe === 'nasdaq') qs.set('nasdaq_batch', nasdaqBatch)
      if (universe === 'nasdaq_gt5' && gt5Batch) qs.set('nasdaq_batch', gt5Batch)
      if (signalName)  qs.set('signal_name', signalName)
      if (minPrice)    qs.set('min_price', minPrice)
      if (maxPrice)    qs.set('max_price', maxPrice)
      if (minVolume)   qs.set('min_volume', minVolume)
      const data = await apiGet(`/api/tz-wlnbb/scan?${qs}`)
      if (data.error) setError(data.error)
      setResults(data.results || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  // ── Replay polling ────────────────────────────────────────────────────────
  useEffect(() => {
    apiGet('/api/tz-wlnbb/replay/status').then(s => setReplayState(s)).catch(() => {})
  }, [])

  function startReplayPolling() {
    if (replayPollRef.current) return
    replayPollRef.current = setInterval(async () => {
      try {
        const s = await apiGet('/api/tz-wlnbb/replay/status')
        setReplayState(s)
        if (!s.running) {
          clearInterval(replayPollRef.current)
          replayPollRef.current = null
        }
      } catch {}
    }, 2000)
  }

  async function handleReplay() {
    setReplayTopRows([])
    try {
      const params = { universe, tf }
      if (universe === 'nasdaq') params.nasdaq_batch = nasdaqBatch
      if (universe === 'nasdaq_gt5' && gt5Batch) params.nasdaq_batch = gt5Batch
      await apiPost('/api/tz-wlnbb/replay', params)
      startReplayPolling()
    } catch (e) {
      setReplayState({ running: false, error: e.message, output: null })
    }
  }

  const isRunning = status?.running
  const replayRunning = replayState?.running

  return (
    <div className="bg-md-surface text-md-on-surface p-3 flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <span className="text-lg font-bold text-white">📡 TZ/WLNBB Analyzer</span>
        <span className="text-xs text-md-on-surface-var">Pine Script conversion — candlestick + volume analysis</span>
      </div>

      {/* ── Controls ──────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-2 items-end">
        {/* Universe */}
        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">Universe</label>
          <select
            value={universe}
            onChange={e => setUniverse(e.target.value)}
            className="bg-md-surface-high text-md-on-surface text-xs px-2 py-1 rounded border border-md-outline-var"
          >
            {UNIVERSES.map(u => (
              <option key={u.key} value={u.key}>{u.label}</option>
            ))}
          </select>
        </div>

        {/* NASDAQ Batch — only visible when NASDAQ is selected */}
        {universe === 'nasdaq' && (
          <div className="flex flex-col gap-1">
            <label className="text-xs text-md-on-surface-var">Batch</label>
            <div className="flex gap-1">
              {NASDAQ_BATCHES.map(b => (
                <button
                  key={b.key}
                  onClick={() => setNasdaqBatch(b.key)}
                  className={`text-xs px-2 py-1 rounded transition-colors
                    ${nasdaqBatch === b.key
                      ? 'bg-amber-600 text-white font-semibold'
                      : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}
                >
                  {b.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* NASDAQ > $5 Batch — only visible when nasdaq_gt5 + 4H */}
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

        {/* Timeframe */}
        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">Timeframe</label>
          <div className="flex gap-1">
            {TF_OPTS.map(t => (
              <button
                key={t}
                onClick={() => setTf(t)}
                className={`text-xs px-2 py-1 rounded transition-colors
                  ${tf === t ? 'bg-blue-600 text-white font-semibold' : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* Signal Type */}
        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">Signal Type</label>
          <div className="flex flex-wrap gap-1">
            {SIG_TYPES.map(s => (
              <button
                key={s.key}
                onClick={() => setSignalType(s.key)}
                className={`text-xs px-2 py-1 rounded transition-colors
                  ${signalType === s.key ? 'bg-blue-600 text-white font-semibold' : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        {/* Signal Name */}
        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">Signal Name</label>
          <input
            type="text"
            value={signalName}
            onChange={e => setSignalName(e.target.value)}
            placeholder="e.g. T4, Z6, L34…"
            className="bg-md-surface-high text-md-on-surface text-xs px-2 py-1 rounded border border-md-outline-var w-32"
          />
        </div>

        {/* Price filters */}
        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">Min Price</label>
          <input
            type="number"
            value={minPrice}
            onChange={e => setMinPrice(e.target.value)}
            placeholder="0"
            className="bg-md-surface-high text-md-on-surface text-xs px-2 py-1 rounded border border-md-outline-var w-20"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">Max Price</label>
          <input
            type="number"
            value={maxPrice}
            onChange={e => setMaxPrice(e.target.value)}
            placeholder="∞"
            className="bg-md-surface-high text-md-on-surface text-xs px-2 py-1 rounded border border-md-outline-var w-20"
          />
        </div>

        {/* Min Volume */}
        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">Min Volume</label>
          <input
            type="number"
            value={minVolume}
            onChange={e => setMinVolume(e.target.value)}
            placeholder="0"
            className="bg-md-surface-high text-md-on-surface text-xs px-2 py-1 rounded border border-md-outline-var w-24"
          />
        </div>

        {/* Recent Window */}
        <div className="flex flex-col gap-1">
          <label className="text-xs text-md-on-surface-var">Recent Bars</label>
          <input
            type="number"
            value={recentWindow}
            onChange={e => setRecentWindow(Number(e.target.value) || 1)}
            min={1}
            max={10}
            className="bg-md-surface-high text-md-on-surface text-xs px-2 py-1 rounded border border-md-outline-var w-16"
          />
        </div>

        {/* Scan button */}
        <button
          onClick={handleScan}
          disabled={loading}
          className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 text-white text-xs font-semibold rounded transition-colors self-end"
        >
          {loading ? 'Scanning…' : 'Scan'}
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
            title="Audit split universe consistency between Turbo and WLNBB/TZ"
          >
            {splitAuditLoading ? '…' : '🔍 Audit Split'}
          </button>
        )}
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

      {/* ── Generate Stock Stat ──────────────────────────────────────────── */}
      <div className="flex items-center gap-3 p-2 bg-md-surface-con rounded border border-md-outline-var">
        <button
          onClick={handleGenerate}
          disabled={isRunning}
          className="px-3 py-1.5 bg-emerald-700 hover:bg-emerald-600 disabled:bg-gray-700 text-white text-xs font-semibold rounded transition-colors"
        >
          {isRunning ? 'Generating…' : 'Generate Stock Stat'}
        </button>
        {isRunning && (
          <button
            onClick={handleStop}
            className="px-3 py-1.5 bg-red-700 hover:bg-red-600 text-white text-xs font-semibold rounded transition-colors"
          >
            Stop
          </button>
        )}
        <div className="text-xs text-md-on-surface-var">
          Runs TZ/WLNBB analysis on all tickers in the selected universe and saves to CSV.
          {isRunning && status && (
            <span className="ml-2 text-yellow-400">
              Running… {status.done || 0} / {status.total || '?'} tickers
            </span>
          )}
          {status && !status.running && status.output && (
            <span className="ml-2 text-green-400 flex items-center gap-2 flex-wrap">
              Done: {status.output}
              <a
                href={`${BASE}/api/tz-wlnbb/download/${status.output}`}
                download
                className="px-2 py-0.5 bg-blue-700 hover:bg-blue-600 text-white rounded text-xs transition-colors"
              >
                ⬇ CSV
              </a>
              {splitGenCount && universe === 'split' && (
                <span className={`px-2 py-0.5 rounded text-xs font-mono
                  ${splitGenCount.is_consistent ? 'bg-green-900/50 text-green-300' : 'bg-red-900/50 text-red-300'}`}>
                  {splitGenCount.is_consistent ? '✓' : '⚠'} shared {splitGenCount.shared} · stock_stat {splitGenCount.stock_stat}
                </span>
              )}
            </span>
          )}
          {status && !status.running && status.error && (
            <span className="ml-2 text-red-400">Error: {status.error}</span>
          )}
          {genError && (
            <span className="ml-2 text-red-400">{genError}</span>
          )}
        </div>
      </div>

      {/* ── Replay Analytics ────────────────────────────────────────────── */}
      <div className="flex flex-col gap-2 p-2 bg-md-surface-con rounded border border-md-outline-var">
        <div className="flex items-center gap-3 flex-wrap">
          <button
            onClick={handleReplay}
            disabled={replayRunning}
            className="px-3 py-1.5 bg-purple-700 hover:bg-purple-600 disabled:bg-gray-700 text-white text-xs font-semibold rounded transition-colors"
          >
            {replayRunning ? 'Generating Replay…' : '🔄 Generate Replay'}
          </button>
          <span className="text-xs text-md-on-surface-var">
            Reads stock_stat CSV → computes forward returns → generates analytics ZIP.
          </span>
          {replayRunning && (
            <span className="text-xs text-yellow-400 animate-pulse">Running…</span>
          )}
          {replayState && !replayState.running && replayState.output && (
            <a
              href={`${BASE}/api/tz-wlnbb/download/${replayState.output}`}
              download
              className="text-xs px-2 py-1 bg-blue-700 hover:bg-blue-600 text-white rounded transition-colors"
            >
              ⬇ Download ZIP
            </a>
          )}
          {replayState && !replayState.running && replayState.error && (
            <span className="text-xs text-red-400">Error: {replayState.error}</span>
          )}
        </div>
      </div>

      {/* ── Error ────────────────────────────────────────────────────────── */}
      {error && (
        <div className="p-2 bg-red-900/30 border border-red-700 rounded text-red-300 text-xs">
          {error}
        </div>
      )}

      {universe === 'nasdaq_gt5' && tf === '4h' && gt5Batch === '' && (
        <div className="p-2 bg-amber-900/30 border border-amber-700 rounded text-amber-300 text-xs">
          ⚠ Full NASDAQ &gt; $5 4H may be too large for Railway. Batch mode is recommended.
        </div>
      )}

      {/* ── Results count ────────────────────────────────────────────────── */}
      {results.length > 0 && (
        <div className="text-xs text-md-on-surface-var">
          {results.length} result{results.length !== 1 ? 's' : ''}
        </div>
      )}

      {/* ── Results table ────────────────────────────────────────────────── */}
      {results.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="text-md-on-surface-var border-b border-md-outline-var">
                <th className="text-left p-1 font-medium">Ticker</th>
                <th className="text-left p-1 font-medium">Date</th>
                <th className="text-right p-1 font-medium">Close</th>
                <th className="text-right p-1 font-medium">Volume</th>
                <th className="text-left p-1 font-medium">T</th>
                <th className="text-left p-1 font-medium">Z</th>
                <th className="text-left p-1 font-medium">L</th>
                <th className="text-left p-1 font-medium">PRE</th>
                <th className="text-left p-1 font-medium">Lane 1</th>
                <th className="text-left p-1 font-medium">Lane 3</th>
                <th className="text-left p-1 font-medium">NE</th>
                <th className="text-left p-1 font-medium">Wk</th>
                <th className="text-left p-1 font-medium">Vol</th>
                <th className="text-center p-1 font-medium">Debug</th>
              </tr>
            </thead>
            <tbody>
              {results.map((row, i) => {
                const tSig   = row.t_signal     || ''
                const zSig   = row.z_signal     || ''
                const lSig   = row.l_signal     || ''
                const preSig = row.preup_signal || row.predn_signal || ''
                const hasAny = tSig || zSig || lSig || preSig

                return (
                  <tr
                    key={`${row.ticker}-${row.date}-${i}`}
                    className={`border-b border-md-outline-var/50 hover:bg-md-surface-con/50 transition-colors
                      ${hasAny ? '' : 'opacity-60'}`}
                  >
                    <td className="p-1 font-semibold text-white">{row.ticker}</td>
                    <td className="p-1 text-md-on-surface-var">{row.date}</td>
                    <td className="p-1 text-right text-md-on-surface">
                      {row.close ? Number(row.close).toFixed(2) : '—'}
                    </td>
                    <td className="p-1 text-right text-md-on-surface-var">
                      {row.volume
                        ? Number(row.volume) >= 1e6
                          ? `${(Number(row.volume) / 1e6).toFixed(1)}M`
                          : Number(row.volume) >= 1e3
                          ? `${(Number(row.volume) / 1e3).toFixed(0)}K`
                          : row.volume
                        : '—'}
                    </td>
                    <td className="p-1">
                      {tSig ? <Badge text={tSig} color={tBadgeColor(tSig)} /> : null}
                    </td>
                    <td className="p-1">
                      {zSig ? <Badge text={zSig} color={zBadgeColor(zSig)} /> : null}
                    </td>
                    <td className="p-1">
                      {lSig ? <Badge text={lSig} color={lBadgeColor()} /> : null}
                    </td>
                    <td className="p-1">
                      {preSig ? <Badge text={preSig} color={preBadgeColor(preSig)} /> : null}
                    </td>
                    <td className="p-1 text-blue-300 font-mono">{row.lane1_label || ''}</td>
                    <td className="p-1 text-red-300 font-mono">{row.lane3_label || ''}</td>
                    <td className="p-1 text-md-on-surface-var">{row.ne_suffix || ''}</td>
                    <td className="p-1 text-md-on-surface-var">{row.wick_suffix || ''}</td>
                    <td className="p-1 text-md-on-surface-var">
                      <span className={`px-1 rounded text-xs
                        ${row.volume_bucket === 'VB' ? 'text-red-300' :
                          row.volume_bucket === 'B'  ? 'text-orange-300' :
                          row.volume_bucket === 'N'  ? 'text-yellow-300' :
                          row.volume_bucket === 'L'  ? 'text-blue-300' :
                          row.volume_bucket === 'W'  ? 'text-md-on-surface-var' : ''}`}
                      >
                        {row.volume_bucket || ''}
                      </span>
                    </td>
                    <td className="p-1 text-center">
                      <button
                        onClick={() => setDebugRow({ ticker: row.ticker, date: row.date, tf })}
                        className="text-md-on-surface-var hover:text-blue-400 transition-colors text-xs"
                        title="Debug"
                      >
                        🔍
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {!loading && !error && results.length === 0 && (
        <div className="text-md-on-surface-var/70 text-xs py-4 text-center">
          No results. Run "Generate Stock Stat" first, then click "Scan".
        </div>
      )}

      {/* ── Debug Modal ──────────────────────────────────────────────────── */}
      {debugRow && (
        <DebugModal
          ticker={debugRow.ticker}
          date={debugRow.date}
          tf={tf}
          onClose={() => setDebugRow(null)}
        />
      )}
    </div>
  )
}

import { useState, useEffect, useRef } from 'react'
import { api } from '../api'
import { turboCacheSet } from './TurboScanPanel'
import { getCacheBackend, setCacheBackend } from '../turboCache'
import {
  Card, CardHeader,
  Button,
  Badge,
  LinearProgress,
  Alert,
  Stat,
  PageHeader,
  StatusChip,
  EmptyState,
  FilterChip,
} from '../design-system'

const UNIVERSES = [
  { key: 'sp500',     label: 'S&P 500'   },
  { key: 'nasdaq',    label: 'NASDAQ'    },
  { key: 'russell2k', label: 'Russell 2K'},
  { key: 'all_us',    label: 'All US'    },
]
const TFS = ['1d', '4h', '1h', '1wk']

function fmt(sec) {
  if (sec == null) return '—'
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60)
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

function pct(done, total) {
  if (!total) return 0
  return Math.min(100, Math.round((done / total) * 100))
}

function ChipRow({ children }) {
  return <div className="flex flex-wrap gap-1.5">{children}</div>
}

function FilterNumInput({ label, value, onChange, min, max, step = 1, placeholder }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-md-on-surface-var font-medium">{label}</label>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        placeholder={placeholder}
        onChange={e => onChange(e.target.value === '' ? '' : Number(e.target.value))}
        className="w-20 px-2 py-1.5 rounded-md-sm bg-md-surface-high border border-md-outline-var text-md-on-surface text-xs focus:border-md-primary focus:outline-none transition-colors"
      />
    </div>
  )
}

export default function AdminPanel() {
  const [status,   setStatus]   = useState(null)
  const [history,  setHistory]  = useState([])
  const [universe, setUniverse] = useState('sp500')
  const [tf,       setTf]       = useState('1d')
  const [error,    setError]    = useState(null)
  const [priceMin, setPriceMin] = useState('')
  const [priceMax, setPriceMax] = useState('')
  const [rsiMin,   setRsiMin]   = useState('')
  const [rsiMax,   setRsiMax]   = useState('')
  const [cciMin,   setCciMin]   = useState('')
  const [cciMax,   setCciMax]   = useState('')
  const [caching,     setCaching]    = useState(false)
  const [cacheMode,   setCacheModeS] = useState(() => getCacheBackend())
  const pollRef    = useRef(null)
  const scanParams = useRef({ tf: '1d', uni: 'sp500' })

  // ── Stock Stat state ──
  const [ssUni,    setSsUni]    = useState('sp500')
  const [ssTf,     setSsTf]     = useState('1d')
  const [ssBars,   setSsBars]   = useState(150)
  const [ssStatus, setSsStatus] = useState(null)
  const [ssError,  setSsError]  = useState(null)
  const ssPollRef  = useRef(null)

  const setCacheMode  = (val) => { setCacheBackend(val); setCacheModeS(val) }
  const fetchStatus   = () => api.turboScanStatus().then(setStatus).catch(() => {})
  const fetchHistory  = () => api.adminScanHistory().then(setHistory).catch(() => {})
  const fetchSsStatus = () => api.stockStatStatus().then(setSsStatus).catch(() => {})

  useEffect(() => {
    fetchStatus()
    fetchHistory()
    pollRef.current = setInterval(fetchStatus, 2000)
    return () => clearInterval(pollRef.current)
  }, [])

  useEffect(() => {
    fetchSsStatus()
    ssPollRef.current = setInterval(fetchSsStatus, 2000)
    return () => clearInterval(ssPollRef.current)
  }, [])

  const prevRunning = useRef(false)
  useEffect(() => {
    if (prevRunning.current && status && !status.running && !status.error) {
      fetchHistory()
      const { tf: scanTf, uni: scanUni } = scanParams.current
      setCaching(true)
      api.turboScan(10000, 0, 'all', scanTf, scanUni, {})
        .then(d => {
          const results = d.results || []
          if (results.length > 0) {
            turboCacheSet(scanTf, scanUni, results, d.last_scan)
            try {
              localStorage.setItem('sachoki_turbo_tf',  scanTf)
              localStorage.setItem('sachoki_turbo_uni', scanUni)
            } catch {}
            window.dispatchEvent(new CustomEvent('sachoki:scan-cached', {
              detail: { tf: scanTf, uni: scanUni, results, lastScan: d.last_scan },
            }))
          }
        })
        .finally(() => setCaching(false))
    }
    prevRunning.current = status?.running ?? false
  }, [status?.running])

  const startScan = () => {
    setError(null)
    scanParams.current = { tf, uni: universe }
    api.adminScanStart(tf, universe, cacheMode === 'idb' ? 0 : 5)
      .then(() => fetchStatus())
      .catch(e => setError(e?.detail || e?.message || String(e)))
  }

  const resetScan = () => {
    api.turboScanReset().then(() => fetchStatus())
  }

  const startStockStat = () => {
    setSsError(null)
    api.stockStatTrigger(ssTf, ssUni, ssBars)
      .then(() => fetchSsStatus())
      .catch(e => setSsError(e?.detail || e?.message || String(e)))
  }

  const running  = status?.running ?? false
  const done     = status?.done    ?? 0
  const total    = status?.total   ?? 0
  const found    = status?.found   ?? 0
  const failed   = status?.failed  ?? 0
  const elapsed  = status?.elapsed ?? 0
  const eta      = status?.eta
  const scanErr  = status?.error
  const progress = pct(done, total)

  return (
    <div className="p-4 max-w-3xl mx-auto space-y-5">

      {/* ── Page Header ── */}
      <PageHeader
        title="Scan Admin"
        subtitle="Manage market scans and bulk signal computation"
        badge={
          <StatusChip
            status={running ? 'running' : 'idle'}
            label={running ? 'Running' : 'Idle'}
          />
        }
      />

      {/* ── Cache Backend ── */}
      <Card variant="outlined">
        <CardHeader title="Cache Backend" subtitle="Where scan results are stored between sessions" />
        <div className="flex gap-2 flex-wrap">
          {[
            { key: 'ls',  label: 'localStorage',  desc: 'score≥5 filter · fast · 5 MB limit' },
            { key: 'idb', label: 'IndexedDB',      desc: 'all tickers · no size limit · robust' },
          ].map(m => (
            <button
              key={m.key}
              onClick={() => setCacheMode(m.key)}
              className={[
                'flex flex-col items-start px-3 py-2 rounded-md-md border text-xs transition-colors',
                cacheMode === m.key
                  ? 'bg-md-primary-container border-md-primary/40 text-md-on-primary-container'
                  : 'bg-md-surface-high border-md-outline-var text-md-on-surface-var hover:border-md-outline',
              ].join(' ')}
            >
              <span className="font-semibold">{m.label}</span>
              <span className="opacity-70 mt-0.5">{m.desc}</span>
            </button>
          ))}
        </div>
        <p className="text-md-on-surface-var text-xs mt-3">
          {cacheMode === 'idb'
            ? 'IndexedDB mode: all tickers stored (no score filter). Scan saves ~5000 results.'
            : 'localStorage mode: only score≥5 tickers stored (~2000). Smaller, faster.'}
        </p>
      </Card>

      {/* ── Start New Scan ── */}
      <Card variant="outlined">
        <CardHeader title="Start New Scan" />
        <div className="space-y-3">

          <div className="flex flex-wrap gap-4">
            <div>
              <p className="text-md-on-surface-var text-xs mb-1.5">Universe</p>
              <ChipRow>
                {UNIVERSES.map(u => (
                  <FilterChip
                    key={u.key}
                    label={u.label}
                    selected={universe === u.key}
                    disabled={running}
                    onToggle={() => setUniverse(u.key)}
                  />
                ))}
              </ChipRow>
            </div>
            <div>
              <p className="text-md-on-surface-var text-xs mb-1.5">Timeframe</p>
              <ChipRow>
                {TFS.map(t => (
                  <FilterChip
                    key={t}
                    label={t}
                    selected={tf === t}
                    disabled={running}
                    onToggle={() => setTf(t)}
                  />
                ))}
              </ChipRow>
            </div>
          </div>

          {/* Filters */}
          <div className="border-t border-md-outline-var pt-3">
            <p className="text-md-on-surface-var text-xs mb-2">Result Filters</p>
            <div className="flex flex-wrap gap-4">
              <div className="flex gap-2 items-end">
                <FilterNumInput label="Price Min $" value={priceMin} onChange={setPriceMin} min={0} step={0.1} placeholder="0" />
                <FilterNumInput label="Price Max $" value={priceMax} onChange={setPriceMax} min={0} step={1}   placeholder="∞" />
              </div>
              <div className="flex gap-2 items-end">
                <FilterNumInput label="RSI Min" value={rsiMin} onChange={setRsiMin} min={0}   max={100} placeholder="0"   />
                <FilterNumInput label="RSI Max" value={rsiMax} onChange={setRsiMax} min={0}   max={100} placeholder="100" />
              </div>
              <div className="flex gap-2 items-end">
                <FilterNumInput label="CCI Min" value={cciMin} onChange={setCciMin} min={-500} max={500} step={10} placeholder="-∞" />
                <FilterNumInput label="CCI Max" value={cciMax} onChange={setCciMax} min={-500} max={500} step={10} placeholder="∞"  />
              </div>
            </div>
          </div>

          <div className="flex gap-2 pt-1">
            <Button variant="filled" onClick={startScan} disabled={running}>
              ⚡ Start Scan
            </Button>
            {running && (
              <Button variant="outlined" onClick={resetScan}>
                Force Stop
              </Button>
            )}
          </div>

          {error && <Alert variant="error">{error}</Alert>}
        </div>
      </Card>

      {/* ── Live Progress ── */}
      {(running || scanErr || (status && done > 0)) && (
        <Card variant="outlined">
          <CardHeader
            title={running ? 'Live Progress' : 'Last Scan'}
            action={
              <Badge variant={running ? 'warning' : scanErr ? 'error' : 'positive'}>
                {running ? `${progress}%` : scanErr ? 'Error' : 'Complete'}
              </Badge>
            }
          />

          <LinearProgress
            value={progress}
            color={running ? 'warning' : 'positive'}
            className="mb-4"
          />

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Stat label="Fetched"    value={status?.fetched_from_massive || total || '—'} />
            <Stat label="Scanned"    value={`${done} / ${total}`} />
            <Stat label="Into Turbo" value={found}  positive />
            <Stat label="Failed"     value={failed} negative={failed > 0} />
            <Stat label="Elapsed"    value={fmt(elapsed)} />
            <Stat label="ETA"        value={running ? fmt(eta) : '—'} />
            <Stat label="Universe"   value={status?.universe ?? '—'} />
            <Stat label="Timeframe"  value={status?.tf ?? '—'} />
          </div>

          {scanErr && (
            <Alert variant="error" className="mt-3">Error: {scanErr}</Alert>
          )}
          {!running && !scanErr && done > 0 && (
            <Alert variant="success" className="mt-3">
              Scan completed — {found} tickers
              {caching ? ' · saving to Turbo cache…' : ' · cached in Turbo ✓'}
            </Alert>
          )}
        </Card>
      )}

      {/* ── Scan History ── */}
      <Card variant="outlined">
        <CardHeader title="Scan History" subtitle="Last 20 runs" />
        {history.length === 0 ? (
          <EmptyState compact message="No scans yet" icon="📋" />
        ) : (
          <div className="overflow-x-auto -mx-4 px-4">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-md-outline-var">
                  {['#', 'Universe', 'TF', 'Started', 'Duration', 'Results'].map((h, i) => (
                    <th
                      key={h}
                      className={`py-2 pr-3 text-md-on-surface-var font-medium ${i === 5 ? 'text-right pr-0' : 'text-left'}`}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {history.map(r => {
                  const dur = r.started_at && r.completed_at
                    ? Math.round((new Date(r.completed_at) - new Date(r.started_at)) / 1000)
                    : null
                  const complete = r.completed_at != null
                  return (
                    <tr key={r.id} className="border-b border-md-outline-var/50 hover:bg-md-surface-high/50 transition-colors">
                      <td className="py-2 pr-3 text-md-on-surface-var">{r.id}</td>
                      <td className="py-2 pr-3 text-md-on-surface">{r.universe}</td>
                      <td className="py-2 pr-3 text-md-on-surface-var">{r.tf}</td>
                      <td className="py-2 pr-3 text-md-on-surface-var">{r.started_at?.slice(0, 16).replace('T', ' ')}</td>
                      <td className="py-2 pr-3 text-md-on-surface-var">
                        {complete
                          ? fmt(dur)
                          : <span className="text-md-warning">running…</span>}
                      </td>
                      <td className="py-2 text-right font-mono">
                        <span className={r.result_count > 0 ? 'text-md-positive' : 'text-md-on-surface-var'}>
                          {r.result_count ?? '—'}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ── Stock Stat ── */}
      <Card variant="outlined">
        <CardHeader
          title="Stock Stat — Bulk Signal CSV"
          subtitle="Bar-level signal computation for every ticker → combined CSV export"
          action={ssStatus?.running ? <StatusChip status="running" /> : undefined}
        />
        <p className="text-md-on-surface-var text-xs mb-3">
          S&P 500 ≈ 5–10 min · Russell 2K ≈ 20–40 min · All US ≈ 1–2 h
        </p>

        <div className="space-y-3">
          <div className="flex flex-wrap gap-4">
            <div>
              <p className="text-md-on-surface-var text-xs mb-1.5">Universe</p>
              <ChipRow>
                {UNIVERSES.map(u => (
                  <FilterChip
                    key={u.key}
                    label={u.label}
                    selected={ssUni === u.key}
                    disabled={ssStatus?.running}
                    onToggle={() => setSsUni(u.key)}
                  />
                ))}
              </ChipRow>
            </div>
            <div>
              <p className="text-md-on-surface-var text-xs mb-1.5">Timeframe</p>
              <ChipRow>
                {TFS.map(t => (
                  <FilterChip
                    key={t}
                    label={t}
                    selected={ssTf === t}
                    disabled={ssStatus?.running}
                    onToggle={() => setSsTf(t)}
                  />
                ))}
              </ChipRow>
            </div>
            <FilterNumInput
              label="Bars"
              value={ssBars}
              onChange={v => setSsBars(v === '' ? 150 : Number(v))}
              min={10}
              max={500}
            />
          </div>

          <Button
            variant="tonal"
            onClick={startStockStat}
            disabled={ssStatus?.running}
          >
            📊 Run Stock Stat
          </Button>

          {/* Progress */}
          {ssStatus && (ssStatus.running || ssStatus.output_path || ssStatus.error) && (
            <div className="space-y-2 pt-1">
              <LinearProgress
                value={pct(ssStatus.done ?? 0, ssStatus.total || 1)}
                color={ssStatus.running ? 'primary' : 'positive'}
              />
              <div className="flex flex-wrap gap-4 text-xs text-md-on-surface-var">
                <span>{ssStatus.done ?? 0} / {ssStatus.total ?? 0} tickers</span>
                <span>{fmt(ssStatus.elapsed)}</span>
                {ssStatus.universe && (
                  <span>{ssStatus.universe} · {ssStatus.tf}</span>
                )}
              </div>
              {ssStatus.error && (
                <Alert variant="error">Error: {ssStatus.error}</Alert>
              )}
              {!ssStatus.running && !ssStatus.error && ssStatus.output_path && (
                <div className="flex items-center gap-3">
                  <Alert variant="success" className="flex-1">
                    Done — {ssStatus.done} tickers ·{' '}
                    {Math.round((ssStatus.output_size ?? 0) / 1024 / 1024 * 10) / 10} MB
                  </Alert>
                  <a
                    href={api.stockStatDownloadUrl()}
                    download
                    className="shrink-0 inline-flex items-center px-3 py-1.5 rounded-md-md text-xs font-semibold bg-md-positive text-black hover:opacity-90 transition-opacity"
                  >
                    ⬇ Download CSV
                  </a>
                </div>
              )}
            </div>
          )}
          {ssError && <Alert variant="error">{ssError}</Alert>}
        </div>
      </Card>

    </div>
  )
}

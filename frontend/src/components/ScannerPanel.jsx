import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../api'
import { exportToTV } from '../utils/exportTickers'
import {
  Card,
  Button,
  Badge,
  LinearProgress,
  Alert,
  Spinner,
  EmptyState,
  AssistChip,
} from '../design-system'

function SigBadge({ sig_id, sig_name }) {
  const bull = sig_id >= 1  && sig_id <= 12
  const bear = sig_id >= 13 && sig_id <= 25
  if (!bull && !bear) return null
  return (
    <Badge variant={bull ? 'positive' : 'negative'} size="sm">
      {sig_name}
    </Badge>
  )
}

export default function ScannerPanel({ tf, onSelectTicker }) {
  const [results,  setResults]  = useState([])
  const [lastScan, setLastScan] = useState(null)
  const [loading,  setLoading]  = useState(false)
  const [scanning, setScanning] = useState(false)
  const [progress, setProgress] = useState(null)
  const [error,    setError]    = useState(null)
  const pollRef = useRef(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    api.scanResults(tf, 100)
      .then(d => {
        setResults(d.results || [])
        setLastScan(d.last_scan)
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [tf])

  useEffect(() => { load() }, [load])

  const startPolling = () => {
    if (pollRef.current) return
    pollRef.current = setInterval(() => {
      api.scanStatus()
        .then(s => {
          setProgress({ done: s.done, total: s.total, found: s.found })
          if (!s.running) {
            stopPolling()
            setScanning(false)
            setProgress(null)
            load()
          }
        })
        .catch(() => {})
    }, 1000)
  }

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  useEffect(() => () => stopPolling(), [])

  const scan = () => {
    setScanning(true)
    setProgress({ done: 0, total: 0, found: 0 })
    setError(null)
    api.scanTrigger(tf)
      .then(() => startPolling())
      .catch(e => { setError(e.message); setScanning(false); setProgress(null) })
  }

  const fmtTime = (iso) => {
    if (!iso) return null
    try { return new Date(iso).toLocaleString() } catch { return iso }
  }

  const pct = progress && progress.total > 0
    ? Math.round((progress.done / progress.total) * 100)
    : 0

  return (
    <Card variant="outlined" padding="none" className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-md-outline-var">
        <div className="flex items-center gap-3">
          <span className="font-semibold text-sm text-md-on-surface">T/Z Scanner</span>
          {lastScan && !scanning && (
            <span className="text-xs text-md-on-surface-var">Last: {fmtTime(lastScan)}</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {results.length > 0 && (
            <AssistChip
              label="Export TV"
              icon="⬇"
              onClick={() => exportToTV(results.map(r => r.ticker), 'tz_scanner.txt')}
            />
          )}
          <Button
            variant="tonal"
            size="sm"
            onClick={scan}
            disabled={scanning}
            loading={scanning}
          >
            {scanning ? 'Scanning…' : 'Scan Now'}
          </Button>
        </div>
      </div>

      {/* Progress */}
      {scanning && progress && (
        <div className="px-4 py-2 border-b border-md-outline-var space-y-1.5">
          <div className="flex items-center justify-between text-xs text-md-on-surface-var">
            <span>
              {progress.total > 0
                ? `${progress.done} / ${progress.total} tickers`
                : 'Starting…'}
            </span>
            <span className="text-md-positive">{progress.found} signals found</span>
          </div>
          <LinearProgress value={pct} color="primary" />
          {pct > 0 && (
            <div className="text-right text-xs text-md-on-surface-var">{pct}%</div>
          )}
        </div>
      )}

      {error && (
        <div className="px-4 py-2">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      {/* Table */}
      <div className="overflow-auto flex-1">
        {loading ? (
          <div className="flex items-center justify-center py-12 gap-2 text-md-on-surface-var text-xs">
            <Spinner size={14} />
            <span>Loading…</span>
          </div>
        ) : results.length === 0 ? (
          <EmptyState
            icon="📡"
            message="No results"
            sub="Trigger a scan first to see T/Z signals."
            compact
          />
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-md-outline-var sticky top-0 bg-md-surface-con">
                <th className="text-left px-3 py-2 text-md-on-surface-var font-medium">Ticker</th>
                <th className="text-center px-2 py-2 text-md-on-surface-var font-medium">Signal</th>
                <th className="text-left px-2 py-2 text-md-on-surface-var font-medium hidden md:table-cell">3-Bar Pattern</th>
                <th className="text-right px-2 py-2 text-md-on-surface-var font-medium">Price</th>
                <th className="text-right px-2 py-2 text-md-on-surface-var font-medium">Chg%</th>
              </tr>
            </thead>
            <tbody>
              {results.map((row, i) => (
                <tr
                  key={i}
                  onClick={() => onSelectTicker?.(row.ticker)}
                  className="border-b border-md-outline-var/50 cursor-pointer hover:bg-md-surface-high/50 transition-colors"
                >
                  <td className="px-3 py-2 font-semibold text-md-on-surface">{row.ticker}</td>
                  <td className="text-center px-2 py-2">
                    <SigBadge sig_id={row.sig_id} sig_name={row.sig_name} />
                  </td>
                  <td className="px-2 py-2 text-md-on-surface-var font-mono hidden md:table-cell max-w-[160px] truncate">
                    {row.pattern_3bar}
                  </td>
                  <td className="text-right px-2 py-2 text-md-on-surface">
                    {row.last_price ? `$${Number(row.last_price).toFixed(2)}` : '—'}
                  </td>
                  <td className={`text-right px-2 py-2 font-medium ${
                    (row.change_pct ?? 0) >= 0 ? 'text-md-positive' : 'text-md-negative'
                  }`}>
                    {row.change_pct != null
                      ? `${row.change_pct >= 0 ? '+' : ''}${Number(row.change_pct).toFixed(2)}%`
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Card>
  )
}

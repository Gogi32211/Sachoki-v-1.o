import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'

function fmt(v, digits = 2) {
  if (v == null) return '—'
  const n = Number(v)
  return isNaN(n) ? '—' : n.toFixed(digits)
}

function RetBadge({ v }) {
  if (v == null) return <span className="text-md-on-surface-var">—</span>
  const n = Number(v)
  const cls = n >= 0 ? 'text-green-400' : 'text-red-400'
  return <span className={cls}>{n >= 0 ? '+' : ''}{fmt(v)}%</span>
}

function ReasonBadge({ r }) {
  if (!r) return <span className="text-md-on-surface-var">—</span>
  const cls = r === 'TP' ? 'bg-green-800 text-green-200'
            : r === 'SL' ? 'bg-red-800 text-red-200'
            : 'bg-gray-700 text-md-on-surface'
  return <span className={`px-1.5 py-0.5 rounded text-xs font-mono ${cls}`}>{r}</span>
}

function ZoneBadge({ z }) {
  if (!z) return null
  const cls = z === 'OPTIMAL'     ? 'bg-green-700 text-green-100'
            : z === 'WATCH'       ? 'bg-blue-700 text-blue-100'
            : z === 'BUILDING'    ? 'bg-yellow-700 text-yellow-100'
            : z === 'SHORT_WATCH' ? 'bg-red-700 text-red-100'
            : 'bg-gray-700 text-md-on-surface'
  return <span className={`px-1.5 py-0.5 rounded text-xs ${cls}`}>{z}</span>
}

// ── Open Positions ────────────────────────────────────────────────────────────

function OpenPositions({ positions }) {
  if (!positions.length) return (
    <div className="text-md-on-surface-var text-sm py-6 text-center">No open positions</div>
  )
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs text-md-on-surface border-collapse">
        <thead>
          <tr className="text-md-on-surface-var border-b border-md-outline-var">
            <th className="text-left py-1.5 pr-3">Ticker</th>
            <th className="text-left pr-3">Zone</th>
            <th className="text-left pr-3">T/Z</th>
            <th className="text-left pr-3">Tier</th>
            <th className="text-right pr-3">Entry</th>
            <th className="text-right pr-3">TP 20%</th>
            <th className="text-right pr-3">TP 15%</th>
            <th className="text-right pr-3">SL</th>
            <th className="text-right pr-3">Score</th>
            <th className="text-right">Entry Date</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p, i) => (
            <tr key={i} className="border-b border-md-outline-var hover:bg-md-surface-high/40">
              <td className="py-1.5 pr-3 font-bold text-yellow-300">{p.ticker}</td>
              <td className="pr-3"><ZoneBadge z={p.beta_zone} /></td>
              <td className="pr-3 font-mono text-cyan-400">{p.tz_sig || '—'}</td>
              <td className="pr-3 text-md-on-surface-var">{p.tier}</td>
              <td className="text-right pr-3 font-mono">${fmt(p.entry_price)}</td>
              <td className="text-right pr-3 font-mono text-green-400">${fmt(p.tp_parabolic)}</td>
              <td className="text-right pr-3 font-mono text-green-300">${fmt(p.tp_wide)}</td>
              <td className="text-right pr-3 font-mono text-red-400">${fmt(p.sl_price)}</td>
              <td className="text-right pr-3 text-purple-300">{p.ultra_score ?? '—'}</td>
              <td className="text-right text-md-on-surface-var">{p.entry_date}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Stats Summary ─────────────────────────────────────────────────────────────

function StatCard({ label, value, sub }) {
  return (
    <div className="bg-md-surface-high rounded-lg p-3 flex flex-col gap-0.5">
      <div className="text-xs text-md-on-surface-var">{label}</div>
      <div className="text-lg font-bold text-white">{value}</div>
      {sub && <div className="text-xs text-md-on-surface-var">{sub}</div>}
    </div>
  )
}

function StatsPanel({ stats, days }) {
  if (!stats) return null
  const o = stats.overall || {}
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="Total Signals" value={o.total ?? '—'} sub={`${days}d window`} />
        <StatCard label="Closed" value={o.closed ?? '—'} sub={`${o.open ?? 0} open`} />
        <StatCard
          label="PARABOLIC avg"
          value={o.avg_ret_p != null ? `${o.avg_ret_p >= 0 ? '+' : ''}${fmt(o.avg_ret_p)}%` : '—'}
          sub={`TP ${o.tp_rate_p ?? '—'}% · SL ${o.sl_rate_p ?? '—'}%`}
        />
        <StatCard
          label="WIDE avg"
          value={o.avg_ret_w != null ? `${o.avg_ret_w >= 0 ? '+' : ''}${fmt(o.avg_ret_w)}%` : '—'}
          sub={`TP ${o.tp_rate_w ?? '—'}% · SL ${o.sl_rate_w ?? '—'}%`}
        />
      </div>

      {/* By Zone */}
      {stats.by_zone?.length > 0 && (
        <div>
          <div className="text-xs text-md-on-surface-var mb-1.5">By Beta Zone</div>
          <table className="w-full text-xs text-md-on-surface border-collapse">
            <thead>
              <tr className="text-md-on-surface-var border-b border-md-outline-var">
                <th className="text-left py-1 pr-4">Zone</th>
                <th className="text-right pr-4">N</th>
                <th className="text-right pr-4">PARA avg</th>
                <th className="text-right pr-4">WIDE avg</th>
                <th className="text-right">TP rate</th>
              </tr>
            </thead>
            <tbody>
              {stats.by_zone.map((r, i) => (
                <tr key={i} className="border-b border-md-outline-var">
                  <td className="py-1 pr-4"><ZoneBadge z={r.beta_zone} /></td>
                  <td className="text-right pr-4 text-md-on-surface-var">{r.n}</td>
                  <td className="text-right pr-4"><RetBadge v={r.avg_p} /></td>
                  <td className="text-right pr-4"><RetBadge v={r.avg_w} /></td>
                  <td className="text-right text-md-on-surface">{r.tp_rate_p != null ? `${r.tp_rate_p}%` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* By Signal */}
      {stats.by_signal?.length > 0 && (
        <div>
          <div className="text-xs text-md-on-surface-var mb-1.5">By T/Z Signal</div>
          <table className="w-full text-xs text-md-on-surface border-collapse">
            <thead>
              <tr className="text-md-on-surface-var border-b border-md-outline-var">
                <th className="text-left py-1 pr-4">Signal</th>
                <th className="text-right pr-4">N</th>
                <th className="text-right pr-4">PARA avg</th>
                <th className="text-right">WIDE avg</th>
              </tr>
            </thead>
            <tbody>
              {stats.by_signal.map((r, i) => (
                <tr key={i} className="border-b border-md-outline-var">
                  <td className="py-1 pr-4 font-mono text-cyan-400">{r.tz_sig || '—'}</td>
                  <td className="text-right pr-4 text-md-on-surface-var">{r.n}</td>
                  <td className="text-right pr-4"><RetBadge v={r.avg_p} /></td>
                  <td className="text-right"><RetBadge v={r.avg_w} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Recent Trades ─────────────────────────────────────────────────────────────

function RecentTrades({ entries }) {
  const closed = entries.filter(e => e.status === 'CLOSED').slice(0, 30)
  if (!closed.length) return (
    <div className="text-md-on-surface-var text-sm py-6 text-center">No closed trades yet</div>
  )
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs text-md-on-surface border-collapse">
        <thead>
          <tr className="text-md-on-surface-var border-b border-md-outline-var">
            <th className="text-left py-1.5 pr-3">Date</th>
            <th className="text-left pr-3">Ticker</th>
            <th className="text-left pr-3">Zone</th>
            <th className="text-right pr-3">Entry</th>
            <th className="text-right pr-3">PARA</th>
            <th className="text-center pr-3">P.Exit</th>
            <th className="text-right pr-3">WIDE</th>
            <th className="text-center">W.Exit</th>
          </tr>
        </thead>
        <tbody>
          {closed.map((e, i) => (
            <tr key={i} className="border-b border-md-outline-var hover:bg-md-surface-high/40">
              <td className="py-1.5 pr-3 text-md-on-surface-var">{e.signal_date}</td>
              <td className="pr-3 font-bold text-yellow-300">{e.ticker}</td>
              <td className="pr-3"><ZoneBadge z={e.beta_zone} /></td>
              <td className="text-right pr-3 font-mono">${fmt(e.entry_price)}</td>
              <td className="text-right pr-3"><RetBadge v={e.realized_return_p} /></td>
              <td className="text-center pr-3"><ReasonBadge r={e.exit_reason_p} /></td>
              <td className="text-right pr-3"><RetBadge v={e.realized_return_w} /></td>
              <td className="text-center"><ReasonBadge r={e.exit_reason_w} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Pending Positions ─────────────────────────────────────────────────────────

function PendingPositions({ pending, onRefresh }) {
  const [prices, setPrices] = useState({})
  const [saving, setSaving] = useState({})
  const today = new Date().toISOString().slice(0, 10)

  if (!pending.length) return (
    <div className="text-md-on-surface-var text-sm py-6 text-center">No pending positions</div>
  )

  const setPrice = async (ticker, signalDate) => {
    const raw = prices[ticker]
    const price = parseFloat(raw)
    if (!raw || isNaN(price) || price <= 0) {
      alert('Enter a valid entry price')
      return
    }
    setSaving(s => ({ ...s, [ticker]: true }))
    try {
      const r = await api.portfolioSetEntryPrice([{
        ticker,
        entry_date: today,
        open_price: price,
      }])
      if (r.updated > 0) {
        onRefresh()
      } else {
        alert('No rows updated — position may already be open')
      }
    } catch (err) {
      alert('Failed: ' + err.message)
    } finally {
      setSaving(s => ({ ...s, [ticker]: false }))
    }
  }

  return (
    <div className="overflow-x-auto">
      <div className="text-xs text-md-on-surface-var mb-2">
        Enter tomorrow's open price for each pending signal to move it to Open.
      </div>
      <table className="w-full text-xs text-md-on-surface border-collapse">
        <thead>
          <tr className="text-md-on-surface-var border-b border-md-outline-var">
            <th className="text-left py-1.5 pr-3">Ticker</th>
            <th className="text-left pr-3">Zone</th>
            <th className="text-left pr-3">T/Z</th>
            <th className="text-left pr-3">Tier</th>
            <th className="text-right pr-3">Score</th>
            <th className="text-right pr-3">Signal Date</th>
            <th className="text-right pr-3">Signal $</th>
            <th className="text-right">Entry Price</th>
          </tr>
        </thead>
        <tbody>
          {pending.map((p, i) => (
            <tr key={i} className="border-b border-md-outline-var hover:bg-md-surface-high/40">
              <td className="py-1.5 pr-3 font-bold text-yellow-300">{p.ticker}</td>
              <td className="pr-3"><ZoneBadge z={p.beta_zone} /></td>
              <td className="pr-3 font-mono text-cyan-400">{p.tz_sig || '—'}</td>
              <td className="pr-3 text-md-on-surface-var">{p.tier}</td>
              <td className="text-right pr-3 text-purple-300">{p.ultra_score ?? '—'}</td>
              <td className="text-right pr-3 text-md-on-surface-var">{p.signal_date}</td>
              <td className="text-right pr-3 font-mono">{p.signal_price ? `$${fmt(p.signal_price)}` : '—'}</td>
              <td className="text-right">
                <div className="flex items-center gap-1 justify-end">
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    placeholder="0.00"
                    value={prices[p.ticker] || ''}
                    onChange={e => setPrices(prev => ({ ...prev, [p.ticker]: e.target.value }))}
                    className="w-20 bg-md-surface-con border border-gray-600 rounded px-1.5 py-0.5 text-xs text-white text-right focus:border-blue-500 outline-none"
                  />
                  <button
                    onClick={() => setPrice(p.ticker, p.signal_date)}
                    disabled={saving[p.ticker]}
                    className="px-2 py-0.5 text-xs rounded bg-green-700 hover:bg-green-600 disabled:opacity-50 text-white whitespace-nowrap"
                  >
                    {saving[p.ticker] ? '…' : 'Set Entry'}
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Main Panel ────────────────────────────────────────────────────────────────

export default function PortfolioPanel() {
  const [tab, setTab]         = useState('open')
  const [open, setOpen]       = useState([])
  const [pending, setPending] = useState([])
  const [stats, setStats]     = useState(null)
  const [entries, setEntries] = useState([])
  const [days, setDays]       = useState(90)
  const [universe, setUniverse] = useState('sp500')
  const [loading, setLoading] = useState(false)
  const [adding, setAdding]   = useState(false)
  const [error, setError]     = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [o, pend, s, e] = await Promise.all([
        api.portfolioOpen(),
        api.portfolioList({ status: 'PENDING', days: 30 }),
        api.portfolioStats(days),
        api.portfolioList({ days, status: tab === 'trades' ? 'CLOSED' : undefined }),
      ])
      setOpen(o.positions || [])
      setPending(pend.entries || [])
      setStats(s)
      setEntries(e.entries || [])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [days, tab])

  useEffect(() => { load() }, [load])

  const runCheck = async () => {
    try {
      const r = await api.portfolioDailyCheck()
      alert(`Checked ${r.checked} positions, closed ${r.closed}`)
      load()
    } catch (err) {
      alert('Check failed: ' + err.message)
    }
  }

  const generatePicks = async () => {
    setAdding(true)
    try {
      const r = await api.portfolioScanAndAdd(universe, '1d')
      const msg = r.warning
        ? r.warning
        : `Added ${r.inserted} (TIER1: ${r.tier1}, TIER2: ${r.tier2}), skipped ${r.skipped} duplicates.`
      alert(msg)
      load()
    } catch (err) {
      alert('Generate failed: ' + err.message)
    } finally {
      setAdding(false)
    }
  }

  return (
    <div className="p-4 text-sm text-md-on-surface max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <h2 className="text-lg font-bold text-white">📋 Paper Portfolio</h2>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            value={universe}
            onChange={e => setUniverse(e.target.value)}
            className="bg-md-surface-high border border-gray-600 rounded px-2 py-1 text-xs text-md-on-surface"
          >
            <option value="sp500">S&P 500</option>
            <option value="nasdaq">NASDAQ</option>
            <option value="all_us">All US</option>
          </select>
          <button
            onClick={generatePicks}
            disabled={adding}
            className="px-3 py-1 text-xs rounded bg-purple-700 hover:bg-purple-600 disabled:opacity-50 text-white"
            title="Pull cached ULTRA results and add today's TIER1/TIER2 picks"
          >
            {adding ? 'Adding…' : '✨ Generate Today\'s Picks'}
          </button>
          <select
            value={days}
            onChange={e => setDays(Number(e.target.value))}
            className="bg-md-surface-high border border-gray-600 rounded px-2 py-1 text-xs text-md-on-surface"
          >
            {[30, 60, 90, 180].map(d => (
              <option key={d} value={d}>{d}d</option>
            ))}
          </select>
          <button
            onClick={runCheck}
            className="px-3 py-1 text-xs rounded bg-blue-700 hover:bg-blue-600 text-white"
          >
            Run TP/SL Check
          </button>
          <button
            onClick={load}
            disabled={loading}
            className="px-3 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600 text-white"
          >
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
      </div>

      {error && (
        <div className="text-red-400 text-xs mb-3 p-2 bg-red-900/20 rounded">{error}</div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-4 border-b border-md-outline-var">
        {[
          { id: 'pending', label: `Pending (${pending.length})` },
          { id: 'open',    label: `Open (${open.length})` },
          { id: 'stats',   label: 'Stats' },
          { id: 'trades',  label: 'Recent Trades' },
        ].map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-1.5 text-xs rounded-t transition-colors
              ${tab === t.id
                ? 'bg-gray-700 text-white border-b-2 border-blue-500'
                : 'text-md-on-surface-var hover:text-md-on-surface'}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      {tab === 'pending' && <PendingPositions pending={pending} onRefresh={load} />}
      {tab === 'open'    && <OpenPositions positions={open} />}
      {tab === 'stats'   && <StatsPanel stats={stats} days={days} />}
      {tab === 'trades'  && <RecentTrades entries={entries} />}
    </div>
  )
}

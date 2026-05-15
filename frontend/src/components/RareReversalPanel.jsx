import { useState } from 'react'

const BASE = import.meta.env.VITE_API_URL || ''

const UNIVERSES = [
  { value: 'sp500',      label: 'S&P 500' },
  { value: 'nasdaq_gt5', label: 'NASDAQ >$5' },
  { value: 'nasdaq',     label: 'NASDAQ' },
  { value: 'russell2k',  label: 'Russell 2K' },
  { value: 'split',      label: 'Split Universe' },
]
const TFS = ['1d', '4h', '1h', '1wk']

const TIER_COLORS = {
  CONFIRMED_RARE:  { bg: '#064e3b', text: '#6ee7b7', border: '#059669' },
  ANECDOTAL_RARE:  { bg: '#1e3a5f', text: '#93c5fd', border: '#3b82f6' },
  FORMING_PATTERN: { bg: '#2d1b69', text: '#c4b5fd', border: '#7c3aed' },
  NO_DATA:         { bg: '#1f2937', text: '#9ca3af', border: '#374151' },
}

const CSV_COLS = [
  'rank', 'ticker', 'evidence_tier', 'forming_subtype',
  'base4_key', 'base4_tier', 'base4_med10d', 'base4_fail10d',
  'extended5_key', 'extended6_key',
  'pattern_length', 'pattern_count',
  'sequence_low_bar_offset', 'sequence_contains_20bar_low',
  'return_from_sequence_low_to_final',
  'median_10d_return', 'win_rate_10d', 'fail_rate_10d',
  'score', 'last_seen_date', 'is_currently_active', 'current_pattern_completion',
  'example_dates',
]

function exportCSV(rows, universe, tf) {
  const lines = [CSV_COLS.join(',')]
  for (const r of rows) {
    lines.push(CSV_COLS.map(c => {
      let v = r[c] ?? ''
      if (Array.isArray(v)) v = v.join(';')
      v = String(v)
      if (/^[=+\-@]/.test(v)) v = "'" + v
      return v.includes(',') || v.includes('"') || v.includes('\n')
        ? `"${v.replace(/"/g, '""')}"` : v
    }).join(','))
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `rare_reversal_${universe}_${tf}_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

function pct(v) {
  if (v == null) return '—'
  return (parseFloat(v) >= 0 ? '+' : '') + parseFloat(v).toFixed(2) + '%'
}

function fmtPct(v) {
  if (v == null) return '—'
  return parseFloat(v).toFixed(1) + '%'
}

function TierBadge({ tier }) {
  const c = TIER_COLORS[tier] || TIER_COLORS.NO_DATA
  return (
    <span style={{
      background: c.bg, color: c.text, border: `1px solid ${c.border}`,
      borderRadius: 4, padding: '2px 7px', fontSize: 11, fontWeight: 700,
      letterSpacing: '0.04em', whiteSpace: 'nowrap',
    }}>
      {tier}
    </span>
  )
}

function ActiveBadge({ active }) {
  if (!active) return null
  return (
    <span style={{
      background: '#14532d', color: '#86efac', border: '1px solid #16a34a',
      borderRadius: 4, padding: '2px 6px', fontSize: 10, fontWeight: 700,
      marginLeft: 6,
    }}>LIVE</span>
  )
}

function CompletionBar({ value }) {
  const pct = Math.round((value || 0) * 100)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{
        width: 60, height: 6, background: '#374151', borderRadius: 3, overflow: 'hidden',
      }}>
        <div style={{
          width: `${pct}%`, height: '100%',
          background: pct === 100 ? '#22c55e' : '#6366f1',
          borderRadius: 3,
        }} />
      </div>
      <span style={{ fontSize: 11, color: '#9ca3af' }}>{pct}%</span>
    </div>
  )
}

export default function RareReversalPanel() {
  const [universe, setUniverse]  = useState('sp500')
  const [tf, setTf]              = useState('1d')
  const [minPrice, setMinPrice]  = useState('')
  const [maxPrice, setMaxPrice]  = useState('')
  const [limit, setLimit]        = useState(200)
  const [loading, setLoading]    = useState(false)
  const [results, setResults]    = useState(null)
  const [error, setError]        = useState(null)
  const [tierFilter, setTierFilter] = useState('all')
  const [activeOnly, setActiveOnly] = useState(false)
  const [expanded, setExpanded]  = useState({})

  async function handleScan() {
    setLoading(true)
    setError(null)
    setResults(null)
    setExpanded({})
    try {
      const params = new URLSearchParams({ universe, tf, limit })
      if (minPrice) params.set('min_price', minPrice)
      if (maxPrice) params.set('max_price', maxPrice)
      const resp = await fetch(`${BASE}/api/rare-reversal/scan?${params}`)
      if (!resp.ok) {
        const t = await resp.text()
        throw new Error(`HTTP ${resp.status}: ${t}`)
      }
      const data = await resp.json()
      if (data.error) throw new Error(data.error)
      setResults(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const filtered = results ? results.results.filter(r => {
    if (tierFilter !== 'all' && r.evidence_tier !== tierFilter) return false
    if (activeOnly && !r.is_currently_active) return false
    return true
  }) : []

  const toggleExpand = key => setExpanded(prev => ({ ...prev, [key]: !prev[key] }))

  return (
    <div style={{ fontFamily: 'monospace', padding: 24, maxWidth: 1300, margin: '0 auto' }}>
      <h2 style={{ color: '#f9fafb', marginBottom: 4, fontSize: 20 }}>Rare Reversal Miner</h2>
      <p style={{ color: '#9ca3af', fontSize: 13, marginBottom: 20 }}>
        Extends known 4-bar SEQ4 analytics left by 1–2 bars. Surfaces bottom-reversal
        patterns anchored to the master matrix.
      </p>

      {/* Controls */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 20, alignItems: 'flex-end' }}>
        <label style={{ color: '#d1d5db', fontSize: 13 }}>
          Universe
          <select
            value={universe}
            onChange={e => setUniverse(e.target.value)}
            style={{ display: 'block', marginTop: 4, background: '#1f2937', color: '#f9fafb', border: '1px solid #374151', borderRadius: 6, padding: '6px 10px', fontSize: 13 }}
          >
            {UNIVERSES.map(u => <option key={u.value} value={u.value}>{u.label}</option>)}
          </select>
        </label>

        <label style={{ color: '#d1d5db', fontSize: 13 }}>
          Timeframe
          <select
            value={tf}
            onChange={e => setTf(e.target.value)}
            style={{ display: 'block', marginTop: 4, background: '#1f2937', color: '#f9fafb', border: '1px solid #374151', borderRadius: 6, padding: '6px 10px', fontSize: 13 }}
          >
            {TFS.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>

        <label style={{ color: '#d1d5db', fontSize: 13 }}>
          Min Price
          <input
            type="number" value={minPrice} onChange={e => setMinPrice(e.target.value)}
            placeholder="0"
            style={{ display: 'block', marginTop: 4, width: 80, background: '#1f2937', color: '#f9fafb', border: '1px solid #374151', borderRadius: 6, padding: '6px 8px', fontSize: 13 }}
          />
        </label>

        <label style={{ color: '#d1d5db', fontSize: 13 }}>
          Max Price
          <input
            type="number" value={maxPrice} onChange={e => setMaxPrice(e.target.value)}
            placeholder="—"
            style={{ display: 'block', marginTop: 4, width: 80, background: '#1f2937', color: '#f9fafb', border: '1px solid #374151', borderRadius: 6, padding: '6px 8px', fontSize: 13 }}
          />
        </label>

        <label style={{ color: '#d1d5db', fontSize: 13 }}>
          Limit
          <input
            type="number" value={limit} onChange={e => setLimit(Number(e.target.value))}
            style={{ display: 'block', marginTop: 4, width: 70, background: '#1f2937', color: '#f9fafb', border: '1px solid #374151', borderRadius: 6, padding: '6px 8px', fontSize: 13 }}
          />
        </label>

        <button
          onClick={handleScan}
          disabled={loading}
          style={{
            padding: '8px 22px', background: loading ? '#374151' : '#4f46e5',
            color: '#fff', border: 'none', borderRadius: 6, cursor: loading ? 'default' : 'pointer',
            fontWeight: 700, fontSize: 14, alignSelf: 'flex-end',
          }}
        >
          {loading ? 'Scanning…' : 'Scan'}
        </button>
      </div>

      {error && (
        <div style={{ background: '#450a0a', color: '#fca5a5', border: '1px solid #7f1d1d', borderRadius: 8, padding: '10px 16px', marginBottom: 16, fontSize: 13 }}>
          {error}
        </div>
      )}

      {results && (
        <>
          {/* Summary bar */}
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 16, alignItems: 'center' }}>
            <span style={{ color: '#9ca3af', fontSize: 13 }}>
              <strong style={{ color: '#f9fafb' }}>{results.total}</strong> patterns found
            </span>
            {['all', 'CONFIRMED_RARE', 'ANECDOTAL_RARE', 'FORMING_PATTERN'].map(t => {
              const cnt = t === 'all'
                ? results.results.length
                : results.results.filter(r => r.evidence_tier === t).length
              const c = TIER_COLORS[t] || { text: '#9ca3af', border: '#374151' }
              return (
                <button
                  key={t}
                  onClick={() => setTierFilter(t)}
                  style={{
                    background: tierFilter === t ? '#1f2937' : 'transparent',
                    color: t === 'all' ? '#d1d5db' : c.text,
                    border: `1px solid ${tierFilter === t ? (t === 'all' ? '#6b7280' : c.border) : '#374151'}`,
                    borderRadius: 6, padding: '4px 10px', cursor: 'pointer', fontSize: 12,
                    fontWeight: tierFilter === t ? 700 : 400,
                  }}
                >
                  {t === 'all' ? 'All' : t.replace(/_/g, ' ')} ({cnt})
                </button>
              )
            })}
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#d1d5db', fontSize: 12, cursor: 'pointer' }}>
              <input type="checkbox" checked={activeOnly} onChange={e => setActiveOnly(e.target.checked)} />
              Live only
            </label>
            <button
              onClick={() => exportCSV(filtered, universe, tf)}
              style={{ marginLeft: 'auto', background: '#1f2937', color: '#9ca3af', border: '1px solid #374151', borderRadius: 6, padding: '4px 12px', cursor: 'pointer', fontSize: 12 }}
            >
              Export CSV
            </button>
          </div>

          {/* Table */}
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ background: '#111827', color: '#6b7280', textAlign: 'left' }}>
                  {['#', 'Ticker', 'Evidence Tier', 'Sequence Keys', 'Bars', 'Occurrences', 'Seq Low', '20-bar Low', 'Ret from Low', 'Med 10d', 'Win', 'Fail', 'Score', 'Active', 'Completion'].map(h => (
                    <th key={h} style={{ padding: '8px 10px', borderBottom: '1px solid #1f2937', whiteSpace: 'nowrap' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((r, idx) => {
                  const key = `${r.ticker}-${r.base4_key}-${idx}`
                  const isExp = expanded[key]
                  return [
                    <tr
                      key={key}
                      onClick={() => toggleExpand(key)}
                      style={{
                        background: isExp ? '#1a2332' : (idx % 2 === 0 ? '#111827' : '#0f1724'),
                        cursor: 'pointer',
                        borderBottom: '1px solid #1f2937',
                      }}
                    >
                      <td style={{ padding: '7px 10px', color: '#6b7280' }}>{r.rank}</td>
                      <td style={{ padding: '7px 10px', color: '#f9fafb', fontWeight: 700 }}>
                        {r.ticker}
                        <ActiveBadge active={r.is_currently_active} />
                      </td>
                      <td style={{ padding: '7px 10px' }}><TierBadge tier={r.evidence_tier} /></td>
                      <td style={{ padding: '7px 10px', color: '#e2e8f0', maxWidth: 240 }}>
                        <div style={{ fontSize: 11, color: '#93c5fd', fontFamily: 'monospace' }}>
                          {r.extended6_key || r.extended5_key || r.base4_key}
                        </div>
                        {r.extended6_key && (
                          <div style={{ fontSize: 10, color: '#6b7280', marginTop: 2 }}>
                            base: {r.base4_key}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: '7px 10px', color: '#d1d5db', textAlign: 'center' }}>{r.pattern_length}</td>
                      <td style={{ padding: '7px 10px', color: '#d1d5db', textAlign: 'center' }}>{r.pattern_count}</td>
                      <td style={{ padding: '7px 10px', color: '#d1d5db', textAlign: 'center' }}>
                        {r.sequence_low_bar_offset != null ? `${r.sequence_low_bar_offset}b ago` : '—'}
                      </td>
                      <td style={{ padding: '7px 10px', textAlign: 'center' }}>
                        {r.sequence_contains_20bar_low
                          ? <span style={{ color: '#f59e0b', fontWeight: 700 }}>YES</span>
                          : <span style={{ color: '#374151' }}>—</span>}
                      </td>
                      <td style={{ padding: '7px 10px', color: r.return_from_sequence_low_to_final > 0 ? '#4ade80' : '#f87171', textAlign: 'right' }}>
                        {pct(r.return_from_sequence_low_to_final)}
                      </td>
                      <td style={{ padding: '7px 10px', color: r.median_10d_return > 0 ? '#4ade80' : '#f87171', textAlign: 'right' }}>
                        {pct(r.median_10d_return)}
                      </td>
                      <td style={{ padding: '7px 10px', color: '#4ade80', textAlign: 'right' }}>{fmtPct(r.win_rate_10d)}</td>
                      <td style={{ padding: '7px 10px', color: '#f87171', textAlign: 'right' }}>{fmtPct(r.fail_rate_10d)}</td>
                      <td style={{ padding: '7px 10px', color: '#e2e8f0', textAlign: 'right', fontWeight: 600 }}>
                        {r.score != null ? r.score.toFixed(1) : '—'}
                      </td>
                      <td style={{ padding: '7px 10px', textAlign: 'center' }}>
                        {r.is_currently_active
                          ? <span style={{ color: '#22c55e', fontWeight: 700 }}>YES</span>
                          : <span style={{ color: '#374151' }}>—</span>}
                      </td>
                      <td style={{ padding: '7px 10px', minWidth: 100 }}>
                        <CompletionBar value={r.current_pattern_completion} />
                      </td>
                    </tr>,
                    isExp && (
                      <tr key={`${key}-detail`} style={{ background: '#0d1929' }}>
                        <td colSpan={15} style={{ padding: '12px 20px' }}>
                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 12 }}>
                            <Detail label="Base4 Key" value={r.base4_key} mono />
                            <Detail label="Ext5 Key"  value={r.extended5_key || '—'} mono />
                            <Detail label="Ext6 Key"  value={r.extended6_key || '—'} mono />
                            <Detail label="Base4 Status" value={r.base4_tier} />
                            <Detail label="Forming Subtype" value={r.forming_subtype || '—'} />
                            <Detail label="Last Seen" value={r.last_seen_date || '—'} />
                            <Detail label="Example Dates" value={Array.isArray(r.example_dates) ? r.example_dates.join(', ') : '—'} />
                          </div>
                        </td>
                      </tr>
                    ),
                  ]
                })}
              </tbody>
            </table>
          </div>

          {filtered.length === 0 && (
            <div style={{ textAlign: 'center', color: '#6b7280', padding: '40px 0', fontSize: 14 }}>
              No patterns match the current filters.
            </div>
          )}
        </>
      )}

      {!results && !loading && !error && (
        <div style={{ textAlign: 'center', color: '#6b7280', padding: '60px 0', fontSize: 14 }}>
          Select universe and timeframe, then click Scan.
        </div>
      )}
    </div>
  )
}

function Detail({ label, value, mono }) {
  return (
    <div>
      <div style={{ color: '#6b7280', fontSize: 11, marginBottom: 2 }}>{label}</div>
      <div style={{ color: '#e2e8f0', fontSize: 12, fontFamily: mono ? 'monospace' : 'inherit', wordBreak: 'break-all' }}>
        {value}
      </div>
    </div>
  )
}

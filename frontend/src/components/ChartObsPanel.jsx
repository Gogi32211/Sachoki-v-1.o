import { useState, useEffect, useCallback } from 'react'

const BASE = import.meta.env.VITE_API_URL || ''

const K_OPTIONS = [
  'K1','K1G','K2','K2G','K3','K4','K5','K6','K9','K10','K11','NONE',
]
const QUALITY_OPTIONS = ['PERFECT','GOOD','OK','BAD']

export default function ChartObsPanel({ onSelectTicker }) {
  const [view, setView] = useState('new')  // 'new' | 'stats' | 'recent'

  const [ticker, setTicker]   = useState('')
  const [obsDate, setObsDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [prefilled, setPrefilled] = useState(null)
  const [status, setStatus]   = useState('')
  const [loading, setLoading] = useState(false)

  const [kSig, setKSig]         = useState('')
  const [quality, setQuality]   = useState('')
  const [lvbo, setLvbo]         = useState(false)
  const [eb, setEb]             = useState(false)
  const [kFired, setKFired]     = useState(false)
  const [notes, setNotes]       = useState('')

  // Stats / Recent state
  const [statsRows,  setStatsRows]  = useState([])
  const [statsDays,  setStatsDays]  = useState(180)
  const [recentRows, setRecentRows] = useState([])
  const [statsErr,   setStatsErr]   = useState('')

  const flash = (msg, ms = 2500) => {
    setStatus(msg)
    setTimeout(() => setStatus(''), ms)
  }

  const loadSignals = async () => {
    const t = ticker.trim().toUpperCase()
    if (!t || !obsDate) return
    onSelectTicker?.(t)
    setLoading(true)
    setStatus('⏳ loading...')
    try {
      const r = await fetch(`${BASE}/obs/prefill?ticker=${t}&obs_date=${obsDate}`)
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
      const data = await r.json()
      setPrefilled(data)
      flash('✓ loaded')
    } catch (e) {
      setPrefilled(null)
      flash('❌ ' + e.message, 4000)
    } finally {
      setLoading(false)
    }
  }

  const saveObs = async () => {
    if (!prefilled) return
    const payload = {
      ...prefilled,
      k_signal_match: kSig || null,
      entry_quality:  quality || null,
      notes:          notes || null,
      lvbo_present:   lvbo,
      eb_reversal:    eb,
      k_fired:        kFired,
    }
    try {
      const r = await fetch(`${BASE}/obs/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
      const j = await r.json()
      flash('✅ saved ' + j.ticker, 3000)
    } catch (e) {
      flash('❌ ' + e.message, 4000)
    }
  }

  const resetForm = () => {
    setTicker('')
    setPrefilled(null)
    setKSig('')
    setQuality('')
    setLvbo(false)
    setEb(false)
    setKFired(false)
    setNotes('')
    setStatus('')
  }

  const onKey = (e) => { if (e.key === 'Enter') loadSignals() }

  const delta = prefilled
    ? (Number(prefilled.score_at || 0) - Number(prefilled.score_before || 0))
    : 0

  const loadStats = useCallback(async () => {
    setStatsErr('')
    try {
      const r = await fetch(`${BASE}/obs/stats?days=${statsDays}`)
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
      const j = await r.json()
      setStatsRows(Array.isArray(j.stats) ? j.stats : [])
    } catch (e) {
      setStatsErr(String(e.message || e))
      setStatsRows([])
    }
  }, [statsDays])

  const loadRecent = useCallback(async () => {
    setStatsErr('')
    try {
      const r = await fetch(`${BASE}/obs/recent?limit=100`)
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
      const j = await r.json()
      setRecentRows(Array.isArray(j.observations) ? j.observations : [])
    } catch (e) {
      setStatsErr(String(e.message || e))
      setRecentRows([])
    }
  }, [])

  const syncResults = async () => {
    try {
      const r = await fetch(`${BASE}/obs/sync-results`, { method: 'POST' })
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
      flash('✓ results synced from portfolio')
      loadStats()
      loadRecent()
    } catch (e) {
      flash('❌ ' + e.message, 4000)
    }
  }

  useEffect(() => {
    if (view === 'stats')  loadStats()
    if (view === 'recent') loadRecent()
  }, [view, loadStats, loadRecent])

  return (
    <div className="bg-md-surface-con rounded-md-md p-4 font-mono text-xs text-md-on-surface max-w-4xl">
      <div className="flex items-center justify-between mb-3">
        <div className="text-sm font-bold text-green-400">
          📊 Chart Observation
        </div>
        <div className="flex gap-1">
          {[
            ['new',    '✎ New'],
            ['stats',  '📈 Stats'],
            ['recent', '📋 Recent'],
          ].map(([id, lbl]) => (
            <button
              key={id}
              onClick={() => setView(id)}
              className={`text-[11px] px-2 py-1 rounded transition-colors
                ${view === id
                  ? 'bg-blue-700 text-white font-semibold'
                  : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}
            >
              {lbl}
            </button>
          ))}
        </div>
      </div>

      {view === 'stats'  && renderStats({ statsRows, statsDays, setStatsDays, loadStats, syncResults, statsErr })}
      {view === 'recent' && renderRecent({ recentRows, statsErr, syncResults })}
      {view === 'new' && (<>
      <div className="max-w-md">

      {/* STEP 1: ticker + date */}
      <div className="grid grid-cols-[1fr_1fr_auto] gap-2 mb-3">
        <input
          value={ticker}
          onChange={e => setTicker(e.target.value.toUpperCase())}
          onKeyDown={onKey}
          placeholder="TICKER"
          className="bg-md-surface-high border border-md-outline-var rounded px-2 py-1.5 text-sm text-md-on-surface focus:border-blue-500 outline-none"
        />
        <input
          type="date"
          value={obsDate}
          onChange={e => setObsDate(e.target.value)}
          onKeyDown={onKey}
          className="bg-md-surface-high border border-md-outline-var rounded px-2 py-1.5 text-sm text-md-on-surface focus:border-blue-500 outline-none"
        />
        <button
          onClick={loadSignals}
          disabled={loading}
          className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white font-bold px-3 py-1.5 rounded text-sm"
        >
          LOAD →
        </button>
      </div>

      {/* STEP 2: auto-filled display */}
      {prefilled && (
        <>
          <div className="bg-md-surface rounded p-2.5 mb-2.5 text-[11px] leading-relaxed">
            <div className="grid grid-cols-3 gap-1">
              <span>T: <b className="text-green-400">{prefilled.t_signal || '—'}</b></span>
              <span>Z-1: <b className="text-red-400">{prefilled.z_prev_1 || '—'}</b></span>
              <span>Z-2: <b className="text-red-400">{prefilled.z_prev_2 || '—'}</b></span>
              <span>L: <b className="text-yellow-300">{prefilled.l_signal || '—'}</b></span>
              <span>F: <b className="text-sky-400">{prefilled.f_signal || '—'}</b></span>
              <span>GOG: <b className="text-purple-300">{prefilled.gog_signal || '—'}</b></span>
              <span>Score: <b className="text-white">{prefilled.score_before ?? '?'}→{prefilled.score_at ?? '?'}</b></span>
              <span>Δ: <b className="text-green-300">{delta > 0 ? '+' : ''}{delta}</b></span>
              <span>RTB: <b className="text-white">{(prefilled.rtb_total ?? '')}/{(prefilled.rtb_phase ?? '')}</b></span>
              <span>Beta: <b className="text-white">{prefilled.beta_score ?? '—'}</b></span>
              <span>Zone: <b className="text-sky-400">{prefilled.beta_zone || '—'}</b></span>
              <span>Sweet: <b className="text-yellow-300">{prefilled.sweet_spot ? '★' : '—'}</b></span>
            </div>
            <div className="mt-1">Seq: <b className="text-md-on-surface">{prefilled.sequence_label || '—'}</b></div>
            {prefilled.signal_reasons && (
              <div className="mt-0.5 text-[10px] text-md-on-surface-var">{prefilled.signal_reasons}</div>
            )}
          </div>

          {/* STEP 3: user inputs */}
          <div className="grid grid-cols-3 gap-2 mb-2">
            <select
              value={kSig}
              onChange={e => setKSig(e.target.value)}
              className="bg-md-surface-high border border-green-700 rounded px-1.5 py-1.5 text-xs text-md-on-surface"
            >
              <option value="">K match?</option>
              {K_OPTIONS.map(k => <option key={k} value={k}>{k}</option>)}
            </select>
            <select
              value={quality}
              onChange={e => setQuality(e.target.value)}
              className="bg-md-surface-high border border-green-700 rounded px-1.5 py-1.5 text-xs text-md-on-surface"
            >
              <option value="">Quality?</option>
              {QUALITY_OPTIONS.map(q => <option key={q} value={q}>{q}</option>)}
            </select>
            <div className="flex flex-wrap gap-1.5 items-center text-[11px]">
              <label className="flex items-center gap-1 cursor-pointer">
                <input type="checkbox" checked={lvbo} onChange={e => setLvbo(e.target.checked)} />
                LVBO
              </label>
              <label className="flex items-center gap-1 cursor-pointer">
                <input type="checkbox" checked={eb} onChange={e => setEb(e.target.checked)} />
                EB↓↑
              </label>
              <label className="flex items-center gap-1 cursor-pointer">
                <input type="checkbox" checked={kFired} onChange={e => setKFired(e.target.checked)} />
                K✓
              </label>
            </div>
          </div>

          <input
            value={notes}
            onChange={e => setNotes(e.target.value)}
            placeholder="notes (optional)..."
            className="w-full bg-md-surface-high border border-md-outline-var rounded px-2 py-1.5 text-xs text-md-on-surface mb-2 focus:border-blue-500 outline-none"
          />

          <div className="flex gap-2 items-center">
            <button
              onClick={saveObs}
              className="bg-green-500 hover:bg-green-400 text-black font-bold px-5 py-1.5 rounded text-sm"
            >
              SAVE
            </button>
            <button
              onClick={resetForm}
              className="bg-gray-700 hover:bg-gray-600 text-md-on-surface px-3 py-1.5 rounded text-xs"
            >
              Reset
            </button>
            <span className="text-green-400 text-xs">{status}</span>
          </div>
        </>
      )}

      {!prefilled && status && (
        <div className="text-xs text-md-on-surface-var mt-1">{status}</div>
      )}
      </div>
      </>)}
    </div>
  )
}

// ── Stats view ──────────────────────────────────────────────────────────────
function renderStats({ statsRows, statsDays, setStatsDays, loadStats, syncResults, statsErr }) {
  const fmt = (v, d = 1) => v == null ? '—' : Number(v).toFixed(d)
  const winColor = (w) => w == null ? 'text-md-on-surface-var'
    : w >= 60 ? 'text-green-400'
    : w >= 45 ? 'text-yellow-300'
    : 'text-red-400'

  return (
    <div>
      <div className="flex items-center gap-2 mb-3 text-[11px]">
        <span className="text-md-on-surface-var">Days:</span>
        {[30, 90, 180, 365].map(d => (
          <button
            key={d}
            onClick={() => setStatsDays(d)}
            className={`px-2 py-1 rounded ${statsDays === d
              ? 'bg-blue-700 text-white'
              : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}
          >{d}d</button>
        ))}
        <button onClick={loadStats}
          className="px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 text-md-on-surface ml-2">
          ↻ refresh
        </button>
        <button onClick={syncResults}
          className="px-2 py-1 rounded bg-purple-700 hover:bg-purple-600 text-white ml-auto"
          title="Pull result_5d/10d from Paper Portfolio closed trades">
          🔁 Sync results
        </button>
      </div>

      {statsErr && <div className="text-red-400 text-[11px] mb-2">❌ {statsErr}</div>}

      {statsRows.length === 0 ? (
        <div className="text-md-on-surface-var text-[11px]">No observations yet. Save some from the New tab.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead className="text-md-on-surface-var border-b border-md-outline-var">
              <tr>
                <th className="text-left  px-2 py-1">T</th>
                <th className="text-left  px-2 py-1">Sequence</th>
                <th className="text-left  px-2 py-1">K-match</th>
                <th className="text-right px-2 py-1">N</th>
                <th className="text-right px-2 py-1">Win%</th>
                <th className="text-right px-2 py-1">Avg 10d</th>
                <th className="text-right px-2 py-1">Δ score</th>
              </tr>
            </thead>
            <tbody>
              {statsRows.map((r, i) => (
                <tr key={i} className="border-b border-md-outline-var hover:bg-md-surface-high/40">
                  <td className="px-2 py-1 text-green-400 font-bold">{r.t_signal || '—'}</td>
                  <td className="px-2 py-1 text-md-on-surface">{r.sequence_label || '—'}</td>
                  <td className="px-2 py-1 text-yellow-300">{r.k_signal_match || '—'}</td>
                  <td className="px-2 py-1 text-right text-md-on-surface">{r.n}</td>
                  <td className={`px-2 py-1 text-right font-bold ${winColor(r.win_rate)}`}>
                    {r.win_rate == null ? '—' : `${fmt(r.win_rate, 0)}%`}
                  </td>
                  <td className={`px-2 py-1 text-right ${Number(r.avg10d) > 0 ? 'text-green-300' : 'text-red-300'}`}>
                    {r.avg10d == null ? '—' : `${Number(r.avg10d) > 0 ? '+' : ''}${fmt(r.avg10d, 2)}%`}
                  </td>
                  <td className="px-2 py-1 text-right text-md-on-surface">
                    {r.avg_score_jump == null ? '—' : `${Number(r.avg_score_jump) > 0 ? '+' : ''}${fmt(r.avg_score_jump, 1)}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Recent view ─────────────────────────────────────────────────────────────
function renderRecent({ recentRows, statsErr, syncResults }) {
  const fmt = (v, d = 1) => v == null ? '—' : Number(v).toFixed(d)
  const outcomeColor = (o) =>
    o === 'WIN'  ? 'text-green-400'
    : o === 'LOSS' ? 'text-red-400'
    : o === 'NEUTRAL' ? 'text-md-on-surface-var'
    : 'text-md-on-surface-var/70'

  return (
    <div>
      <div className="flex justify-end mb-2">
        <button onClick={syncResults}
          className="px-2 py-1 rounded bg-purple-700 hover:bg-purple-600 text-white text-[11px]">
          🔁 Sync results
        </button>
      </div>

      {statsErr && <div className="text-red-400 text-[11px] mb-2">❌ {statsErr}</div>}

      {recentRows.length === 0 ? (
        <div className="text-md-on-surface-var text-[11px]">No observations saved yet.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead className="text-md-on-surface-var border-b border-md-outline-var">
              <tr>
                <th className="text-left  px-2 py-1">Date</th>
                <th className="text-left  px-2 py-1">Ticker</th>
                <th className="text-left  px-2 py-1">T</th>
                <th className="text-left  px-2 py-1">Sequence</th>
                <th className="text-left  px-2 py-1">K</th>
                <th className="text-left  px-2 py-1">Quality</th>
                <th className="text-right px-2 py-1">Δ</th>
                <th className="text-right px-2 py-1">10d</th>
                <th className="text-left  px-2 py-1">Result</th>
                <th className="text-left  px-2 py-1">Notes</th>
              </tr>
            </thead>
            <tbody>
              {recentRows.map((r) => (
                <tr key={r.id} className="border-b border-md-outline-var hover:bg-md-surface-high/40">
                  <td className="px-2 py-1 text-md-on-surface-var">{String(r.obs_date || '').slice(0, 10)}</td>
                  <td className="px-2 py-1 text-blue-300 font-bold">{r.ticker}</td>
                  <td className="px-2 py-1 text-green-400">{r.t_signal || '—'}</td>
                  <td className="px-2 py-1 text-md-on-surface">{r.sequence_label || '—'}</td>
                  <td className="px-2 py-1 text-yellow-300">{r.k_signal_match || '—'}</td>
                  <td className="px-2 py-1 text-md-on-surface">{r.entry_quality || '—'}</td>
                  <td className="px-2 py-1 text-right text-md-on-surface">
                    {r.score_delta == null ? '—' : (Number(r.score_delta) > 0 ? `+${r.score_delta}` : r.score_delta)}
                  </td>
                  <td className={`px-2 py-1 text-right ${Number(r.result_10d) > 0 ? 'text-green-300' : 'text-red-300'}`}>
                    {r.result_10d == null ? '—' : `${Number(r.result_10d) > 0 ? '+' : ''}${fmt(r.result_10d, 2)}%`}
                  </td>
                  <td className={`px-2 py-1 ${outcomeColor(r.result_outcome)}`}>{r.result_outcome || '—'}</td>
                  <td className="px-2 py-1 text-md-on-surface-var truncate max-w-[180px]" title={r.notes || ''}>
                    {r.notes || ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

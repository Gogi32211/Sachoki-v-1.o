import { useState, useMemo } from 'react'

const LS_KEY = 'sachoki_personal_watchlist'

export function pwlLoad() {
  try { return JSON.parse(localStorage.getItem(LS_KEY) || '[]') } catch { return [] }
}
export function pwlSave(items) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(items)) } catch {}
}
export function pwlAdd(row) {
  const items = pwlLoad()
  if (items.find(x => x.ticker === row.ticker && x.tf === row.tf)) return // already saved
  const entry = {
    ticker:     row.ticker,
    addedAt:    new Date().toISOString(),
    tf:         row._tf || '1d',
    score:      row.turbo_score ?? 0,
    price:      row.last_price  ?? null,
    change_pct: row.change_pct  ?? null,
    tz_sig:     row.tz_sig      ?? '',
    tz_bull:    row.tz_bull     ?? 0,
    sig_ages:   row.sig_ages    ?? '{}',
    // snapshot all boolean signals for N-bar filtering
    _signals: Object.fromEntries(
      Object.entries(row).filter(([k, v]) =>
        typeof v === 'number' && v === 1 &&
        !['scan_id','turbo_score','turbo_score_n3','turbo_score_n5','turbo_score_n10',
          'last_price','change_pct','rsi','cci','avg_vol'].includes(k)
      )
    ),
  }
  pwlSave([entry, ...items])
}
export function pwlRemove(ticker, tf) {
  pwlSave(pwlLoad().filter(x => !(x.ticker === ticker && x.tf === tf)))
}
export function pwlHas(ticker, tf) {
  return pwlLoad().some(x => x.ticker === ticker && x.tf === tf)
}

// ── Signal label map (same as SignalCorrelPanel) ───────────────────────────────
const SIG_LABEL = {
  vol_spike_20x:'V×20', vol_spike_10x:'V×10', vol_spike_5x:'V×5',
  conso_2809:'CON', um_2809:'UM', svs_2809:'SVS', buy_2809:'BUY', rocket:'🚀',
  sig3g:'3G', rtv:'RTV', hilo_buy:'HILO↑', atr_brk:'ATR↑', bb_brk:'BB↑',
  bias_up:'↑BIAS', va:'VA', seq_bcont:'SBC', ca:'CA', cd:'CD', cw:'CW',
  best_sig:'BEST★', strong_sig:'STR', abs_sig:'ABS', climb_sig:'CLB',
  load_sig:'LD', vbo_up:'VBO↑', ns:'NS', sq:'SQ', sc:'SC',
  sig_l88:'L88', sig_260308:'260308',
  tz_bull_flip:'FLP↑', tz_attempt:'W',
  b1:'B1',b2:'B2',b3:'B3',b4:'B4',b5:'B5',b6:'B6',b7:'B7',b8:'B8',b9:'B9',b10:'B10',b11:'B11',
  g1:'G1',g2:'G2',g4:'G4',g6:'G6',g11:'G11',
  fri34:'FRI34', fri43:'FRI43', l34:'L34', l43:'L43', l64:'L64', l22:'L22',
  blue:'BL', cci_ready:'CCI', cci_0_retest:'CCIOR',
  bo_up:'BO↑', bx_up:'BX↑', be_up:'BE↑', fuchsia_rh:'RH', pre_pump:'PP',
  wick_bull:'WK↑', x2g_wick:'X2G', x2_wick:'X2', x1g_wick:'X1G', x1_wick:'X1', x3_wick:'X3',
  eb_bull:'EB↑', fbo_bull:'FBO↑', bf_buy:'4BF', ultra_3up:'3↑',
  rs:'RS', rs_strong:'RS+',
  preup66:'P66', preup55:'P55', preup89:'P89',
  d_strong_bull:'B/S↑', d_absorb_bull:'Ab↑', d_div_bull:'Δ↑',
  d_cd_bull:'cd↑', d_surge_bull:'Δ↑S', d_blast_bull:'ΔΔ↑',
  d_vd_div_bull:'ΔΔ↑V', d_spring:'dSPR',
}

function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.getMonth()+1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2,'0')}`
}

function SignalChips({ entry, n }) {
  const ages = useMemo(() => {
    try { return JSON.parse(entry.sig_ages || '{}') } catch { return {} }
  }, [entry.sig_ages])

  const active = useMemo(() => {
    const sigs = entry._signals || {}
    return Object.keys(sigs).filter(k => {
      if (n <= 1) return sigs[k] === 1 && (ages[k] ?? 999) < 1  // current bar only
      return sigs[k] === 1 && (ages[k] ?? 0) < n
    })
  }, [entry._signals, ages, n])

  if (!active.length) return <span className="text-md-on-surface-var/60 text-[10px]">—</span>
  return (
    <div className="flex flex-wrap gap-0.5">
      {active.map(k => (
        <span key={k} className="px-1 py-0.5 rounded bg-gray-700 text-md-on-surface text-[10px] font-mono">
          {SIG_LABEL[k] ?? k}
        </span>
      ))}
    </div>
  )
}

const ALL_UNI = ['sp500', 'nasdaq', 'russell2k', 'all_us']
const ALL_TF  = ['1d', '4h', '1h', '30m', '15m', '1wk']
const SKIP_COLS = new Set(['scan_id','turbo_score','turbo_score_n3','turbo_score_n5',
  'turbo_score_n10','last_price','change_pct','rsi','cci','avg_vol'])

function _toEntry(r, tf) {
  return {
    ticker:     r.ticker,
    tf,
    score:      r.turbo_score ?? 0,
    price:      r.last_price  ?? null,
    change_pct: r.change_pct  ?? null,
    tz_sig:     r.tz_sig      ?? '',
    sig_ages:   r.sig_ages    ?? '{}',
    _signals:   Object.fromEntries(
      Object.entries(r).filter(([k, v]) => typeof v === 'number' && v === 1 && !SKIP_COLS.has(k))
    ),
  }
}

// Search ALL cached universe+tf combos for the requested tickers
function useWatchingRows(tickers) {
  return useMemo(() => {
    if (!tickers?.length) return []
    const found = {}
    // prefer current tf/uni first so freshest scan wins
    const curTf  = (() => { try { return localStorage.getItem('sachoki_turbo_tf')  || '1d'    } catch { return '1d'    } })()
    const curUni = (() => { try { return localStorage.getItem('sachoki_turbo_uni') || 'sp500' } catch { return 'sp500' } })()
    const tfsOrd = [curTf, ...ALL_TF.filter(t => t !== curTf)]
    const uniOrd = [curUni, ...ALL_UNI.filter(u => u !== curUni)]
    for (const tf of tfsOrd) {
      for (const uni of uniOrd) {
        try {
          const raw = localStorage.getItem(`sachoki_turbo_${tf}_${uni}`)
          if (!raw) continue
          const rows = JSON.parse(raw)?.results || []
          for (const r of rows) {
            if (tickers.includes(r.ticker) && !found[r.ticker])
              found[r.ticker] = _toEntry(r, tf)
          }
        } catch {}
        if (Object.keys(found).length === tickers.length) break
      }
      if (Object.keys(found).length === tickers.length) break
    }
    return tickers.map(t => found[t]).filter(Boolean)
  }, [tickers])
}

export default function PersonalWatchlistPanel({ onSelectTicker, watchlistTickers, onAddTicker, onRemoveTicker }) {
  const [items,    setItems]    = useState(pwlLoad)
  const [n,        setN]        = useState(5)
  const [sortBy,   setSortBy]   = useState('addedAt')
  const [addInput, setAddInput] = useState('')
  const watchingRows = useWatchingRows(watchlistTickers)

  const refresh = () => setItems(pwlLoad())

  const handleAddTicker = (e) => {
    e.preventDefault()
    const t = addInput.trim().toUpperCase()
    if (t) { onAddTicker?.(t); setAddInput('') }
  }

  const remove = (ticker, tf) => {
    pwlRemove(ticker, tf)
    refresh()
  }

  const sorted = useMemo(() => [...items].sort((a, b) => {
    if (sortBy === 'score') return b.score - a.score
    if (sortBy === 'ticker') return a.ticker.localeCompare(b.ticker)
    return new Date(b.addedAt) - new Date(a.addedAt) // newest first
  }), [items, sortBy])

  return (
    <div className="bg-md-surface-con rounded-md-md border border-md-outline-var text-xs text-md-on-surface">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-b border-md-outline-var">
        <span className="font-semibold text-sm text-white">⭐ Personal Watchlist</span>
        <span className="text-md-on-surface-var">{items.length} saved tickers</span>

        {/* N-bar filter */}
        <div className="flex items-center gap-1 ml-auto text-md-on-surface-var">
          <span>N bars:</span>
          {[1, 3, 5, 10].map(v => (
            <button key={v} onClick={() => setN(v)}
              className={`px-2 py-0.5 rounded text-xs ${n === v ? 'bg-md-primary-container text-md-on-primary-container' : 'bg-md-surface-high text-md-on-surface-var hover:text-md-on-surface'}`}>
              {v}
            </button>
          ))}
        </div>

        {/* Sort */}
        <div className="flex items-center gap-1 text-md-on-surface-var">
          <span>Sort:</span>
          {[['addedAt','Date'],['score','Score'],['ticker','A-Z']].map(([k,l]) => (
            <button key={k} onClick={() => setSortBy(k)}
              className={`px-2 py-0.5 rounded text-xs ${sortBy === k ? 'bg-md-surface-high text-md-on-surface' : 'bg-md-surface-high text-md-on-surface-var hover:text-md-on-surface'}`}>
              {l}
            </button>
          ))}
        </div>

        <button onClick={refresh}
          className="px-2 py-0.5 rounded-md-sm bg-md-surface-high text-md-on-surface-var hover:text-md-on-surface text-xs">↺</button>
      </div>

      {/* Add ticker input */}
      {onAddTicker && (
        <form onSubmit={handleAddTicker} className="flex items-center gap-2 px-4 py-2 border-b border-md-outline-var bg-md-surface-high/20">
          <input
            value={addInput}
            onChange={e => setAddInput(e.target.value.toUpperCase())}
            placeholder="Add ticker… (e.g. AAPL)"
            className="flex-1 bg-md-surface-high border border-md-outline-var rounded-md-sm px-2 py-1 text-xs text-md-on-surface placeholder:text-md-on-surface-var/50 focus:outline-none focus:border-md-primary"
          />
          <button type="submit"
            className="px-3 py-1 rounded bg-blue-700 text-white text-xs hover:bg-blue-600">+ Add</button>
        </form>
      )}

      {/* Watching section — tickers from regular watchlist */}
      {(watchlistTickers?.length > 0) && (
        <div className="border-b border-md-outline-var">
          <div className="px-4 py-1.5 text-[10px] text-md-on-surface-var/60 uppercase tracking-wider bg-md-surface-high/30">
            Watching ({watchlistTickers.length}){watchingRows.length < watchlistTickers.length ? ` · ${watchlistTickers.length - watchingRows.length} not in cache — run Turbo scan first` : ''}
          </div>
          <table className="w-full">
            <tbody>
              {watchlistTickers.map((ticker, i) => {
                const entry = watchingRows.find(r => r.ticker === ticker)
                return (
                  <tr key={i}
                    onClick={() => onSelectTicker?.(ticker)}
                    className="border-b border-md-outline-var/30 hover:bg-md-surface-high/40 cursor-pointer">
                    <td className="px-3 py-1.5 font-semibold text-white w-20">{ticker}</td>
                    <td className="px-2 py-1.5 text-purple-300 font-mono text-[10px] w-12">{entry?.tz_sig || '—'}</td>
                    <td className="px-2 py-1.5 font-bold text-yellow-300 w-12">{entry ? entry.score.toFixed(1) : '—'}</td>
                    <td className="px-2 py-1.5 flex-1">{entry ? <SignalChips entry={entry} n={n} /> : <span className="text-md-on-surface-var/50 text-[10px]">no scan data</span>}</td>
                    <td className="px-2 py-1.5 text-md-on-surface-var w-20">
                      {entry?.price != null ? `$${Number(entry.price).toFixed(2)}` : ''}
                    </td>
                    <td className={`px-2 py-1.5 w-16 ${entry?.change_pct > 0 ? 'text-green-400' : entry?.change_pct < 0 ? 'text-red-400' : 'text-md-on-surface-var'}`}>
                      {entry?.change_pct != null ? `${entry.change_pct > 0 ? '+' : ''}${Number(entry.change_pct).toFixed(1)}%` : ''}
                    </td>
                    {onRemoveTicker && (
                      <td className="px-2 py-1.5 w-6" onClick={e => { e.stopPropagation(); onRemoveTicker(ticker) }}>
                        <button className="text-gray-700 hover:text-red-400 transition-colors" title="Remove">✕</button>
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {sorted.length === 0 && watchingRows.length === 0 && (
        <div className="px-4 py-10 text-center text-md-on-surface-var/70">
          No saved tickers — press ★ on any Turbo scan row to save
        </div>
      )}
      {sorted.length === 0 && watchingRows.length > 0 && (
        <div className="px-4 py-4 text-center text-md-on-surface-var/70 text-[11px]">
          No saved tickers yet — press ★ on any Turbo scan row to save
        </div>
      )}

      {sorted.length > 0 && (
        <div className="overflow-auto max-h-[420px]">
          <div className="px-4 py-1.5 text-[10px] text-md-on-surface-var/60 uppercase tracking-wider bg-md-surface-high/30 sticky top-0">
            Saved from scan ({sorted.length})
          </div>
          <table className="w-full">
            <thead className="sticky top-7 bg-md-surface-con border-b border-md-outline-var">
              <tr>
                <th className="px-3 py-2 text-left text-xs text-md-on-surface-var font-medium w-6"></th>
                <th className="px-3 py-2 text-left text-xs text-md-on-surface-var font-medium">Ticker</th>
                <th className="px-3 py-2 text-left text-xs text-md-on-surface-var font-medium">TF</th>
                <th className="px-3 py-2 text-left text-xs text-md-on-surface-var font-medium">Score</th>
                <th className="px-3 py-2 text-left text-xs text-md-on-surface-var font-medium">T/Z</th>
                <th className="px-3 py-2 text-left text-xs text-md-on-surface-var font-medium">
                  Signals (≤{n} bars)
                </th>
                <th className="px-3 py-2 text-left text-xs text-md-on-surface-var font-medium">Price</th>
                <th className="px-3 py-2 text-left text-xs text-md-on-surface-var font-medium">Chg%</th>
                <th className="px-3 py-2 text-left text-xs text-md-on-surface-var font-medium">Added</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((entry, i) => (
                <tr key={i}
                  onClick={() => onSelectTicker?.(entry.ticker)}
                  className="border-b border-md-outline-var/50 hover:bg-md-surface-high/40 cursor-pointer">
                  <td className="px-2 py-1.5" onClick={e => { e.stopPropagation(); remove(entry.ticker, entry.tf) }}>
                    <button className="text-yellow-400 hover:text-md-on-surface-var transition-colors" title="Remove">★</button>
                  </td>
                  <td className="px-3 py-1.5 font-semibold text-white">{entry.ticker}</td>
                  <td className="px-3 py-1.5 text-md-on-surface-var text-[10px]">{entry.tf}</td>
                  <td className="px-3 py-1.5 font-bold text-yellow-300">{entry.score.toFixed(1)}</td>
                  <td className="px-3 py-1.5 text-purple-300 font-mono text-[10px]">{entry.tz_sig || '—'}</td>
                  <td className="px-3 py-1.5 max-w-xs">
                    <SignalChips entry={entry} n={n} />
                  </td>
                  <td className="px-3 py-1.5 text-md-on-surface">
                    {entry.price != null ? `$${Number(entry.price).toFixed(2)}` : '—'}
                  </td>
                  <td className={`px-3 py-1.5 ${entry.change_pct > 0 ? 'text-green-400' : entry.change_pct < 0 ? 'text-red-400' : 'text-md-on-surface-var'}`}>
                    {entry.change_pct != null ? `${entry.change_pct > 0 ? '+' : ''}${Number(entry.change_pct).toFixed(2)}%` : '—'}
                  </td>
                  <td className="px-3 py-1.5 text-md-on-surface-var/60 text-[10px]">{fmtDate(entry.addedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="px-4 py-2 text-md-on-surface-var/50 text-[10px] border-t border-md-outline-var">
        N bars = show only signals that fired within last N bars · Click row to select ticker · ★ to remove
      </div>
    </div>
  )
}

import { useState, useEffect } from 'react'
import { api } from '../api'

const SIG_LABEL = {
  // Combo / 2809
  conso_2809: 'CON', um_2809: 'UM', svs_2809: 'SVS', buy_2809: 'BUY', rocket: '🚀',
  sig3g: '3G', rtv: 'RTV', hilo_buy: 'HILO↑', hilo_sell: 'HILO↓',
  atr_brk: 'ATR↑', bb_brk: 'BB↑', bias_up: '↑BIAS', bias_down: '↓BIAS',
  cons_atr: 'CATR', seq_bcont: 'SBC', va: 'VA',
  ca: 'CA', cd: 'CD', cw: 'CW',
  // VABS
  best_sig: 'BEST★', strong_sig: 'STRONG', abs_sig: 'ABS', climb_sig: 'CLB',
  load_sig: 'LD', vbo_up: 'VBO↑', vbo_dn: 'VBO↓',
  ns: 'NS', nd: 'ND', sq: 'SQ', sc: 'SC', bc: 'BC',
  // 260308 / L88
  sig_l88: 'L88', sig_260308: '260308',
  // TZ state
  tz_bull: 'T/Z↑', tz_bull_flip: 'FLP↑', tz_attempt: 'W',
  tz_weak_bull: 'W↑', tz_weak_bear: 'W↓',
  // B signals
  b1:'B1', b2:'B2', b3:'B3', b4:'B4', b5:'B5',
  b6:'B6', b7:'B7', b8:'B8', b9:'B9', b10:'B10', b11:'B11',
  // G signals
  g1:'G1', g2:'G2', g4:'G4', g6:'G6', g11:'G11',
  // WLNBB
  fri34:'FRI34', fri43:'FRI43', fri64:'FRI64',
  l34:'L34', l43:'L43', l64:'L64', l22:'L22', l555:'L555', only_l2l4:'L2L4',
  blue:'BL', cci_ready:'CCI', cci_0_retest:'CCIOR', cci_blue_turn:'CCIB',
  bo_up:'BO↑', bo_dn:'BO↓', bx_up:'BX↑', bx_dn:'BX↓',
  be_up:'BE↑', be_dn:'BE↓',
  fuchsia_rh:'RH', fuchsia_rl:'RL', pre_pump:'PP',
  // Wick
  wick_bull:'WK↑', wick_bear:'WK↓',
  x2g_wick:'X2G', x2_wick:'X2', x1g_wick:'X1G', x1_wick:'X1', x3_wick:'X3',
  // ULTRA v2
  best_long:'BEST↑', best_short:'BEST↓',
  eb_bull:'EB↑', eb_bear:'EB↓', fbo_bull:'FBO↑', fbo_bear:'FBO↓',
  bf_buy:'4BF', bf_sell:'4BF↓', ultra_3up:'3↑', ultra_3dn:'3↓',
  // RS
  rs:'RS', rs_strong:'RS+',
  // PREUP / PREDN
  preup66:'P66', preup55:'P55', preup89:'P89', preup3:'P3', preup2:'P2', preup50:'P50',
  predn66:'D66', predn55:'D55', predn89:'D89', predn3:'D3', predn2:'D2', predn50:'D50',
  // Delta
  d_strong_bull:'B/S↑', d_strong_bear:'B/S↓',
  d_absorb_bull:'Ab↑', d_absorb_bear:'Ab↓',
  d_div_bull:'Δ↑', d_div_bear:'Δ↓',
  d_cd_bull:'cd↑', d_cd_bear:'cd↓',
  d_surge_bull:'Δ↑S', d_surge_bear:'Δ↓S',
  d_blast_bull:'ΔΔ↑', d_blast_bear:'ΔΔ↓',
  d_vd_div_bull:'ΔΔ↑V', d_vd_div_bear:'ΔΔ↓V',
  d_spring:'dSPR', d_upthrust:'T↓',
  d_flip_bull:'FLP↑D', d_flip_bear:'FLP↓D', d_orange_bull:'ORG↑',
  d_blast_bull_red:'ΔΔ↑R', d_blast_bear_grn:'ΔΔ↓G',
  d_surge_bull_red:'Δ↑R', d_surge_bear_grn:'Δ↓G',
}

const lbl = (k) => SIG_LABEL[k] ?? k

function pctColor(p) {
  if (p >= 70) return 'text-lime-400 font-bold'
  if (p >= 50) return 'text-yellow-300 font-semibold'
  if (p >= 30) return 'text-sky-300'
  return 'text-md-on-surface-var'
}

function pctBg(p) {
  if (p >= 70) return 'bg-lime-900/30'
  if (p >= 50) return 'bg-yellow-900/20'
  if (p >= 30) return 'bg-sky-900/15'
  return ''
}

export default function SignalCorrelPanel() {
  const [data,      setData]      = useState(null)
  const [loading,   setLoading]   = useState(false)
  const [error,     setError]     = useState(null)
  const [tf,        setTf]        = useState('1d')
  const [universe,  setUniverse]  = useState('sp500')
  const [minPct,    setMinPct]    = useState(15)
  const [sortBy,    setSortBy]    = useState('max_pct')

  const load = () => {
    setLoading(true); setError(null)
    api.signalCorrelation(tf, universe, minPct)
      .then(d => { setData(d); setLoading(false) })
      .catch(e => { setError(e.message); setLoading(false) })
  }

  useEffect(() => { load() }, [tf, universe, minPct])

  const pairs = data?.pairs ?? []
  const sorted = [...pairs].sort((a, b) => b[sortBy] - a[sortBy])

  const SortTh = ({ col, children }) => (
    <th
      onClick={() => setSortBy(col)}
      className={`px-3 py-2 text-left text-xs font-medium cursor-pointer select-none transition-colors
        ${sortBy === col ? 'text-blue-300' : 'text-md-on-surface-var hover:text-md-on-surface'}`}>
      {children}{sortBy === col ? ' ↓' : ''}
    </th>
  )

  return (
    <div className="bg-md-surface-con rounded-md-md border border-md-outline-var text-xs text-md-on-surface">

      {/* Header */}
      <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-b border-md-outline-var">
        <span className="font-semibold text-sm text-white">Signal Co-occurrence</span>
        <span className="text-md-on-surface-var">When signal A fires, how often does B also fire?</span>

        <div className="flex gap-0.5 ml-auto">
          {['1wk','1d','4h','1h'].map(t => (
            <button key={t} onClick={() => setTf(t)}
              className={`px-2 py-0.5 rounded text-xs ${tf === t ? 'bg-blue-600 text-white' : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}>
              {t.toUpperCase()}
            </button>
          ))}
        </div>

        <div className="flex gap-0.5">
          {['sp500','nasdaq','russell2k'].map(u => (
            <button key={u} onClick={() => setUniverse(u)}
              className={`px-2 py-0.5 rounded text-xs ${universe === u ? 'bg-indigo-700 text-white' : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}>
              {u === 'sp500' ? 'SP500' : u === 'nasdaq' ? 'NDX' : 'R2K'}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1 text-md-on-surface-var">
          <span>min%</span>
          {[10, 15, 25, 40].map(v => (
            <button key={v} onClick={() => setMinPct(v)}
              className={`px-1.5 py-0.5 rounded ${minPct === v ? 'bg-gray-700 text-white' : 'text-md-on-surface-var/70 hover:text-white'}`}>
              {v}
            </button>
          ))}
        </div>

        <button onClick={load}
          className="px-2 py-0.5 rounded bg-md-surface-high text-md-on-surface-var hover:text-white text-xs">↺</button>

        {sorted.length > 0 && (
          <button
            onClick={() => {
              const header = 'Signal A,Signal B,Signal C,Together,A fires,B fires,% of A,% of B,max%,% of C'
              const rows = sorted.map(p =>
                `${lbl(p.sig_a)},${lbl(p.sig_b)},${p.top_c ? lbl(p.top_c) : ''},${p.both},${p.a_count},${p.b_count},${p.pct_a}%,${p.pct_b}%,${p.max_pct}%,${p.pct_c ?? ''}%`
              )
              const csv = [header, ...rows].join('\n')
              const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
              const a = document.createElement('a')
              a.href = url
              a.download = `signal_correlation_${tf}_${universe}_min${minPct}pct.csv`
              a.click()
              URL.revokeObjectURL(url)
            }}
            className="px-2 py-0.5 rounded bg-md-surface-high text-md-on-surface-var hover:text-white text-xs">
            ⬇ CSV
          </button>
        )}
      </div>

      {/* Stats bar */}
      {data && (
        <div className="px-4 py-2 border-b border-md-outline-var text-xs text-md-on-surface-var flex gap-4">
          <span>{data.n_tickers} tickers scanned</span>
          <span>{sorted.length} pairs ≥ {minPct}%</span>
          <span className="text-md-on-surface-var/70">
            {Object.entries(data.signal_counts ?? {})
              .filter(([, v]) => v > 0)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 8)
              .map(([k, v]) => `${lbl(k)}:${v}`)
              .join(' · ')}
          </span>
        </div>
      )}

      {loading && <div className="px-4 py-8 text-center text-md-on-surface-var animate-pulse">Loading…</div>}
      {error   && <div className="px-4 py-4 text-red-400">{error}</div>}
      {!loading && !error && sorted.length === 0 && (
        <div className="px-4 py-8 text-center text-md-on-surface-var/70">
          No pairs found — run a Turbo scan first, or lower min%
        </div>
      )}

      {!loading && sorted.length > 0 && (
        <div className="overflow-auto max-h-[520px]">
          <table className="w-full">
            <thead className="sticky top-0 bg-md-surface-con border-b border-md-outline-var">
              <tr>
                <th className="px-3 py-2 text-left text-xs text-md-on-surface-var font-medium">Signal A</th>
                <th className="px-3 py-2 text-left text-xs text-md-on-surface-var font-medium">Signal B</th>
                <th className="px-3 py-2 text-left text-xs text-md-on-surface-var font-medium">Signal C</th>
                <SortTh col="both">Together</SortTh>
                <SortTh col="a_count">A fires</SortTh>
                <SortTh col="b_count">B fires</SortTh>
                <SortTh col="pct_a">% of A</SortTh>
                <SortTh col="pct_b">% of B</SortTh>
                <SortTh col="max_pct">max%</SortTh>
                <SortTh col="pct_c">% of C</SortTh>
              </tr>
            </thead>
            <tbody>
              {sorted.map((p, i) => (
                <tr key={i} className={`border-b border-md-outline-var/50 ${pctBg(p.max_pct)}`}>
                  <td className="px-3 py-1.5 font-mono font-semibold text-blue-300">{lbl(p.sig_a)}</td>
                  <td className="px-3 py-1.5 font-mono font-semibold text-cyan-300">{lbl(p.sig_b)}</td>
                  <td className="px-3 py-1.5 font-mono text-violet-300">
                    {p.top_c ? (
                      <span title={p.top_c}>{lbl(p.top_c)}</span>
                    ) : <span className="text-gray-700">—</span>}
                  </td>
                  <td className="px-3 py-1.5 text-center text-md-on-surface">{p.both}</td>
                  <td className="px-3 py-1.5 text-center text-md-on-surface-var">{p.a_count}</td>
                  <td className="px-3 py-1.5 text-center text-md-on-surface-var">{p.b_count}</td>
                  <td className={`px-3 py-1.5 text-center ${pctColor(p.pct_a)}`}>{p.pct_a}%</td>
                  <td className={`px-3 py-1.5 text-center ${pctColor(p.pct_b)}`}>{p.pct_b}%</td>
                  <td className={`px-3 py-1.5 text-center font-bold ${pctColor(p.max_pct)}`}>{p.max_pct}%</td>
                  <td className={`px-3 py-1.5 text-center ${pctColor(p.pct_c ?? 0)}`}>
                    {p.pct_c != null ? `${p.pct_c}%` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="px-4 py-2 text-md-on-surface-var/70 text-[10px] border-t border-md-outline-var">
        % of A = when A fired, B also fired X% · Signal C = most common third signal when A+B both fire
      </div>
    </div>
  )
}

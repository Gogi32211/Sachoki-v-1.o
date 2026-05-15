import { useState } from 'react'
import { api } from '../api'
import PredictorPanel from './PredictorPanel'
import { pwlAdd, pwlHas } from './PersonalWatchlistPanel'
import { Card, Button, Alert, Spinner, EmptyState } from '../design-system'

// ── Engine family detection ───────────────────────────────────────────────────
function engineFamilies(r) {
  const fams = new Set()
  if (r.best_sig || r.strong_sig || r.vbo_up || r.abs_sig || r.ns || r.sq || r.load_sig || r.va) fams.add('Vol')
  if (r.wyk_spring || r.wyk_sos || r.wyk_lps || r.wyk_accum || r.wyk_markup) fams.add('Wyk')
  if (r.d_spring || r.d_strong_bull || r.d_absorb_bull || r.d_blast_bull || r.d_surge_bull || r.d_flip_bull || r.d_orange_bull) fams.add('Δ')
  if (r.tz_sig || r.tz_bull_flip || r.tz_attempt) fams.add('T/Z')
  if (r.fri34 || r.fri43 || r.l34 || r.preup66 || r.preup55 || r.preup3 || r.preup2 || r.preup50) fams.add('L')
  if (r.rocket || r.buy_2809 || r.seq_bcont) fams.add('Cmb')
  if (r.fbo_bull || r.eb_bull || r.rs_strong || r.ultra_3up) fams.add('Brk')
  return fams
}

function setupPhase(r) {
  const early = r.wyk_spring || r.d_spring || r.tz_bull_flip
  const late  = (r.rocket || r.buy_2809) && (r.fbo_bull || r.eb_bull || r.vbo_up)
  if (early && !late) return 'Early'
  if (late)           return 'Late'
  return null
}

function scoreColor(s) {
  if (s >= 65) return 'text-lime-300 font-bold'
  if (s >= 50) return 'text-yellow-300 font-semibold'
  if (s >= 35) return 'text-blue-300'
  return 'text-md-on-surface-var'
}

function scoreBg(s) {
  if (s >= 65) return 'bg-lime-900/20'
  if (s >= 50) return 'bg-yellow-900/15'
  if (s >= 35) return 'bg-blue-900/10'
  return ''
}

// ── Tier badge ────────────────────────────────────────────────────────────────
function TierBadge({ label, tier, age }) {
  const isOld = age > 0
  const cls = tier === 'A'
    ? isOld ? 'bg-lime-900/20 text-lime-400/50 ring-1 ring-lime-700/40'
            : 'bg-lime-900/50 text-lime-300 ring-1 ring-lime-600'
    : tier === 'B'
    ? isOld ? 'bg-sky-900/20 text-sky-400/50 ring-1 ring-sky-800/40'
            : 'bg-sky-900/40 text-sky-300 ring-1 ring-sky-700'
    : isOld ? 'bg-md-surface-high/50 text-md-on-surface-var/60'
            : 'bg-md-surface-high text-md-on-surface-var'
  return (
    <span className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md-sm text-[10px] leading-tight ${cls}`}>
      {tier && <span className="text-[8px] opacity-50">{tier}</span>}
      <span className="font-mono font-semibold">{label}</span>
      {isOld && <span className="text-[8px] opacity-60 ml-0.5">{age}b</span>}
    </span>
  )
}

// ── Signal grid ───────────────────────────────────────────────────────────────
function SignalGrid({ r, ages, N }) {
  const age  = (key) => ages?.[key] ?? (r[key] ? 0 : 999)
  const show = (key) => age(key) < N

  const groups = [
    {
      label: 'VABS / Vol', items: [
        show('best_sig')   && ['best_sig',   'BEST★', 'A'],
        show('strong_sig') && ['strong_sig', 'STR',   'A'],
        show('vbo_up')     && ['vbo_up',     'VBO↑',  'A'],
        show('abs_sig')    && ['abs_sig',    'ABS',   'B'],
        show('ns')         && ['ns',         'NS',    'B'],
        show('sq')         && ['sq',         'SQ',    'B'],
        show('load_sig')   && ['load_sig',   'LD',    'B'],
        show('va')         && ['va',         'VA',    'B'],
        show('sig_l88')    && ['sig_l88',    'L88',   'B'],
      ].filter(Boolean),
    },
    {
      label: 'Wyckoff', items: [
        show('wyk_spring') && ['wyk_spring', 'wSPR', 'A'],
        show('wyk_sos')    && ['wyk_sos',    'SOS',  'A'],
        show('wyk_lps')    && ['wyk_lps',    'LPS',  'B'],
        show('wyk_accum')  && ['wyk_accum',  'ACC',  'B'],
        show('wyk_markup') && ['wyk_markup', 'MKP',  'B'],
        show('wyk_sc')     && ['wyk_sc',     'wSC',  null],
        show('wyk_ar')     && ['wyk_ar',     'AR',   null],
        show('wyk_st')     && ['wyk_st',     'ST',   null],
      ].filter(Boolean),
    },
    {
      label: 'Delta / Order Flow', items: [
        show('d_spring')         && ['d_spring',         'dSPR', 'A'],
        show('d_blast_bull')     && ['d_blast_bull',     'ΔΔ↑',  'A'],
        show('d_surge_bull')     && ['d_surge_bull',     'Δ↑',   'B'],
        show('d_strong_bull')    && ['d_strong_bull',    'B/S↑', 'B'],
        show('d_absorb_bull')    && ['d_absorb_bull',    'Ab↑',  'B'],
        show('d_div_bull')       && ['d_div_bull',       'T↓',   null],
        show('d_vd_div_bull')    && ['d_vd_div_bull',    'NS',   null],
        show('d_flip_bull')      && ['d_flip_bull',      'FLP↑', 'B'],
        show('d_orange_bull')    && ['d_orange_bull',    'ORG↑', null],
        show('d_blast_bull_red') && ['d_blast_bull_red', 'ΔΔ↑R', 'B'],
        show('d_surge_bull_red') && ['d_surge_bull_red', 'Δ↑R',  'B'],
        show('d_surge_bear_grn') && ['d_surge_bear_grn', 'Δ↓G', null],
        show('d_blast_bear_grn') && ['d_blast_bear_grn', 'ΔΔ↓G', null],
        show('d_vd_div_bear')    && ['d_vd_div_bear',    'ND',   null],
      ].filter(Boolean),
    },
    {
      label: 'T/Z Candlestick', items: [
        r.tz_sig        && ['tz_sig',       r.tz_sig + (r.l_combo && r.l_combo !== 'NONE' ? ` [${r.l_combo}]` : ''), ['T4','T6','T1G','T2G'].includes(r.tz_sig) ? 'A' : 'B'],
        show('tz_bull_flip') && ['tz_bull_flip', 'TZ→3', 'B'],
        show('tz_attempt')   && ['tz_attempt',   'TZ→2', null],
        (show('tz_weak_bull') || show('tz_weak_bear')) && ['tz_weak_bull', 'W', 'B'],
        r.ca && ['ca', 'CA', null],
        r.cd && ['cd', 'CD', null],
        r.cw && ['cw', 'CW', null],
      ].filter(Boolean),
    },
    {
      label: 'WLNBB / L-structure', items: [
        r.l_combo && r.l_combo !== 'NONE' && r.l_combo !== '' && ['_lcombo', r.l_combo, null],
        show('fri34')   && ['fri34',   'FRI34', 'A'],
        show('fri43')   && ['fri43',   'FRI43', 'B'],
        show('l34')     && ['l34',     'L34',   'B'],
        show('preup66') && ['preup66', 'P66',   'A'],
        show('preup55') && ['preup55', 'P55',   'B'],
        show('preup89') && ['preup89', 'P89',   'B'],
        show('preup3')  && ['preup3',  'P3',    null],
        show('preup2')  && ['preup2',  'P2',    null],
        show('preup50') && ['preup50', 'P50',   null],
        show('blue')    && ['blue',    'BLUE',  null],
      ].filter(Boolean),
    },
    {
      label: 'Combo / 2809', items: [
        show('rocket')    && ['rocket',    '🚀',    'A'],
        show('buy_2809')  && ['buy_2809',  'BUY',   'A'],
        show('seq_bcont') && ['seq_bcont', 'SBC',   'B'],
        show('sig3g')     && ['sig3g',     '3G',    null],
        show('rtv')       && ['rtv',       'RTV',   null],
        show('hilo_buy')  && ['hilo_buy',  'HILO↑', null],
        show('atr_brk')   && ['atr_brk',  'ATR↑',  null],
        show('bb_brk')    && ['bb_brk',   'BB↑',   null],
      ].filter(Boolean),
    },
    {
      label: 'Breakout / ULTRA', items: [
        r.fbo_bull  && ['fbo_bull',  'FBO↑', 'A'],
        r.eb_bull   && ['eb_bull',   'EB↑',  'A'],
        r.rs_strong && ['rs_strong', 'RS+',  'B'],
        r.rs        && ['rs',        'RS',   null],
        r.ultra_3up && ['ultra_3up', '3↑',   null],
      ].filter(Boolean),
    },
    {
      label: 'Wick X signals', items: [
        show('x2g_wick')  && ['x2g_wick',  'X2G', 'A'],
        show('x2_wick')   && ['x2_wick',   'X2',  'A'],
        show('x1g_wick')  && ['x1g_wick',  'X1G', 'B'],
        show('x1_wick')   && ['x1_wick',   'X1',  'B'],
        show('x3_wick')   && ['x3_wick',   'X3',  null],
        show('wick_bull') && ['wick_bull',  'W↑',  null],
      ].filter(Boolean),
    },
    {
      label: 'B / G Signals', items: [
        ...[1,2,3,4,5,6,7,8,9,10,11]
          .filter(i => show(`b${i}`))
          .map(i => [`b${i}`, `B${i}`, i === 1 || i === 10 ? 'B' : null]),
        ...['g1','g2','g4','g6','g11']
          .filter(k => show(k))
          .map(k => [k, k.toUpperCase(), 'B']),
      ],
    },
  ]

  return (
    <div className="space-y-3">
      {groups.map(g => g.items.length > 0 && (
        <div key={g.label}>
          <div className="text-[10px] text-md-on-surface-var/60 uppercase tracking-wide mb-1">{g.label}</div>
          <div className="flex flex-wrap gap-1">
            {g.items.map(([key, lbl, tier]) => (
              <TierBadge key={key} label={lbl} tier={tier} age={age(key)} />
            ))}
          </div>
        </div>
      ))}
      {groups.every(g => g.items.length === 0) && (
        <p className="text-md-on-surface-var text-xs">
          No signals fired in the last {N} bar{N > 1 ? 's' : ''}
        </p>
      )}
    </div>
  )
}

const TF_OPTS = ['1wk', '1d', '4h', '1h', '30m', '15m']
const N_OPTS  = [1, 3, 5, 10]

// ─────────────────────────────────────────────────────────────────────────────
export default function TickerAnalysisPanel({ onAddToWatchlist, onChartChange }) {
  const [input,   setInput]   = useState('')
  const [ticker,  setTicker]  = useState('')
  const [tf,      setTf]      = useState('1d')
  const [result,  setResult]  = useState(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)
  const [added,   setAdded]   = useState(false)
  const [N,       setN]       = useState(1)

  const analyze = (sym = input, timeframe = tf) => {
    const t = sym.trim().toUpperCase()
    if (!t) return
    setTicker(t)
    setAdded(false)
    setLoading(true); setError(null); setResult(null)
    onChartChange?.({ ticker: t, tf: timeframe })
    api.turboAnalyze(t, timeframe)
      .then(d => { setResult(d); setLoading(false) })
      .catch(e => { setError(e.message); setLoading(false) })
  }

  const changeTf = (t) => {
    setTf(t)
    if (ticker) { onChartChange?.({ ticker, tf: t }); analyze(ticker, t) }
  }

  const handleAdd = () => {
    if (result?.ticker) {
      onAddToWatchlist?.(result.ticker)
      if (!pwlHas(result.ticker, result._tf || '1d')) {
        pwlAdd({ ...result, _tf: result._tf || '1d' })
      }
      setAdded(true)
    }
  }

  const r = result

  const n     = r ? engineFamilies(r).size : 0
  const cross = n >= 4 ? '⚡×4' : n === 3 ? '⚡×3' : n === 2 ? '⚡×2' : ''
  const phase = r ? setupPhase(r) : null

  return (
    <Card variant="outlined" padding="none" className="text-xs">

      {/* ── Header / input ── */}
      <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-b border-md-outline-var">
        <div>
          <span className="font-semibold text-sm text-md-on-surface">Ticker Analysis</span>
          <span className="text-md-on-surface-var ml-2 text-xs">Full Turbo engine on any ticker</span>
        </div>

        {/* Ticker input + analyze */}
        <div className="flex items-center gap-1.5 ml-auto">
          <input
            value={input}
            onChange={e => setInput(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === 'Enter' && analyze()}
            placeholder="e.g. RGTI"
            className="bg-md-surface-high border border-md-outline-var rounded-md-sm px-2 py-1.5 text-xs text-md-on-surface
                       placeholder:text-md-on-surface-var/50 focus:outline-none focus:border-md-primary w-24 transition-colors"
          />
          <Button variant="tonal" size="sm" onClick={() => analyze()} disabled={loading}>
            {loading ? <Spinner size={12} /> : 'Analyze'}
          </Button>
        </div>

        {/* TF selector */}
        <div className="flex rounded-md-sm overflow-hidden border border-md-outline-var">
          {TF_OPTS.map((t, i) => (
            <button key={t} onClick={() => changeTf(t)}
              className={[
                'px-2 py-1 text-xs font-medium transition-colors',
                i > 0 ? 'border-l border-md-outline-var' : '',
                tf === t
                  ? 'bg-md-primary-container text-md-on-primary-container'
                  : 'text-md-on-surface-var hover:bg-white/5',
              ].join(' ')}>
              {t.toUpperCase()}
            </button>
          ))}
        </div>

        {/* N selector */}
        <div className="flex items-center gap-1">
          <span className="text-md-on-surface-var text-[10px]">last</span>
          <div className="flex rounded-md-sm overflow-hidden border border-md-outline-var">
            {N_OPTS.map((n, i) => (
              <button key={n} onClick={() => setN(n)}
                className={[
                  'px-2 py-1 text-xs font-medium transition-colors',
                  i > 0 ? 'border-l border-md-outline-var' : '',
                  N === n
                    ? 'bg-md-primary-container text-md-on-primary-container'
                    : 'text-md-on-surface-var hover:bg-white/5',
                ].join(' ')}>
                {n}
              </button>
            ))}
          </div>
          <span className="text-md-on-surface-var text-[10px]">bars</span>
        </div>
      </div>

      {/* States */}
      {loading && (
        <div className="flex items-center justify-center gap-2 py-10 text-md-on-surface-var">
          <Spinner size={14} />
          <span>Analyzing {ticker}…</span>
        </div>
      )}
      {error && (
        <div className="p-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}
      {!ticker && !loading && !r && !error && (
        <EmptyState
          icon="🔍"
          message="Enter a ticker and press Analyze"
          sub="Runs the full Turbo engine on any ticker and shows active signals."
        />
      )}

      {/* Result */}
      {r && (
        <div className="p-4 space-y-5">

          {/* Score summary */}
          <div className={`rounded-md-md p-4 border border-md-outline-var ${scoreBg(r.turbo_score)}`}>
            <div className="flex items-center gap-3 mb-1 flex-wrap">
              <span className="text-2xl font-bold text-md-on-surface">{r.ticker}</span>
              <span className={`text-xl font-bold ${scoreColor(r.turbo_score)}`}>
                {r.turbo_score?.toFixed(1)}
              </span>
              <span className="text-md-on-surface-var text-xs">Turbo Score</span>
              {cross && <span className="text-lime-300 font-semibold">{cross}</span>}
              {phase === 'Early' && (
                <span className="text-sky-300 text-[10px] bg-sky-900/30 px-1.5 py-0.5 rounded-md-sm">[E] Early</span>
              )}
              {phase === 'Late' && (
                <span className="text-orange-300 text-[10px] bg-orange-900/30 px-1.5 py-0.5 rounded-md-sm">[L] Late</span>
              )}
              {r.rtb_phase && r.rtb_phase !== '0' && (
                <span
                  title={`RTB v4 | Phase ${r.rtb_phase} | Build ${r.rtb_build ?? 0} Turn ${r.rtb_turn ?? 0} Ready ${r.rtb_ready ?? 0} Late ${r.rtb_late ?? 0}${r.rtb_transition ? ' | ' + r.rtb_transition : ''}`}
                  className={`text-[10px] px-1.5 py-0.5 rounded-md-sm font-bold cursor-default ${
                    r.rtb_phase === 'C' ? 'bg-lime-700/80 text-lime-100 ring-1 ring-lime-400' :
                    r.rtb_phase === 'B' ? 'bg-sky-800/80  text-sky-200  ring-1 ring-sky-500' :
                    r.rtb_phase === 'A' ? 'bg-md-surface-high text-md-on-surface-var' :
                    /* D */              'bg-orange-800/70 text-orange-200'
                  }`}>
                  RTB {r.rtb_phase}
                  <span className="font-normal ml-1 opacity-70">{Math.round(r.rtb_total ?? 0)}</span>
                </span>
              )}
              {r.data_source === 'yfinance' && (
                <span className="text-orange-400/60 text-[9px]">yf</span>
              )}
              {onAddToWatchlist && (
                <Button
                  variant={added ? 'tonal' : 'outlined'}
                  size="sm"
                  onClick={handleAdd}
                  disabled={added}
                  className="ml-auto"
                >
                  {added ? '✓ Added' : '+ Watchlist'}
                </Button>
              )}
            </div>

            <div className="flex items-center gap-4 text-xs text-md-on-surface-var mt-1">
              <span className="text-md-on-surface">${r.last_price?.toFixed(2)}</span>
              <span className={r.change_pct >= 0 ? 'text-md-positive' : 'text-md-negative'}>
                {r.change_pct >= 0 ? '+' : ''}{r.change_pct?.toFixed(2)}%
              </span>
              {r.tz_sig && (
                <span className={`font-mono font-semibold ${['T4','T6','T1G','T2G'].includes(r.tz_sig) ? 'text-violet-300' : 'text-md-on-surface-var'}`}>
                  {r.tz_sig}
                </span>
              )}
              {r.rsi != null && <span>RSI {r.rsi?.toFixed(0)}</span>}
              {r.br_score > 0 && (
                <span className={r.br_score >= 71 ? 'text-md-positive' : ''}>
                  BR {r.br_score?.toFixed(0)}
                </span>
              )}
              {n > 0 && (
                <span>{n} engine {n === 1 ? 'family' : 'families'} active</span>
              )}
            </div>
          </div>

          {/* Active signals */}
          <div>
            <div className="text-xs text-md-on-surface-var font-semibold uppercase tracking-wide mb-2">
              Signals — last {N} bar{N > 1 ? 's' : ''}
              <span className="ml-2 font-normal normal-case">
                <span className="text-lime-300/70">A</span> = Tier A ·{' '}
                <span className="text-sky-300/70">B</span> = amplifier ·{' '}
                <span className="opacity-60">dimmed = fired N bars ago</span>
              </span>
            </div>
            <SignalGrid
              r={r}
              ages={(() => { try { return JSON.parse(r.sig_ages || '{}') } catch { return {} } })()}
              N={N}
            />
          </div>

          {/* Predictor */}
          <div>
            <div className="text-xs text-md-on-surface-var font-semibold uppercase tracking-wide mb-2">
              Predictor
            </div>
            <PredictorPanel ticker={r.ticker} tf={tf} />
          </div>

        </div>
      )}
    </Card>
  )
}

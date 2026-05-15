import { useState, useEffect, useRef, useCallback } from 'react'
import TickerInput from './components/TickerInput'
import WatchlistPanel from './components/WatchlistPanel'
import CandleChart from './components/CandleChart'
import PredictorPanel from './components/PredictorPanel'
import ScannerPanel from './components/ScannerPanel'
import CombinedScanPanel from './components/CombinedScanPanel'
import TZLStatsPanel from './components/TZLStatsPanel'
import HowItWorksPanel from './components/HowItWorksPanel'
import TurboScanPanel from './components/TurboScanPanel'
import UltraScanPanel from './components/UltraScanPanel'
import AdminPanel from './components/AdminPanel'
import SignalCorrelPanel from './components/SignalCorrelPanel'
import TickerAnalysisPanel from './components/TickerAnalysisPanel'
import PersonalWatchlistPanel from './components/PersonalWatchlistPanel'
import SuperchartPanel from './components/SuperchartPanel'
import SectorAnalysisPanel from './components/SectorAnalysisPanel'
import ReplayPanel from './components/ReplayPanel'
import TZWLNBBPanel from './components/TZWLNBBPanel'
import TZIntelligencePanel from './components/TZIntelligencePanel'
import RareReversalPanel from './components/RareReversalPanel'
import SequenceScanPanel from './components/SequenceScanPanel'
import PortfolioPanel from './components/PortfolioPanel'
import ChartObsPanel from './components/ChartObsPanel'
import SignalReplayPanel from './components/SignalReplayPanel'
import UltraPumpResearchPanel from './components/UltraPumpResearchPanel'
import TradingDashboardPanel from './components/TradingDashboardPanel'
import AppSidebar from './components/AppSidebar'

// ── localStorage helpers ──────────────────────────────────────────────────────
const LS = {
  get: (key, fallback) => {
    try { const v = localStorage.getItem(key); return v !== null ? JSON.parse(v) : fallback }
    catch { return fallback }
  },
  set: (key, val) => {
    try { localStorage.setItem(key, JSON.stringify(val)) } catch {}
  },
}

// ── Navigation structure (grouped) ───────────────────────────────────────────
const TAB_GROUPS = [
  {
    label: 'Main',
    tabs: [
      { id: 'dashboard',  label: '🏠 Dashboard' },
      { id: 'turbo',      label: '⚡ Turbo' },
      { id: 'ultra',      label: '🧬 Ultra' },
      { id: 'superchart', label: '📋 Superchart' },
    ],
  },
  {
    label: 'Signals',
    tabs: [
      { id: 'scanner',      label: '🎯 T/Z Scanner' },
      { id: 'sequences',    label: '🔢 Sequences' },
      { id: 'tzwlnbb',      label: '📡 TZ/WLNBB' },
      { id: 'rarereversal', label: '🔄 Rare Rev.' },
      { id: 'chartobs',     label: '👁 Obs' },
      { id: 'predictor',    label: '🔮 Predictor' },
      { id: 'combined',     label: '◐ Combined' },
    ],
  },
  {
    label: 'Research',
    tabs: [
      { id: 'analyze',        label: '🔍 Analyze' },
      { id: 'sigreplay',      label: '🧪 Pump Research' },
      { id: 'replay',         label: '🔬 Replay' },
      { id: 'tzlstats',       label: '📈 T/Z × L Stats' },
      { id: 'corr',           label: '📊 Corr' },
      { id: 'tzintelligence', label: '🧠 TZ Intel' },
    ],
  },
  {
    label: 'Market',
    tabs: [
      { id: 'sectors', label: '🌐 Sectors' },
    ],
  },
  {
    label: 'Portfolio',
    tabs: [
      { id: 'watchlist', label: '⭐ Watchlist' },
      { id: 'portfolio', label: '💼 Portfolio' },
    ],
  },
  {
    label: 'System',
    tabs: [
      { id: 'howitworks', label: '❔ How It Works' },
      { id: 'admin',      label: '⚙ Admin' },
    ],
  },
]

const TABS = TAB_GROUPS.flatMap(g => g.tabs)
const VALID_TAB_IDS = new Set(TABS.map(t => t.id))
const TF_OPTIONS = ['1d', '4h', '1h', '30m', '15m']

// Tabs that manage their own chart or don't need the global chart
const NO_CHART_TABS = new Set(['turbo', 'dashboard'])

export default function App() {
  const [watchlist, setWatchlist] = useState(
    () => LS.get('watchlist', ['AAPL', 'TSLA', 'NVDA'])
  )
  const [selected, setSelected] = useState(
    () => LS.get('selected_ticker', 'AAPL')
  )
  const [tf, setTf] = useState(
    () => LS.get('tf', '1d')
  )
  const [activeTab, setActiveTab] = useState(() => {
    const saved = LS.get('active_tab', 'dashboard')
    return VALID_TAB_IDS.has(saved) ? saved : 'dashboard'
  })
  const [analyzeChart, setAnalyzeChart] = useState({ ticker: null, tf: '1d' })
  const [scTicker, setScTicker] = useState(null)
  const [scTf, setScTf]         = useState('1d')
  const chartInstanceRef        = useRef(null)
  const [chartReady, setChartReady] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => LS.get('sidebar_collapsed', false)
  )
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)

  const handleChartReady = useCallback((chart) => {
    chartInstanceRef.current = chart
    setChartReady(!!chart)
  }, [])

  useEffect(() => { LS.set('watchlist', watchlist) }, [watchlist])
  useEffect(() => { LS.set('selected_ticker', selected) }, [selected])
  useEffect(() => { LS.set('tf', tf) }, [tf])
  useEffect(() => { LS.set('active_tab', activeTab) }, [activeTab])
  useEffect(() => { LS.set('sidebar_collapsed', sidebarCollapsed) }, [sidebarCollapsed])

  // Cmd/Ctrl + B toggles sidebar collapsed state
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'b' || e.key === 'B')) {
        e.preventDefault()
        setSidebarCollapsed(v => !v)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const handleSelect       = (ticker) => setSelected(ticker)
  const handleAddTicker    = (t) => setWatchlist(prev => [...new Set([...prev, t.toUpperCase()])])
  const handleRemoveTicker = (t) => setWatchlist(prev => prev.filter(x => x !== t))
  const handleOpenChart    = useCallback((ticker) => {
    setSelected(ticker)
    setActiveTab('superchart')
  }, [])

  const chartTicker =
    activeTab === 'superchart' && scTicker           ? scTicker :
    activeTab === 'analyze'    && analyzeChart.ticker ? analyzeChart.ticker :
    selected
  const chartTf =
    activeTab === 'superchart' && scTicker           ? scTf :
    activeTab === 'analyze'    && analyzeChart.ticker ? analyzeChart.tf :
    tf

  const activeTabLabel = TABS.find(t => t.id === activeTab)?.label || ''

  return (
    <div className="min-h-screen flex bg-md-surface text-md-on-surface">
      {/* ── Left sidebar navigation ──────────────────────────────────────── */}
      <AppSidebar
        groups={TAB_GROUPS}
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        collapsed={sidebarCollapsed}
        onToggleCollapsed={() => setSidebarCollapsed(v => !v)}
        mobileOpen={mobileSidebarOpen}
        onCloseMobile={() => setMobileSidebarOpen(false)}
      />

      {/* ── Right column: trading control top bar + content ──────────────── */}
      <div className="flex-1 flex flex-col min-w-0">
        <header className="sticky top-0 z-30 flex items-center gap-3 px-3 md:px-4 h-14 shadow-md-2 bg-md-surface-high shrink-0">
          {/* Mobile hamburger */}
          <button
            onClick={() => setMobileSidebarOpen(true)}
            aria-label="Open menu"
            className="md:hidden p-1.5 rounded-md-sm text-md-on-surface-var hover:bg-white/5"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M3 6h14M3 10h14M3 14h14" />
            </svg>
          </button>

          {/* Brand (visible only on mobile — sidebar header shows it on desktop) */}
          <div className="flex md:hidden items-center gap-2 shrink-0">
            <span className="text-base font-semibold tracking-tight text-md-primary">Sachoki</span>
          </div>

          {/* Current page label */}
          <div className="hidden md:flex items-center gap-2 shrink-0">
            <span className="text-sm font-medium text-md-on-surface truncate max-w-[200px]">
              {activeTabLabel}
            </span>
            <span className="text-[10px] text-md-on-surface-var/70">v4.7.24</span>
          </div>

          {/* Timeframe segmented control */}
          <div className="flex rounded-md-sm overflow-hidden border border-md-outline-var shrink-0">
            {TF_OPTIONS.map((t, i) => (
              <button
                key={t}
                onClick={() => setTf(t)}
                className={[
                  'px-2.5 sm:px-3 py-1.5 text-xs font-medium transition-colors duration-100',
                  i > 0 ? 'border-l border-md-outline-var' : '',
                  tf === t
                    ? 'bg-md-primary-container text-md-on-primary-container'
                    : 'text-md-on-surface-var hover:bg-white/5',
                ].join(' ')}
              >
                {t}
              </button>
            ))}
          </div>

          {/* Ticker input */}
          <div className="flex-1 min-w-0 max-w-sm">
            <TickerInput
              watchlist={watchlist}
              onAdd={handleAddTicker}
              onRemove={handleRemoveTicker}
              tf={tf}
              onTfChange={setTf}
            />
          </div>

          {/* Selected ticker pill (desktop) */}
          {selected && (
            <div className="hidden lg:flex items-center gap-1.5 px-2 py-1 rounded-md-sm bg-md-surface-con border border-md-outline-var">
              <span className="text-[10px] text-md-on-surface-var">Sel</span>
              <span className="text-xs font-mono font-semibold text-md-on-surface">{selected}</span>
            </div>
          )}
        </header>

        {/* ── Content area ───────────────────────────────────────────────── */}
        <main className="flex flex-col gap-3 p-3 flex-1 min-w-0">
        {/* Global chart — hidden on dashboard and turbo tabs */}
        {!NO_CHART_TABS.has(activeTab) && (
          <div style={{ minHeight: '340px' }}>
            <CandleChart
              ticker={chartTicker}
              tf={chartTf}
              onChartReady={handleChartReady}
            />
          </div>
        )}

        {/* Tab content */}
        <div className="min-h-[400px]">
          {activeTab === 'dashboard' && (
            <TradingDashboardPanel
              onSelectTicker={handleSelect}
              onAddToWatchlist={handleAddTicker}
              watchlistTickers={watchlist}
              onOpenChart={handleOpenChart}
            />
          )}

          {/* Always-mounted: preserve scan results on tab switch */}
          <div style={{ display: activeTab === 'turbo' ? 'block' : 'none' }}>
            <TurboScanPanel onSelectTicker={handleSelect} />
          </div>
          <div style={{ display: activeTab === 'ultra' ? 'block' : 'none' }}>
            <UltraScanPanel onSelectTicker={handleSelect} />
          </div>

          {activeTab === 'watchlist'      && <PersonalWatchlistPanel watchlistTickers={watchlist} onSelectTicker={handleSelect} onAddTicker={handleAddTicker} onRemoveTicker={handleRemoveTicker} />}
          {activeTab === 'combined'       && <CombinedScanPanel tf={tf} onSelectTicker={handleSelect} />}
          {activeTab === 'predictor'      && <PredictorPanel ticker={selected} tf={tf} />}
          {activeTab === 'scanner'        && <ScannerPanel tf={tf} onSelectTicker={handleSelect} />}
          {activeTab === 'tzlstats'       && <TZLStatsPanel ticker={selected} tf={tf} />}
          {activeTab === 'corr'           && <SignalCorrelPanel />}
          {activeTab === 'superchart'     && <SuperchartPanel initialTicker={selected} initialTf={tf} onTickerChange={(t, f) => { setScTicker(t); setScTf(f) }} />}
          {activeTab === 'sectors'        && <SectorAnalysisPanel onSelectTicker={handleSelect} />}
          {activeTab === 'analyze'        && <TickerAnalysisPanel onAddToWatchlist={handleAddTicker} onChartChange={setAnalyzeChart} />}
          {activeTab === 'replay'         && <ReplayPanel />}
          {activeTab === 'tzwlnbb'        && <TZWLNBBPanel />}
          {activeTab === 'tzintelligence' && <TZIntelligencePanel onSelectTicker={handleSelect} />}
          {activeTab === 'rarereversal'   && <RareReversalPanel />}
          {activeTab === 'sequences'      && <SequenceScanPanel />}
          {activeTab === 'howitworks'     && <HowItWorksPanel />}
          {activeTab === 'portfolio'      && <PortfolioPanel />}
          {activeTab === 'chartobs'       && <ChartObsPanel onSelectTicker={handleSelect} />}
          {activeTab === 'sigreplay'      && <UltraPumpResearchPanel />}
          {activeTab === 'admin'          && <AdminPanel />}
        </div>
        </main>
      </div>
    </div>
  )
}

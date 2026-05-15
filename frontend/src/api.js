const BASE = import.meta.env.VITE_API_URL || ''

async function get(path) {
  const res = await fetch(BASE + path)
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json()
}

async function post(path, body) {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json()
}

export const api = {
  // Signals + chart (now includes WLNBB columns + per-bar scores)
  signals: (ticker, tf = '1d', bars = 150) =>
    get(`/api/signals/${ticker}?tf=${tf}&bars=${bars}`),

  tickerInfo: (ticker) =>
    get(`/api/ticker-info/${ticker}`),

  tickerInfoBatch: (tickers) =>
    post('/api/ticker-info-batch', { tickers }),

  wlnbb: (ticker, tf = '1d', bars = 150) =>
    get(`/api/wlnbb/${ticker}?tf=${tf}&bars=${bars}`),

  // Watchlist
  watchlist: (tickers, tf = '1d') =>
    get(`/api/watchlist?tickers=${tickers.join(',')}&tf=${tf}`),
  watchlistSaved: () =>
    get('/api/watchlist/saved'),
  watchlistSave: (tickers) =>
    post('/api/watchlist/save', { tickers }),

  // Predictor (T/Z + L-combo, 4 tables)
  predict: (ticker, tf = '1d') =>
    get(`/api/predict/${ticker}?tf=${tf}`),
  pooledPredict: (ticker, tf = '1d', universe = 'sp500') =>
    get(`/api/pooled-predict/${ticker}?tf=${tf}&universe=${universe}`),
  pooledStatsBuild: (universe = 'sp500', interval = '1d', max_tickers = 2000) =>
    post(`/api/pooled-stats/build?universe=${universe}&interval=${interval}&max_tickers=${max_tickers}`),
  pooledStatsStatus: (universe = 'sp500', interval = '1d') =>
    get(`/api/pooled-stats/status?universe=${universe}&interval=${interval}`),

  // L-combo predictor (dedicated)
  lPredict: (ticker, tf = '1d') =>
    get(`/api/l-predict/${ticker}?tf=${tf}`),

  // T/Z × L stats matrix
  tzLStats: (ticker, tf = '1d') =>
    get(`/api/tz-l-stats/${ticker}?tf=${tf}`),

  // Scanner
  scanResults: (tf = '1d', limit = 100, tab = 'all', min_score = 0) =>
    get(`/api/scan/results?tf=${tf}&limit=${limit}&tab=${tab}&min_score=${min_score}`),
  scanTrigger: (tf = '1d') =>
    post(`/api/scan/trigger?tf=${tf}`),
  scanStatus: () =>
    get('/api/scan/status'),

  // Combined
  combinedScan: (tf = '1d', min_score = 4, tab = 'bull', limit = 100) =>
    get(`/api/combined-scan?tf=${tf}&min_score=${min_score}&tab=${tab}&limit=${limit}`),

  // Pump combos
  pumpCombos: (threshold = 2.0, window = 20, combo_len = 3, limit = 50) =>
    get(`/api/pump-combos?threshold=${threshold}&window=${window}&combo_len=${combo_len}&limit=${limit}`),
  pumpTrigger: (threshold = 2.0, window = 20, combo_len = 3) =>
    post(`/api/pump-combos/trigger?threshold=${threshold}&window=${window}&combo_len=${combo_len}`),

  // 260323 Combo scan
  comboScan: (signal = 'all', limit = 200) =>
    get(`/api/combo-scan?signal=${signal}&limit=${limit}`),
  comboScanTrigger: (tf = '1d', n_bars = 3) =>
    post(`/api/combo-scan/trigger?tf=${tf}&n_bars=${n_bars}`),
  comboScanStatus: () =>
    get('/api/combo-scan/status'),
  comboScanDebug: (ticker, tf = '1d', rows = 7, n_bars = 3) =>
    get(`/api/combo-scan/debug/${ticker}?tf=${tf}&rows=${rows}&n_bars=${n_bars}`),

  // Power Scan (260323 + T/Z + WLNBB confluence)
  powerScan: (limit = 200) =>
    get(`/api/power-scan?limit=${limit}`),
  powerScanTrigger: (tf = '1d', n_bars = 3) =>
    post(`/api/power-scan/trigger?tf=${tf}&n_bars=${n_bars}`),
  powerScanStatus: () =>
    get('/api/power-scan/status'),

  // BR Scan (260328 Break Readiness)
  brScan: (limit = 300, min_br = 0, entry = 'all', tf = '1d') =>
    get(`/api/br-scan?limit=${limit}&min_br=${min_br}&entry=${entry}&tf=${tf}`),
  brScanTrigger: (tf = '1d') =>
    post(`/api/br-scan/trigger?tf=${tf}`),
  brScanStatus: () =>
    get('/api/br-scan/status'),

  // Turbo Scan (unified all-engine)
  turboScan: (limit = 2000, min_score = 0, direction = 'bull', tf = '1d', universe = 'sp500', filters = {}) => {
    const p = new URLSearchParams({ limit, min_score, direction, tf, universe })
    if (filters.price_min > 0)    p.set('price_min', filters.price_min)
    if (filters.price_max < 1e9)  p.set('price_max', filters.price_max)
    if (filters.rsi_min > 0)      p.set('rsi_min',   filters.rsi_min)
    if (filters.rsi_max < 100)    p.set('rsi_max',   filters.rsi_max)
    if (filters.cci_min > -9999)  p.set('cci_min',   filters.cci_min)
    if (filters.cci_max < 9999)   p.set('cci_max',   filters.cci_max)
    if (filters.vol_min > 0)      p.set('vol_min',   filters.vol_min)
    if (filters.vol_max > 0)      p.set('vol_max',   filters.vol_max)
    return get(`/api/turbo-scan?${p}`)
  },
  turboScanTrigger: (tf = '1d', universe = 'sp500', lookback_n = 5, partialDay = false, minVolume = 0, minStoreScore = 5) =>
    post(`/api/turbo-scan/trigger?tf=${tf}&universe=${universe}&lookback_n=${lookback_n}&partial_day=${partialDay}&min_volume=${minVolume}&min_store_score=${minStoreScore}`),
  turboScanStatus: () =>
    get('/api/turbo-scan/status'),
  turboScanReset: () =>
    post('/api/turbo-scan/reset'),

  // Signal correlation
  signalCorrelation: (tf = '1d', universe = 'sp500', min_pct = 15) =>
    get(`/api/signal-correlation?tf=${tf}&universe=${universe}&min_pct=${min_pct}`),

  // Single-ticker Turbo analysis
  turboAnalyze: (ticker, tf = '1d') =>
    get(`/api/turbo-analyze/${ticker}?tf=${tf}`),

  // Signal stats — per-ticker signal performance analyzer
  signalStats: (ticker, tf = '1d', signals = [], combo = false, minN = 5) => {
    const p = new URLSearchParams({ tf, signals: signals.join(','), combo, min_n: minN })
    return get(`/api/signal-stats/${ticker}?${p}`)
  },

  // Signal stats — pooled SP500 aggregate
  signalStatsPooledBuild: (tf = '1d', universe = 'sp500', signals = [], maxTickers = 500) =>
    post(`/api/signal-stats/pooled/build?tf=${tf}&universe=${universe}&signals=${signals.join(',')}&max_tickers=${maxTickers}`),
  signalStatsPooledStatus: (tf = '1d', universe = 'sp500') =>
    get(`/api/signal-stats/pooled/status?tf=${tf}&universe=${universe}`),

  // Superchart — per-bar signal matrix
  barSignals: (ticker, tf = '1d', bars = 80) =>
    get(`/api/bar_signals/${ticker}?tf=${tf}&bars=${bars}`),

  // Settings
  getSettings: () => get('/api/settings'),
  saveSettings: (s) => post('/api/settings', s),

  getConfig: () => get('/api/config'),

  // Admin
  adminScanHistory: () => get('/api/admin/scan-history'),
  adminScanStart:   (tf, universe, minStoreScore = 5) => post(`/api/admin/scan-start?tf=${tf}&universe=${universe}&min_store_score=${minStoreScore}`),

  // Stock Stat — bulk per-bar signal CSV
  stockStatTrigger: (tf = '1d', universe = 'sp500', bars = 60) =>
    post(`/api/stock-stat/trigger?tf=${tf}&universe=${universe}&bars=${bars}`),
  stockStatStatus: () =>
    get('/api/stock-stat/status'),
  stockStatDownloadUrl: () => BASE + '/api/stock-stat/download',

  // ULTRA — read-only signal aggregation orchestrator (no new score)
  // Trigger an orchestrated ULTRA scan (Turbo + TZ/WLNBB stock_stat + enrichments)
  ultraScanTrigger: (tf = '1d', universe = 'sp500', opts = {}) => {
    const p = new URLSearchParams({ tf, universe })
    if (opts.lookbackN     != null) p.set('lookback_n',      opts.lookbackN)
    if (opts.partialDay    != null) p.set('partial_day',     opts.partialDay)
    if (opts.minVolume     != null) p.set('min_volume',      opts.minVolume)
    if (opts.minStoreScore != null) p.set('min_store_score', opts.minStoreScore)
    if (opts.nasdaqBatch)           p.set('nasdaq_batch',    opts.nasdaqBatch)
    if (opts.stockStatBars != null) p.set('stock_stat_bars', opts.stockStatBars)
    if (opts.minPrice      != null) p.set('min_price',       opts.minPrice)
    if (opts.maxPrice      != null) p.set('max_price',       opts.maxPrice)
    if (opts.maxWorkers    != null) p.set('max_workers',     opts.maxWorkers)
    return post(`/api/ultra-scan/trigger?${p}`)
  },
  ultraScanStatus: () => get('/api/ultra-scan/status'),
  ultraScanResults: (universe = 'sp500', tf = '1d', nasdaq_batch = '') => {
    const p = new URLSearchParams({ universe, tf })
    if (nasdaq_batch) p.set('nasdaq_batch', nasdaq_batch)
    return get(`/api/ultra-scan/results?${p}`)
  },
  // Stage 2 — enrich a chosen subset of tickers. POSTs JSON body.
  ultraScanEnrich: ({ universe = 'sp500', tf = '1d', nasdaq_batch = '',
                       tickers = [], direction = 'all',
                       minPrice, maxPrice, minVolume,
                       stockStatBars, maxWorkers } = {}) => {
    const body = { universe, tf, nasdaq_batch, tickers, direction }
    if (minPrice      != null) body.min_price        = minPrice
    if (maxPrice      != null) body.max_price        = maxPrice
    if (minVolume     != null) body.min_volume       = minVolume
    if (stockStatBars != null) body.stock_stat_bars  = stockStatBars
    if (maxWorkers    != null) body.max_workers      = maxWorkers
    return post('/api/ultra-scan/enrich', body)
  },
  // Backwards-compat: same payload as /results
  ultraScan: (params) => get(`/api/ultra-scan?${new URLSearchParams(params)}`),

  // Sequence Scan — universe-wide N-bar T/Z sequence analyzer
  sequenceScanTrigger: (params = {}) => {
    const p = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v != null && v !== '') p.set(k, v)
    }
    return post(`/api/sequence-scan/trigger?${p}`)
  },
  sequenceScanStatus:  (params = {}) => {
    const p = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v != null && v !== '') p.set(k, v)
    }
    return get(`/api/sequence-scan/status?${p}`)
  },
  sequenceScanResults: (params = {}) => {
    const p = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v != null && v !== '') p.set(k, v)
    }
    return get(`/api/sequence-scan/results?${p}`)
  },

  // Sector Analysis
  sectorOverview: ()               => get('/api/sectors/overview'),
  sectorDetail:   (etf)            => get(`/api/sectors/${etf}`),
  sectorRRG:      (trail = 12)     => get(`/api/sectors/rrg?trail=${trail}`),
  sectorHeatmap:  (metric = 'return_1d') => get(`/api/sectors/heatmap?metric=${metric}`),
  sectorMacro:    ()               => get('/api/sectors/macro'),

  // Paper Portfolio
  portfolioOpen:       ()           => get('/portfolio/open'),
  portfolioStats:      (days = 90)  => get(`/portfolio/stats?days=${days}`),
  portfolioList:       (params = {}) => {
    const p = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => v != null && p.set(k, v))
    return get(`/portfolio/?${p}`)
  },
  portfolioDailyCheck: ()           => post('/portfolio/daily-check'),
  portfolioScanAndAdd: (universe = 'sp500', tf = '1d') =>
    post(`/portfolio/scan-and-add?universe=${universe}&tf=${tf}`),
  portfolioSetEntryPrice: (prices) =>
    post('/portfolio/entry-price', prices),
}

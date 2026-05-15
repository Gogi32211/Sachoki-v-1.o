import { useEffect, useRef, useState } from 'react'
import { createChart } from 'lightweight-charts'
import { api } from '../api'

// Bucket colors for histogram pane
const BUCKET_HEX = {
  W: '#c3c0d3',
  L: '#0099ff',
  N: '#ffd000',
  B: '#e48100',
  VB: '#b02020',
}

const SIG_COLOR = {
  bull: '#22c55e',
  bear: '#ef4444',
}

export default function CandleChart({ ticker, tf, onChartReady }) {
  const containerRef = useRef(null)
  const chartRef     = useRef(null)
  const seriesRef    = useRef(null)
  const volSeriesRef = useRef(null)
  const [error, setError]       = useState(null)
  const [loading, setLoading]   = useState(false)
  const [tickerInfo, setTickerInfo] = useState(null)

  useEffect(() => {
    if (!ticker) return
    setTickerInfo(null)
    api.tickerInfo(ticker).then(setTickerInfo).catch(() => {})
  }, [ticker])

  // Init chart once
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: { background: { color: '#030712' }, textColor: '#9ca3af' },
      grid: { vertLines: { color: '#1f2937' }, horzLines: { color: '#1f2937' } },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#374151' },
      timeScale: { borderColor: '#374151', timeVisible: true },
      width: containerRef.current.clientWidth,
      height: 380,
    })

    const series = chart.addCandlestickSeries({
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    })

    // Volume histogram pane with bucket colors
    const volSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'vol',
      color: '#374151',
    })
    chart.priceScale('vol').applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    })

    chartRef.current     = chart
    seriesRef.current    = series
    volSeriesRef.current = volSeries
    onChartReady?.(chart)

    const ro = new ResizeObserver(() => {
      if (containerRef.current)
        chart.applyOptions({ width: containerRef.current.clientWidth })
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.remove()
      onChartReady?.(null)
    }
  }, [])

  // Load data when ticker/tf changes
  useEffect(() => {
    if (!seriesRef.current) return
    setError(null)
    setLoading(true)

    const bars = tf === '15m' ? 400 : ['30m', '1h'].includes(tf) ? 300 : tf === '4h' ? 200 : 150
    api.signals(ticker, tf, bars)
      .then((rows) => {
        // Intraday TFs need Unix timestamps (seconds) — not date strings —
        // because multiple bars share the same calendar date.
        const isIntraday = ['30m', '15m', '1h', '4h'].includes(tf)

        const toTime = (r) => {
          const d = r.date ?? r.Datetime ?? r.Date
          if (!d) return null
          if (isIntraday) {
            // Parse ISO/space-separated datetime → Unix seconds (UTC)
            const ms = new Date(String(d).replace(' ', 'T')).getTime()
            return isNaN(ms) ? null : Math.floor(ms / 1000)
          }
          return String(d).slice(0, 10)
        }

        const candles = rows
          .filter((r) => r.close != null && toTime(r))
          .map((r) => ({
            time:  toTime(r),
            open:  Number(r.open),
            high:  Number(r.high),
            low:   Number(r.low),
            close: Number(r.close),
          }))

        // Volume histogram with bucket colors
        const volumes = rows
          .filter((r) => r.volume != null && toTime(r))
          .map((r) => ({
            time:  toTime(r),
            value: Number(r.volume),
            color: BUCKET_HEX[r.vol_bucket] ?? '#374151',
          }))

        // Signal markers — include l_combo in label text
        const markers = rows
          .filter((r) => r.sig_id > 0 && toTime(r))
          .map((r) => {
            const combo = r.l_combo && r.l_combo !== 'NONE' ? ` [${r.l_combo}]` : ''
            return {
              time:     toTime(r),
              position: r.is_bull ? 'belowBar' : 'aboveBar',
              color:    r.is_bull ? SIG_COLOR.bull : SIG_COLOR.bear,
              shape:    r.is_bull ? 'arrowUp' : 'arrowDown',
              text:     `${r.sig_name}${combo}`,
            }
          })
          .sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0))

        seriesRef.current.setData(candles)
        seriesRef.current.setMarkers(markers)
        if (volSeriesRef.current) volSeriesRef.current.setData(volumes)
        // Reset both axes so new ticker's price range fills the pane
        chartRef.current.priceScale('right').applyOptions({ autoScale: true })
        chartRef.current.timeScale().fitContent()
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [ticker, tf])

  return (
    <div className="bg-md-surface-con rounded-xl border border-md-outline-var">
      <div className="flex items-center justify-between px-4 py-3 border-b border-md-outline-var">
        <div className="flex items-center gap-3">
          <span className="font-semibold text-sm">
            {ticker} <span className="text-md-on-surface-var font-normal">{tf}</span>
            {tickerInfo?.sector && (
              <span className="ml-2 text-xs font-normal text-md-on-surface-var bg-md-surface-high px-1.5 py-0.5 rounded">
                {tickerInfo.sector}
              </span>
            )}
          </span>
          {/* Bucket legend */}
          <div className="hidden md:flex items-center gap-1.5 text-xs text-md-on-surface-var">
            {Object.entries(BUCKET_HEX).map(([k, v]) => (
              <span key={k} className="flex items-center gap-0.5">
                <span
                  className="inline-block w-2.5 h-2.5 rounded-sm"
                  style={{ backgroundColor: v }}
                />
                <span className="font-mono">{k}</span>
              </span>
            ))}
          </div>
        </div>
        {loading && <span className="text-xs text-md-on-surface-var animate-pulse">loading…</span>}
        {error && <span className="text-xs text-red-400">{error}</span>}
      </div>
      <div ref={containerRef} className="w-full" />
    </div>
  )
}

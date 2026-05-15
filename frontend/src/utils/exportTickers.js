/**
 * Export tickers to a TradingView-compatible watchlist .txt file.
 * Yahoo Finance format → TradingView: removes the -USD/-USDT dash so
 * "BTC-USD" becomes "BTCUSD", stocks like "AAPL" stay as-is.
 */
export function exportToTV(tickers, filename = 'watchlist.txt') {
  if (!tickers || tickers.length === 0) return

  const tvList = tickers.map(t => {
    // Yahoo crypto: BTC-USD → BTCUSD, ETH-USDT → ETHUSDT
    return t.replace(/-/g, '')
  })

  const blob = new Blob([tvList.join('\n')], { type: 'text/plain' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/**
 * Export arbitrary data rows as a CSV file.
 */
export function exportCSV(rows, filename = 'export.csv') {
  if (!rows || rows.length === 0) return
  const header = Object.keys(rows[0]).join(',')
  const body   = rows.map(r =>
    Object.values(r).map(v => {
      const s = String(v ?? '')
      return s.includes(',') ? `"${s}"` : s
    }).join(',')
  ).join('\n')
  const blob = new Blob([header + '\n' + body], { type: 'text/csv' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

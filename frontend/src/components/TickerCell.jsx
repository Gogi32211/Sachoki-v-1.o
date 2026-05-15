/**
 * TickerCell — compact ticker rendering for table cells.
 * Symbol only; company moves to tooltip. Truncates at ~90px.
 */
export default function TickerCell({
  symbol,
  company,
  sector,
  onClick,
  compact = true,
  className = '',
}) {
  if (!symbol) return null
  const title = [symbol, company, sector].filter(Boolean).join(' — ')
  const base =
    'text-sm font-bold font-mono text-md-on-surface hover:text-md-primary truncate whitespace-nowrap text-left'
  const sizing = compact ? 'max-w-[90px]' : ''
  const Comp = onClick ? 'button' : 'span'
  return (
    <Comp
      onClick={onClick}
      title={title}
      className={`${base} ${sizing} ${className}`}
    >
      {symbol}
    </Comp>
  )
}

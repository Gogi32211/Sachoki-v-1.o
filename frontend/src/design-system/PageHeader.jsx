/**
 * MD3 PageHeader — consistent top-of-page header bar.
 * Usage:
 *   <PageHeader title="Scan Admin" />
 *   <PageHeader title="Signal Replay" subtitle="Historical signal backtesting" badge={<StatusChip status="running" />} actions={<Button>New Run</Button>} />
 */
export function PageHeader({
  title,
  subtitle,
  badge,
  actions,
  className = '',
}) {
  return (
    <div className={`flex items-start justify-between gap-4 ${className}`}>
      <div className="flex items-center gap-3 min-w-0">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5 flex-wrap">
            <h1 className="text-md-on-surface font-semibold text-lg leading-6 truncate">
              {title}
            </h1>
            {badge && <div className="shrink-0">{badge}</div>}
          </div>
          {subtitle && (
            <p className="text-md-on-surface-var text-xs mt-0.5">{subtitle}</p>
          )}
        </div>
      </div>
      {actions && (
        <div className="shrink-0 flex items-center gap-2">
          {actions}
        </div>
      )}
    </div>
  )
}

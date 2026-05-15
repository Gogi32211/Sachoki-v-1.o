/**
 * MD3 EmptyState — zero-data placeholder for tables and lists.
 * Usage:
 *   <EmptyState message="No scans yet" />
 *   <EmptyState icon="📊" message="No results" sub="Run a scan to get started" action={<Button>Start Scan</Button>} />
 */
export function EmptyState({
  icon = '—',
  message,
  sub,
  action,
  compact = false,
  className = '',
}) {
  return (
    <div className={[
      'flex flex-col items-center justify-center text-center',
      compact ? 'py-6 gap-1.5' : 'py-12 gap-3',
      className,
    ].join(' ')}>
      {icon && (
        <span className={`text-md-on-surface-var opacity-40 select-none ${compact ? 'text-xl' : 'text-3xl'}`}>
          {icon}
        </span>
      )}
      <p className={`text-md-on-surface-var font-medium ${compact ? 'text-xs' : 'text-sm'}`}>
        {message}
      </p>
      {sub && (
        <p className="text-md-on-surface-var opacity-60 text-xs max-w-xs">{sub}</p>
      )}
      {action && <div className="mt-1">{action}</div>}
    </div>
  )
}

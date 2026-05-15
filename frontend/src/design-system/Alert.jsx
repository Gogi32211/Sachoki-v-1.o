/**
 * MD3 Alert / Banner — inline contextual messages.
 * Usage:
 *   <Alert>Something went wrong</Alert>
 *   <Alert variant="warning">Low sample size</Alert>
 *   <Alert variant="success" icon="✓">Run completed</Alert>
 *   <Alert variant="info" dismissible onDismiss={() => setShow(false)}>Note</Alert>
 */

const VARIANTS = {
  error:   {
    container: 'bg-md-error-container border-md-error/30',
    text:      'text-md-error',
    icon:      '⚠',
  },
  warning: {
    container: 'bg-md-warning-con border-md-warning/30',
    text:      'text-md-warning',
    icon:      '⚠',
  },
  success: {
    container: 'bg-md-positive-con border-md-positive/30',
    text:      'text-md-positive',
    icon:      '✓',
  },
  info: {
    container: 'bg-md-primary-container border-md-primary/30',
    text:      'text-md-on-primary-container',
    icon:      'ℹ',
  },
}

export function Alert({
  children,
  variant = 'error',
  icon,
  dismissible = false,
  onDismiss,
  className = '',
}) {
  const v = VARIANTS[variant] ?? VARIANTS.error
  const iconChar = icon ?? v.icon

  return (
    <div className={[
      'flex items-start gap-2.5 rounded-md-md border px-3 py-2.5 text-sm',
      v.container,
      className,
    ].join(' ')}>
      <span className={`shrink-0 text-base leading-5 ${v.text}`}>{iconChar}</span>
      <span className={`flex-1 ${v.text}`}>{children}</span>
      {dismissible && (
        <button
          onClick={onDismiss}
          className={`shrink-0 text-base leading-4 opacity-60 hover:opacity-100 ${v.text}`}
        >
          ×
        </button>
      )}
    </div>
  )
}

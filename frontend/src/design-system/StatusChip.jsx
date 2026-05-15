/**
 * MD3 StatusChip — compact status indicator with semantic color.
 * Usage:
 *   <StatusChip status="running" />
 *   <StatusChip status="done" label="Complete" />
 *   <StatusChip status="error" label="Failed" />
 *   <StatusChip status="idle" />
 */

const STATUS_MAP = {
  running: {
    dot:  'bg-md-warning',
    chip: 'bg-md-warning-con text-md-warning',
    label: 'Running',
    pulse: true,
  },
  done: {
    dot:  'bg-md-positive',
    chip: 'bg-md-positive-con text-md-positive',
    label: 'Done',
    pulse: false,
  },
  success: {
    dot:  'bg-md-positive',
    chip: 'bg-md-positive-con text-md-positive',
    label: 'Success',
    pulse: false,
  },
  error: {
    dot:  'bg-md-error',
    chip: 'bg-md-error-container text-md-error',
    label: 'Error',
    pulse: false,
  },
  idle: {
    dot:  'bg-md-on-surface-var',
    chip: 'bg-md-surface-high text-md-on-surface-var',
    label: 'Idle',
    pulse: false,
  },
  pending: {
    dot:  'bg-md-primary',
    chip: 'bg-md-primary-container text-md-on-primary-container',
    label: 'Pending',
    pulse: true,
  },
}

export function StatusChip({ status = 'idle', label, className = '' }) {
  const meta = STATUS_MAP[status] ?? STATUS_MAP.idle
  const text = label ?? meta.label

  return (
    <span className={[
      'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md-full text-xs font-medium',
      meta.chip,
      className,
    ].join(' ')}>
      <span className={[
        'w-1.5 h-1.5 rounded-full shrink-0',
        meta.dot,
        meta.pulse ? 'animate-pulse' : '',
      ].join(' ')} />
      {text}
    </span>
  )
}

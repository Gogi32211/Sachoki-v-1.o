/**
 * MD3 Chip — filter, assist, and suggestion variants.
 * Usage:
 *   <FilterChip label="T/Z Only" selected={sel} onToggle={setSel} />
 *   <AssistChip label="Export" icon="⬇" onClick={fn} />
 *   <ChipGroup chips={['3d','5d','10d']} value={v} onChange={setV} />
 */

export function FilterChip({ label, selected, onToggle, disabled = false, className = '' }) {
  return (
    <button
      onClick={() => !disabled && onToggle(!selected)}
      disabled={disabled}
      className={[
        'inline-flex items-center gap-1.5 h-8 px-3 rounded-md-sm text-sm font-medium',
        'border transition-all duration-100 select-none',
        'disabled:opacity-40 disabled:cursor-not-allowed',
        selected
          ? 'bg-md-primary-container border-md-primary-container text-md-on-primary-container'
          : 'bg-transparent border-md-outline text-md-on-surface-var hover:border-md-primary hover:text-md-on-surface hover:bg-white/5',
        className,
      ].join(' ')}
    >
      {selected && <span className="text-xs leading-none">✓</span>}
      {label}
    </button>
  )
}

export function AssistChip({ label, icon, onClick, disabled = false, className = '' }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={[
        'inline-flex items-center gap-1.5 h-8 px-3 rounded-md-sm text-sm font-medium',
        'border border-md-outline-var bg-md-surface-con text-md-on-surface',
        'hover:bg-white/5 hover:border-md-outline transition-all duration-100',
        'disabled:opacity-40 disabled:cursor-not-allowed',
        className,
      ].join(' ')}
    >
      {icon && <span className="text-base leading-none">{icon}</span>}
      {label}
    </button>
  )
}

/**
 * Compact tab-style chip group (like horizon selector).
 * value: currently selected value
 * options: array of strings or {value, label} objects
 */
export function ChipGroup({ value, onChange, options = [], className = '' }) {
  return (
    <div className={`flex flex-wrap gap-1.5 ${className}`}>
      {options.map(o => {
        const val   = typeof o === 'string' ? o : o.value
        const label = typeof o === 'string' ? o : o.label
        return (
          <FilterChip
            key={val}
            label={label}
            selected={val === value}
            onToggle={() => onChange(val)}
          />
        )
      })}
    </div>
  )
}

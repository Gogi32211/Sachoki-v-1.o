/**
 * MD3 Input components — TextField, NumberField, SelectField, ToggleGroup
 * Usage:
 *   <TextField label="Symbol" value={v} onChange={e => setV(e.target.value)} />
 *   <NumberField label="Min N" value={n} onChange={setN} min={1} />
 *   <SelectField label="Horizon" value={h} onChange={setH} options={HORIZONS} />
 *   <ToggleGroup value={active} onChange={setActive} options={['3d','5d','10d','20d']} />
 */

const baseInput = [
  'w-full bg-md-surface-high border border-md-outline-var rounded-md-sm',
  'text-md-on-surface text-sm placeholder:text-md-on-surface-var',
  'px-3 py-2 leading-5',
  'focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary',
  'disabled:opacity-50 disabled:cursor-not-allowed',
  'transition-colors duration-100',
].join(' ')

export function TextField({
  label,
  error,
  className = '',
  containerClass = '',
  ...props
}) {
  return (
    <div className={`flex flex-col gap-1 ${containerClass}`}>
      {label && <label className="text-xs text-md-on-surface-var font-medium">{label}</label>}
      <input className={`${baseInput} ${className}`} {...props} />
      {error && <p className="text-xs text-md-error">{error}</p>}
    </div>
  )
}

export function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  className = '',
  containerClass = '',
  ...props
}) {
  return (
    <div className={`flex flex-col gap-1 ${containerClass}`}>
      {label && <label className="text-xs text-md-on-surface-var font-medium">{label}</label>}
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={e => onChange(Number(e.target.value) || 0)}
        className={`${baseInput} ${className}`}
        {...props}
      />
    </div>
  )
}

export function SelectField({
  label,
  value,
  onChange,
  options = [],
  className = '',
  containerClass = '',
  ...props
}) {
  return (
    <div className={`flex flex-col gap-1 ${containerClass}`}>
      {label && <label className="text-xs text-md-on-surface-var font-medium">{label}</label>}
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className={[
          baseInput,
          'cursor-pointer appearance-none',
          'bg-[url("data:image/svg+xml,%3Csvg%20xmlns%3D\'http%3A//www.w3.org/2000/svg\'%20viewBox%3D\'0%200%2016%2016\'%3E%3Cpath%20fill%3D\'%238B8BA0\'%20d%3D\'M8%2011L3%206h10z\'/%3E%3C/svg%3E")]',
          'bg-no-repeat bg-[right_0.5rem_center] bg-[length:1rem] pr-8',
          className,
        ].join(' ')}
        {...props}
      >
        {options.map(o =>
          typeof o === 'string'
            ? <option key={o} value={o}>{o}</option>
            : <option key={o.value} value={o.value}>{o.label}</option>
        )}
      </select>
    </div>
  )
}

/**
 * Segmented toggle — for small option sets (horizons, timeframes, modes)
 * <ToggleGroup value={h} onChange={setH} options={['3d','5d','10d','20d']} />
 */
export function ToggleGroup({ value, onChange, options = [], label, className = '' }) {
  return (
    <div className={`flex flex-col gap-1 ${className}`}>
      {label && <label className="text-xs text-md-on-surface-var font-medium">{label}</label>}
      <div className="flex rounded-md-sm overflow-hidden border border-md-outline-var bg-md-surface-con">
        {options.map((o, i) => {
          const val   = typeof o === 'string' ? o : o.value
          const label = typeof o === 'string' ? o : o.label
          const active = val === value
          return (
            <button
              key={val}
              onClick={() => onChange(val)}
              className={[
                'flex-1 text-xs px-3 py-1.5 transition-colors duration-100',
                'font-medium select-none',
                i > 0 ? 'border-l border-md-outline-var' : '',
                active
                  ? 'bg-md-primary-container text-md-on-primary-container'
                  : 'text-md-on-surface-var hover:bg-white/5',
              ].join(' ')}
            >
              {label}
            </button>
          )
        })}
      </div>
    </div>
  )
}

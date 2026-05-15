import { useState } from 'react'

const TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1wk']

const inputCls = [
  'bg-md-surface-high border border-md-outline-var rounded-md-sm',
  'text-md-on-surface text-sm px-2 py-1',
  'placeholder:text-md-on-surface-var/50',
  'focus:outline-none focus:border-md-primary transition-colors',
].join(' ')

export default function TickerInput({ watchlist, onAdd, onRemove, tf, onTfChange }) {
  const [input, setInput] = useState('')

  function handleAdd(e) {
    e.preventDefault()
    const t = input.trim().toUpperCase()
    if (t) { onAdd(t); setInput('') }
  }

  return (
    <div className="flex items-center gap-2">
      {/* Timeframe */}
      <select
        value={tf}
        onChange={(e) => onTfChange(e.target.value)}
        className={inputCls}
      >
        {TIMEFRAMES.map((t) => (
          <option key={t} value={t}>{t}</option>
        ))}
      </select>

      {/* Add ticker */}
      <form onSubmit={handleAdd} className="flex gap-1.5">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value.toUpperCase())}
          placeholder="Add ticker…"
          className={`${inputCls} w-28 uppercase`}
        />
        <button
          type="submit"
          className="bg-md-primary hover:opacity-90 text-md-on-primary rounded-md-sm px-3 py-1 text-sm font-semibold transition-opacity"
        >
          +
        </button>
      </form>
    </div>
  )
}

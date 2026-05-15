import { useState } from 'react'
import { exportToTV, exportCSV } from '../utils/exportTickers'

const SOURCE_COLORS = {
  'Combined':  'text-green-400',
  '260323':    'text-cyan-300',
  'T/Z Scan':  'text-blue-400',
  'Power':     'text-yellow-400',
  'Pump':      'text-purple-400',
}

export default function JournalPanel({ journal, onRemove, onUpdateNote, onSelectTicker }) {
  const [editingId, setEditingId] = useState(null)
  const [editNote,  setEditNote]  = useState('')

  const startEdit = (entry) => {
    setEditingId(entry.id)
    setEditNote(entry.note || '')
  }

  const saveEdit = () => {
    if (editingId) onUpdateNote(editingId, editNote)
    setEditingId(null)
  }

  const handleExportTV = () => {
    exportToTV(journal.map(e => e.ticker), 'journal_watchlist.txt')
  }

  const handleExportCSV = () => {
    exportCSV(journal.map(e => ({
      ticker:   e.ticker,
      source:   e.source,
      added_at: e.addedAt,
      score:    e.score ?? '',
      signals:  e.signals ?? '',
      price:    e.price ?? '',
      note:     e.note ?? '',
    })), 'journal.csv')
  }

  const fmtDate = (iso) => {
    try { return new Date(iso).toLocaleString() } catch { return iso }
  }

  return (
    <div className="bg-md-surface-con rounded-md-md border border-md-outline-var flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-md-outline-var">
        <div className="flex items-center gap-3">
          <span className="font-semibold text-sm text-white">Journal</span>
          <span className="text-xs text-md-on-surface-var">{journal.length} tickers</span>
        </div>
        {journal.length > 0 && (
          <div className="flex items-center gap-2">
            <button
              onClick={handleExportTV}
              className="text-xs px-3 py-1 bg-blue-700 hover:bg-blue-600 rounded text-white"
              title="Export ticker list for TradingView watchlist import"
            >
              Export TV
            </button>
            <button
              onClick={handleExportCSV}
              className="text-xs px-3 py-1 bg-gray-700 hover:bg-gray-600 rounded text-md-on-surface"
              title="Export full journal as CSV"
            >
              CSV
            </button>
          </div>
        )}
      </div>

      {/* Table */}
      <div className="overflow-auto flex-1">
        {journal.length === 0 ? (
          <div className="px-4 py-8 text-center text-md-on-surface-var text-xs">
            Journal is empty — click <span className="text-white">+</span> on any scan row to add tickers.
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-md-on-surface-var border-b border-md-outline-var sticky top-0 bg-md-surface-con">
                <th className="text-left px-3 py-2">Ticker</th>
                <th className="text-left px-2 py-2">Source</th>
                <th className="text-left px-2 py-2 hidden sm:table-cell">Signals</th>
                <th className="text-right px-2 py-2 hidden md:table-cell">Price</th>
                <th className="text-left px-2 py-2 hidden md:table-cell">Added</th>
                <th className="text-left px-2 py-2">Note</th>
                <th className="px-2 py-2 w-16"></th>
              </tr>
            </thead>
            <tbody>
              {[...journal].reverse().map(entry => (
                <tr key={entry.id} className="border-b border-md-outline-var/40 hover:bg-md-surface-high/30">
                  <td className="px-3 py-2">
                    <button
                      onClick={() => onSelectTicker?.(entry.ticker)}
                      className="font-semibold text-white hover:text-blue-300 transition-colors"
                    >
                      {entry.ticker}
                    </button>
                  </td>
                  <td className="px-2 py-2">
                    <span className={`font-mono ${SOURCE_COLORS[entry.source] || 'text-md-on-surface-var'}`}>
                      {entry.source}
                    </span>
                  </td>
                  <td className="px-2 py-2 text-md-on-surface-var hidden sm:table-cell max-w-[160px] truncate">
                    {entry.signals || entry.score || '—'}
                  </td>
                  <td className="text-right px-2 py-2 text-md-on-surface font-mono hidden md:table-cell">
                    {entry.price ? `$${Number(entry.price).toFixed(2)}` : '—'}
                  </td>
                  <td className="px-2 py-2 text-md-on-surface-var hidden md:table-cell whitespace-nowrap">
                    {fmtDate(entry.addedAt)}
                  </td>
                  <td className="px-2 py-1.5">
                    {editingId === entry.id ? (
                      <div className="flex gap-1">
                        <input
                          autoFocus
                          value={editNote}
                          onChange={e => setEditNote(e.target.value)}
                          onKeyDown={e => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') setEditingId(null) }}
                          className="w-full bg-md-surface-high text-md-on-surface border border-gray-600 rounded px-1.5 py-0.5 text-xs outline-none"
                          placeholder="Add note…"
                        />
                        <button onClick={saveEdit} className="text-green-400 hover:text-green-300 px-1">✓</button>
                      </div>
                    ) : (
                      <button
                        onClick={() => startEdit(entry)}
                        className="text-md-on-surface-var hover:text-md-on-surface text-left w-full truncate max-w-[120px]"
                        title="Click to edit note"
                      >
                        {entry.note || <span className="italic text-md-on-surface-var/70">add note…</span>}
                      </button>
                    )}
                  </td>
                  <td className="px-2 py-2 text-right">
                    <button
                      onClick={() => onRemove(entry.id)}
                      className="text-md-on-surface-var/70 hover:text-red-400 transition-colors px-1"
                      title="Remove from journal"
                    >
                      ✕
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

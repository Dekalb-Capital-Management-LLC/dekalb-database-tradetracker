import { useState, useEffect, useCallback, useRef } from 'react'
import type { Trade, TradeLabel } from '../types'
import { get, patch, post } from '../api/client'
import LabelBadge from '../components/LabelBadge'

const LABELS: TradeLabel[] = ['event-driven', 'hedge', 'long-term', 'short-term', 'unclassified']

function fmt$(n: number) {
  const abs = Math.abs(Number(n))
  return '$' + abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function Trades() {
  const [trades, setTrades] = useState<Trade[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Filters
  const [source, setSource] = useState<'' | 'ibkr' | 'fidelity'>('')
  const [symbol, setSymbol] = useState('')
  const [side, setSide] = useState<'' | 'BUY' | 'SELL'>('')
  const [labelFilter, setLabelFilter] = useState('')

  // Label editor
  const [editingId, setEditingId] = useState<number | null>(null)
  const [syncing, setSyncing] = useState(false)
  const popoverRef = useRef<HTMLDivElement>(null)

  const fetchTrades = useCallback(() => {
    const params = new URLSearchParams()
    if (source) params.set('source', source)
    if (symbol.trim()) params.set('symbol', symbol.trim().toUpperCase())
    if (side) params.set('side', side)
    if (labelFilter) params.set('label', labelFilter)
    params.set('limit', '500')

    setLoading(true)
    setError(null)
    get<Trade[]>(`/trades?${params}`)
      .then(setTrades)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [source, symbol, side, labelFilter])

  useEffect(() => {
    fetchTrades()
  }, [fetchTrades])

  async function syncFromIbkr() {
    setSyncing(true)
    try {
      await post('/ibkr/sync/trades')
      fetchTrades()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Sync failed'
      setError(msg)
    } finally {
      setSyncing(false)
    }
  }

  // Close popover when clicking outside
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setEditingId(null)
      }
    }
    if (editingId != null) document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [editingId])

  async function applyLabel(tradeId: number, label: TradeLabel) {
    try {
      const updated = await patch<Trade>(`/trades/${tradeId}/label`, { label })
      setTrades((prev) => prev.map((t) => (t.id === tradeId ? updated : t)))
    } catch (e: any) {
      alert('Failed to update label: ' + e.message)
    }
    setEditingId(null)
  }

  return (
    <div className="p-6 max-w-screen-xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-semibold text-white">Trade Log</h2>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-500">
            {syncing ? 'Syncing from IBKR…' : `${trades.length} trades shown`}
          </span>
          <button
            type="button"
            onClick={syncFromIbkr}
            disabled={syncing}
            className="text-xs px-2 py-1 rounded border border-gray-700 text-gray-400 hover:text-white hover:border-gray-500 disabled:opacity-50"
          >
            Sync IBKR
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2 mb-5">
        {/* Source */}
        <div className="flex bg-gray-900 border border-gray-800 rounded-lg p-1 gap-0.5 text-xs">
          {(['', 'ibkr', 'fidelity'] as const).map((s) => (
            <button
              key={s}
              onClick={() => setSource(s)}
              className={`px-3 py-1.5 rounded transition-colors ${
                source === s ? 'bg-gray-700 text-white font-medium' : 'text-gray-400 hover:text-white'
              }`}
            >
              {s === '' ? 'All' : s === 'ibkr' ? 'IBKR' : 'Fidelity'}
            </button>
          ))}
        </div>

        {/* Side */}
        <div className="flex bg-gray-900 border border-gray-800 rounded-lg p-1 gap-0.5 text-xs">
          {(['', 'BUY', 'SELL'] as const).map((s) => (
            <button
              key={s}
              onClick={() => setSide(s)}
              className={`px-3 py-1.5 rounded transition-colors ${
                side === s ? 'bg-gray-700 text-white font-medium' : 'text-gray-400 hover:text-white'
              }`}
            >
              {s === '' ? 'All' : s}
            </button>
          ))}
        </div>

        {/* Symbol */}
        <input
          type="text"
          placeholder="Symbol…"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && fetchTrades()}
          className="bg-gray-900 border border-gray-800 rounded-lg px-3 py-1.5 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 w-36"
        />

        {/* Label filter */}
        <select
          value={labelFilter}
          onChange={(e) => setLabelFilter(e.target.value)}
          className="bg-gray-900 border border-gray-800 rounded-lg px-3 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-blue-500"
        >
          <option value="">All Labels</option>
          {LABELS.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
          <option value="__none">Unlabelled</option>
        </select>
      </div>

      {error && (
        <div className="bg-red-950/50 border border-red-800/50 text-red-300 px-4 py-3 rounded-lg mb-4 text-sm">
          {error}
        </div>
      )}

      <div className="bg-gray-900 rounded-lg border border-gray-800 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-xs text-gray-500 uppercase tracking-wider">
              <th className="text-left px-4 py-3 font-medium">Date</th>
              <th className="text-left px-4 py-3 font-medium">Account</th>
              <th className="text-left px-4 py-3 font-medium">Source</th>
              <th className="text-left px-4 py-3 font-medium">Symbol</th>
              <th className="text-left px-4 py-3 font-medium">Side</th>
              <th className="text-right px-4 py-3 font-medium">Qty</th>
              <th className="text-right px-4 py-3 font-medium">Price</th>
              <th className="text-right px-4 py-3 font-medium">Commission</th>
              <th className="text-right px-4 py-3 font-medium">Net Amount</th>
              <th className="text-left px-4 py-3 font-medium">Label</th>
              <th className="text-left px-4 py-3 font-medium">Flags</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={11} className="text-center py-10 text-gray-600">
                  Loading...
                </td>
              </tr>
            )}
            {!loading && trades.length === 0 && (
              <tr>
                <td colSpan={11} className="text-center py-10 text-gray-600">
                  No trades found.
                </td>
              </tr>
            )}
            {trades.map((t) => (
              <tr
                key={t.id}
                className="border-b border-gray-800/40 hover:bg-gray-800/30 transition-colors"
              >
                <td className="px-4 py-2.5 text-gray-400 whitespace-nowrap text-xs">
                  {new Date(t.trade_date).toLocaleDateString('en-US', {
                    month: 'short',
                    day: 'numeric',
                    year: 'numeric',
                  })}
                </td>
                <td className="px-4 py-2.5 text-gray-500 text-xs">{t.account_id}</td>
                <td className="px-4 py-2.5">
                  <span
                    className={`text-xs px-1.5 py-0.5 rounded ${
                      t.source === 'ibkr'
                        ? 'bg-blue-900/50 text-blue-300'
                        : 'bg-green-900/50 text-green-300'
                    }`}
                  >
                    {t.source === 'ibkr' ? 'IB' : 'Fidelity'}
                  </span>
                </td>
                <td className="px-4 py-2.5 font-semibold text-white">{t.symbol}</td>
                <td className="px-4 py-2.5">
                  <span
                    className={`text-xs font-semibold ${
                      t.side === 'BUY' ? 'text-emerald-400' : 'text-red-400'
                    }`}
                  >
                    {t.side}
                  </span>
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums">
                  {Number(t.quantity).toLocaleString()}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums">{fmt$(Number(t.price))}</td>
                <td className="px-4 py-2.5 text-right tabular-nums text-gray-500">
                  {fmt$(Number(t.commission))}
                </td>
                <td
                  className={`px-4 py-2.5 text-right tabular-nums ${
                    Number(t.net_amount) >= 0 ? 'text-emerald-400' : 'text-red-400'
                  }`}
                >
                  {Number(t.net_amount) < 0 ? '-' : ''}
                  {fmt$(Number(t.net_amount))}
                </td>

                {/* Label cell with inline editor */}
                <td className="px-4 py-2.5">
                  <div className="relative" ref={editingId === t.id ? popoverRef : undefined}>
                    <button
                      onClick={() => setEditingId(editingId === t.id ? null : t.id)}
                      className="hover:opacity-80 transition-opacity"
                      title="Click to change label"
                    >
                      <LabelBadge label={t.label} />
                    </button>
                    {editingId === t.id && (
                      <div className="absolute z-20 left-0 top-7 bg-gray-800 border border-gray-700 rounded-lg shadow-xl p-1.5 min-w-max">
                        {LABELS.map((l) => (
                          <button
                            key={l}
                            onClick={() => applyLabel(t.id, l)}
                            className="block w-full text-left px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-700 hover:text-white rounded transition-colors"
                          >
                            {l}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </td>

                <td className="px-4 py-2.5">
                  {t.is_hedge && (
                    <span className="text-xs bg-yellow-900/50 text-yellow-400 px-1.5 py-0.5 rounded">
                      Hedge
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

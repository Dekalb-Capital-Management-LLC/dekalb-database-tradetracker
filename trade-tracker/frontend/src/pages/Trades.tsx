import { useState, useEffect, useCallback, useRef } from 'react'
import { Search, Trash2 } from 'lucide-react'
import type { Trade, TradeLabel } from '../types'
import { del, get, patch } from '../api/client'
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

  const [source, setSource] = useState<'' | 'ibkr' | 'fidelity'>('')
  const [symbol, setSymbol] = useState('')
  const [side, setSide] = useState<'' | 'BUY' | 'SELL'>('')
  const [labelFilter, setLabelFilter] = useState('')

  const [editingId, setEditingId] = useState<number | null>(null)
  const [confirmingClear, setConfirmingClear] = useState(false)
  const [clearing, setClearing] = useState(false)
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

  useEffect(() => { fetchTrades() }, [fetchTrades])

  async function clearTradeLog() {
    setClearing(true)
    try {
      await del('/trades/reset')
      setConfirmingClear(false)
      fetchTrades()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Clear failed')
    } finally {
      setClearing(false)
    }
  }

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

  /* ── pill toggle helper ── */
  function PillGroup<T extends string>({
    options,
    value,
    onChange,
    labelFn,
  }: {
    options: T[]
    value: T
    onChange: (v: T) => void
    labelFn?: (v: T) => string
  }) {
    return (
      <div
        className="flex rounded-lg p-0.5"
        style={{ backgroundColor: '#ffffff', border: '1px solid #d0dce8' }}
      >
        {options.map((o) => (
          <button
            key={o}
            onClick={() => onChange(o)}
            className="px-3 py-1.5 rounded-md text-xs font-medium transition-colors"
            style={
              value === o
                ? { backgroundColor: '#1a2744', color: '#ffffff' }
                : { color: '#6b7a99' }
            }
          >
            {labelFn ? labelFn(o) : o || 'All'}
          </button>
        ))}
      </div>
    )
  }

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold" style={{ color: '#1a2744' }}>Trade Log</h2>
        <div className="flex items-center gap-3">
          <span className="text-sm" style={{ color: '#9ca3af' }}>{trades.length} trades</span>
          {confirmingClear ? (
            <div
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs"
              style={{ backgroundColor: '#fef2f2', border: '1px solid #fecaca', color: '#991b1b' }}
            >
              <span>Clear all trades, snapshots &amp; positions?</span>
              <button
                onClick={clearTradeLog}
                disabled={clearing}
                className="font-semibold px-2.5 py-1 rounded-md transition-colors disabled:opacity-50"
                style={{ backgroundColor: '#dc2626', color: '#ffffff' }}
              >
                {clearing ? 'Clearing…' : 'Yes, clear'}
              </button>
              <button
                onClick={() => setConfirmingClear(false)}
                className="font-medium px-2.5 py-1 rounded-md border transition-colors hover:bg-white"
                style={{ borderColor: '#fecaca', color: '#991b1b' }}
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmingClear(true)}
              className="inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-lg border transition-colors hover:bg-[#fef2f2]"
              style={{ borderColor: '#fecaca', color: '#dc2626' }}
            >
              <Trash2 size={13} /> Clear trade log
            </button>
          )}
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2 mb-5 items-center">
        <PillGroup
          options={['', 'ibkr', 'fidelity'] as const}
          value={source}
          onChange={(v) => setSource(v as '' | 'ibkr' | 'fidelity')}
          labelFn={(s) => s === '' ? 'All' : s === 'ibkr' ? 'IBKR' : 'Fidelity'}
        />
        <PillGroup
          options={['', 'BUY', 'SELL'] as const}
          value={side}
          onChange={(v) => setSide(v as '' | 'BUY' | 'SELL')}
          labelFn={(s) => s || 'All Sides'}
        />

        <div
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg"
          style={{ backgroundColor: '#ffffff', border: '1px solid #d0dce8' }}
        >
          <Search size={13} color="#9ca3af" />
          <input
            type="text"
            placeholder="Symbol…"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && fetchTrades()}
            className="text-sm bg-transparent outline-none w-28"
            style={{ color: '#1a2744' }}
          />
        </div>

        <select
          value={labelFilter}
          onChange={(e) => setLabelFilter(e.target.value)}
          className="px-3 py-1.5 rounded-lg text-xs focus:outline-none"
          style={{ backgroundColor: '#ffffff', border: '1px solid #d0dce8', color: '#374151' }}
        >
          <option value="">All Labels</option>
          {LABELS.map((l) => <option key={l} value={l}>{l}</option>)}
          <option value="__none">Unlabelled</option>
        </select>
      </div>

      {error && (
        <div
          className="px-4 py-2.5 rounded-lg mb-4 text-sm"
          style={{ backgroundColor: '#fef2f2', border: '1px solid #fecaca', color: '#dc2626' }}
        >
          {error}
        </div>
      )}

      <div
        className="rounded-xl overflow-x-auto"
        style={{ backgroundColor: '#ffffff', border: '1px solid #d0dce8' }}
      >
        <table className="w-full text-sm">
          <thead>
            <tr
              className="text-xs uppercase tracking-wider"
              style={{ borderBottom: '1px solid #e8edf5', color: '#9ca3af' }}
            >
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
                <td colSpan={11} className="text-center py-10" style={{ color: '#9ca3af' }}>
                  Loading...
                </td>
              </tr>
            )}
            {!loading && trades.length === 0 && (
              <tr>
                <td colSpan={11} className="text-center py-10" style={{ color: '#9ca3af' }}>
                  No trades found.
                </td>
              </tr>
            )}
            {trades.map((t) => (
              <tr
                key={t.id}
                className="transition-colors"
                style={{ borderBottom: '1px solid #f1f5f9' }}
              >
                <td className="px-4 py-2.5 whitespace-nowrap text-xs" style={{ color: '#6b7a99' }}>
                  {new Date(t.trade_date).toLocaleDateString('en-US', {
                    month: 'short', day: 'numeric', year: 'numeric',
                  })}
                </td>
                <td className="px-4 py-2.5 text-xs" style={{ color: '#9ca3af' }}>{t.account_id}</td>
                <td className="px-4 py-2.5">
                  <span
                    className="text-xs px-1.5 py-0.5 rounded font-medium"
                    style={
                      t.source === 'ibkr'
                        ? { backgroundColor: '#eff6ff', color: '#1d4ed8' }
                        : { backgroundColor: '#f0fdf4', color: '#15803d' }
                    }
                  >
                    {t.source === 'ibkr' ? 'IB' : 'Fidelity'}
                  </span>
                </td>
                <td className="px-4 py-2.5 font-semibold" style={{ color: '#1a2744' }}>{t.symbol}</td>
                <td className="px-4 py-2.5">
                  <span
                    className="text-xs font-semibold"
                    style={{ color: t.side === 'BUY' ? '#16a34a' : '#dc2626' }}
                  >
                    {t.side}
                  </span>
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums" style={{ color: '#374151' }}>
                  {Number(t.quantity).toLocaleString()}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums" style={{ color: '#374151' }}>
                  {fmt$(Number(t.price))}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums" style={{ color: '#9ca3af' }}>
                  {fmt$(Number(t.commission))}
                </td>
                <td
                  className="px-4 py-2.5 text-right tabular-nums font-medium"
                  style={{ color: Number(t.net_amount) >= 0 ? '#16a34a' : '#dc2626' }}
                >
                  {Number(t.net_amount) < 0 ? '-' : ''}
                  {fmt$(Number(t.net_amount))}
                </td>

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
                      <div
                        className="absolute z-20 left-0 top-7 rounded-xl shadow-xl p-1.5 min-w-max"
                        style={{ backgroundColor: '#ffffff', border: '1px solid #d0dce8' }}
                      >
                        {LABELS.map((l) => (
                          <button
                            key={l}
                            onClick={() => applyLabel(t.id, l)}
                            className="block w-full text-left px-3 py-1.5 text-xs rounded-lg transition-colors hover:bg-gray-50"
                            style={{ color: '#374151' }}
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
                    <span
                      className="text-xs px-1.5 py-0.5 rounded font-medium"
                      style={{ backgroundColor: '#fefce8', color: '#a16207' }}
                    >
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

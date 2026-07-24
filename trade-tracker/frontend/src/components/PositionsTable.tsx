import { useEffect, useRef, useState } from 'react'
import type { PositionSummary, TradeLabel } from '../types'
import { patch } from '../api/client'
import LabelBadge from './LabelBadge'

// Ledger / money-market cash — not real equity tickers (do not confuse with
// Nasdaq "CASH" = Pathward). IBKR synthetic row uses "CASH"; Fidelity sweeps
// use SPAXX/FDRXX/FCASH; IBKR sometimes uses XXCASH.
const CASH_SYMBOLS = new Set(['CASH', 'XXCASH', 'SPAXX', 'FDRXX', 'FCASH'])

const LABELS: TradeLabel[] = [
  'event-driven', 'hedge', 'long-term', 'short-term', 'unclassified',
  'tech', 'energy', 'financials', 'healthcare', 'consumer', 'industrials',
]

function isCashSymbol(symbol: string): boolean {
  return CASH_SYMBOLS.has(symbol.trim().toUpperCase().replace(/\*+$/, ''))
}

function fmt$(n: number | null) {
  if (n == null) return '—'
  return '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPct(n: number | null) {
  if (n == null) return '—'
  const v = Number(n)
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

interface Props {
  positions: PositionSummary[]
  onLabelChange?: (accountId: string, symbol: string, label: TradeLabel) => void
}

export default function PositionsTable({ positions, onLabelChange }: Props) {
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [savingKey, setSavingKey] = useState<string | null>(null)
  const popoverRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setEditingKey(null)
      }
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  async function applyLabel(p: PositionSummary, label: TradeLabel) {
    const key = `${p.account_id}:${p.symbol}`
    setSavingKey(key)
    try {
      await patch('/trades/symbol-label', {
        account_id: p.account_id,
        symbol: p.symbol,
        label,
      })
      onLabelChange?.(p.account_id, p.symbol, label)
      setEditingKey(null)
    } catch (e: any) {
      alert('Failed to save label: ' + (e?.message ?? e))
    } finally {
      setSavingKey(null)
    }
  }

  if (!positions.length) {
    return <p className="text-sm py-4" style={{ color: '#9ca3af' }}>No open positions.</p>
  }

  const cashRows = positions.filter((p) => isCashSymbol(p.symbol))
  const equityRows = positions.filter((p) => !isCashSymbol(p.symbol))
  const cashTotal = cashRows.reduce((s, p) => s + (p.market_value != null ? Number(p.market_value) : 0), 0)

  // Gross = |equity MVs| + cash so % Port is vs full account value.
  const gross =
    equityRows.reduce((s, p) => s + (p.market_value != null ? Math.abs(Number(p.market_value)) : 0), 0) +
    Math.abs(cashTotal)

  return (
    <div className="overflow-x-auto">
      {cashRows.length > 0 && (
        <div
          className="mb-3 flex items-center justify-between gap-3 px-4 py-3 rounded-lg"
          style={{ backgroundColor: 'rgba(22,163,74,0.08)', border: '1px solid #bbf7d0' }}
        >
          <div>
            <p className="text-sm font-semibold" style={{ color: '#15803d' }}>
              Cash (USD)
            </p>
            <p className="text-xs mt-0.5" style={{ color: '#6b7a99' }}>
              Sitting in account — not a traded ticker
            </p>
          </div>
          <p className="text-base font-bold tabular-nums" style={{ color: '#15803d' }}>
            {fmt$(cashTotal)}
          </p>
        </div>
      )}

      {equityRows.length === 0 ? (
        <p className="text-sm py-2" style={{ color: '#9ca3af' }}>No equity positions.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr
              className="text-xs uppercase tracking-wider"
              style={{ borderBottom: '1px solid #e8edf5', color: '#9ca3af' }}
            >
              <th className="text-left py-2 pr-4 font-medium">Symbol</th>
              <th className="text-left py-2 pr-4 font-medium">Account</th>
              <th className="text-right py-2 pr-4 font-medium">Qty</th>
              <th className="text-right py-2 pr-4 font-medium">Avg Cost</th>
              <th className="text-right py-2 pr-4 font-medium">Last</th>
              <th className="text-right py-2 pr-4 font-medium">Mkt Value</th>
              <th className="text-right py-2 pr-4 font-medium">Unreal. P&L</th>
              <th className="text-right py-2 pr-4 font-medium">P&L %</th>
              <th className="text-right py-2 pr-4 font-medium">% Port</th>
              <th className="text-left py-2 font-medium">Label</th>
            </tr>
          </thead>
          <tbody>
            {equityRows.map((p, i) => {
              const pnl = p.unrealized_pnl != null ? Number(p.unrealized_pnl) : null
              const pnlPct = p.unrealized_pnl_pct != null ? Number(p.unrealized_pnl_pct) : null
              const pnlColor = pnl == null ? '#374151' : pnl >= 0 ? '#16a34a' : '#dc2626'
              const mv = p.market_value != null ? Math.abs(Number(p.market_value)) : null
              const weightPct = mv != null && gross > 0 ? (mv / gross) * 100 : null
              const key = `${p.account_id}:${p.symbol}`
              const open = editingKey === key
              const saving = savingKey === key

              return (
                <tr
                  key={`${p.account_id}-${p.symbol}-${i}`}
                  className="transition-colors"
                  style={{ borderBottom: '1px solid #f1f5f9' }}
                >
                  <td className="py-2.5 pr-4 font-semibold" style={{ color: '#1a2744' }}>
                    {p.symbol}
                  </td>
                  <td className="py-2.5 pr-4 text-xs" style={{ color: '#9ca3af' }}>{p.account_id}</td>
                  <td className="py-2.5 pr-4 text-right tabular-nums" style={{ color: '#374151' }}>
                    {Number(p.quantity).toLocaleString()}
                  </td>
                  <td className="py-2.5 pr-4 text-right tabular-nums" style={{ color: '#374151' }}>
                    {fmt$(p.avg_cost != null ? Number(p.avg_cost) : null)}
                  </td>
                  <td className="py-2.5 pr-4 text-right tabular-nums" style={{ color: '#374151' }}>
                    {fmt$(p.current_price != null ? Number(p.current_price) : null)}
                  </td>
                  <td className="py-2.5 pr-4 text-right tabular-nums" style={{ color: '#374151' }}>
                    {fmt$(p.market_value != null ? Number(p.market_value) : null)}
                  </td>
                  <td className="py-2.5 pr-4 text-right tabular-nums font-medium" style={{ color: pnlColor }}>
                    {fmt$(pnl)}
                  </td>
                  <td className="py-2.5 pr-4 text-right tabular-nums font-medium" style={{ color: pnlColor }}>
                    {fmtPct(pnlPct)}
                  </td>
                  <td className="py-2.5 pr-4 text-right tabular-nums" style={{ color: '#6b7a99' }}>
                    {weightPct == null ? '—' : `${weightPct.toFixed(1)}%`}
                  </td>
                  <td className="py-2.5">
                    <div className="relative" ref={open ? popoverRef : undefined}>
                      <button
                        type="button"
                        disabled={saving}
                        onClick={() => setEditingKey(open ? null : key)}
                        className="hover:opacity-80 transition-opacity disabled:opacity-50"
                        title="Click to change category"
                      >
                        <LabelBadge label={p.label} />
                      </button>
                      {open && (
                        <div
                          className="absolute z-20 left-0 top-7 rounded-xl shadow-xl p-1.5 min-w-max max-h-64 overflow-y-auto"
                          style={{ backgroundColor: '#ffffff', border: '1px solid #d0dce8' }}
                        >
                          {LABELS.map((l) => (
                            <button
                              key={l}
                              type="button"
                              onClick={() => applyLabel(p, l)}
                              className="block w-full text-left px-3 py-1.5 text-xs rounded-lg transition-colors hover:bg-gray-50"
                              style={{
                                color: '#374151',
                                fontWeight: p.label === l ? 600 : 400,
                              }}
                            >
                              {l}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}

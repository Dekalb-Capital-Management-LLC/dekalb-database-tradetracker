import type { PositionSummary } from '../types'
import LabelBadge from './LabelBadge'

// Matches the backend's cash-symbol allowlist (routers/portfolio.py CASH_SYMBOLS /
// fidelity_parser.py _CASH_FUND_SYMBOLS) — IBKR's cash placeholder is "XXCASH",
// Fidelity's money-market sweep is usually "SPAXX" (or FDRXX/FCASH); all of
// them should read and behave identically here.
const CASH_SYMBOLS = new Set(['CASH', 'XXCASH', 'SPAXX', 'FDRXX', 'FCASH'])

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
}

export default function PositionsTable({ positions }: Props) {
  if (!positions.length) {
    return <p className="text-sm py-4" style={{ color: '#9ca3af' }}>No open positions.</p>
  }

  // Cash always reads first — it's not a "bet" like the other holdings.
  const sorted = [...positions].sort((a, b) => {
    const aCash = isCashSymbol(a.symbol) ? 0 : 1
    const bCash = isCashSymbol(b.symbol) ? 0 : 1
    return aCash - bCash
  })

  return (
    <div className="overflow-x-auto">
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
            <th className="text-left py-2 font-medium">Label</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((p, i) => {
            const isCash = isCashSymbol(p.symbol)
            const pnl = p.unrealized_pnl != null ? Number(p.unrealized_pnl) : null
            const pnlPct = p.unrealized_pnl_pct != null ? Number(p.unrealized_pnl_pct) : null
            const pnlColor = pnl == null ? '#374151' : pnl >= 0 ? '#16a34a' : '#dc2626'

            return (
              <tr
                key={`${p.account_id}-${p.symbol}-${i}`}
                className="transition-colors"
                style={{
                  borderBottom: '1px solid #f1f5f9',
                  backgroundColor: isCash ? 'rgba(22,163,74,0.06)' : undefined,
                }}
              >
                <td className="py-2.5 pr-4 font-semibold" style={{ color: isCash ? '#16a34a' : '#1a2744' }}>
                  {isCash ? 'CASH' : p.symbol}
                  {isCash && p.symbol.toUpperCase() !== 'CASH' && (
                    <span className="ml-1.5 font-normal text-xs" style={{ color: '#9ca3af' }}>({p.symbol})</span>
                  )}
                </td>
                <td className="py-2.5 pr-4 text-xs" style={{ color: '#9ca3af' }}>{p.account_id}</td>
                <td className="py-2.5 pr-4 text-right tabular-nums" style={{ color: '#374151' }}>
                  {isCash ? '—' : Number(p.quantity).toLocaleString()}
                </td>
                <td className="py-2.5 pr-4 text-right tabular-nums" style={{ color: '#374151' }}>
                  {isCash ? '—' : fmt$(p.avg_cost != null ? Number(p.avg_cost) : null)}
                </td>
                <td className="py-2.5 pr-4 text-right tabular-nums" style={{ color: '#374151' }}>
                  {isCash ? '—' : fmt$(p.current_price != null ? Number(p.current_price) : null)}
                </td>
                <td
                  className="py-2.5 pr-4 text-right tabular-nums"
                  style={{ color: isCash ? '#16a34a' : '#374151', fontWeight: isCash ? 700 : 400 }}
                >
                  {fmt$(p.market_value != null ? Number(p.market_value) : null)}
                </td>
                <td className="py-2.5 pr-4 text-right tabular-nums font-medium" style={{ color: pnlColor }}>
                  {isCash ? '—' : fmt$(pnl)}
                </td>
                <td className="py-2.5 pr-4 text-right tabular-nums font-medium" style={{ color: pnlColor }}>
                  {isCash ? '—' : fmtPct(pnlPct)}
                </td>
                <td className="py-2.5">
                  {isCash ? <span style={{ color: '#9ca3af' }}>—</span> : <LabelBadge label={p.label} />}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

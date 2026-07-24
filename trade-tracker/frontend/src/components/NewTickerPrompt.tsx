import { useState } from 'react'
import { useAnalyst } from '../auth/AnalystContext'

/** Ask about held symbols with no stored visibility row yet. */
export default function NewTickerPrompt({ symbols }: { symbols: string[] }) {
  const { analyst, updateAnalyst } = useAnalyst()
  const [busy, setBusy] = useState(false)

  if (!analyst || symbols.length === 0) return null

  async function setVisible(sym: string, visible: boolean) {
    if (!analyst) return
    setBusy(true)
    try {
      await updateAnalyst(analyst.id, { tickers: [{ symbol: sym, visible }] })
    } finally {
      setBusy(false)
    }
  }

  async function setAll(visible: boolean) {
    if (!analyst) return
    setBusy(true)
    try {
      await updateAnalyst(analyst.id, {
        tickers: symbols.map((symbol) => ({ symbol, visible })),
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="mb-3 px-4 py-3 rounded-lg text-sm"
      style={{ backgroundColor: '#fffbeb', border: '1px solid #fde68a', color: '#92400e' }}
    >
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <p className="font-semibold mb-1">New stock in the portfolio</p>
          <p className="text-xs mb-2" style={{ color: '#a16207' }}>
            Show on your dashboard? This choice is saved.
          </p>
          <ul className="space-y-2">
            {symbols.map((sym) => (
              <li key={sym} className="flex items-center gap-2">
                <span className="font-mono font-semibold w-16">{sym}</span>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setVisible(sym, true)}
                  className="px-2.5 py-1 rounded text-xs font-semibold disabled:opacity-50"
                  style={{ backgroundColor: '#1a2744', color: '#fff' }}
                >
                  Yes
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setVisible(sym, false)}
                  className="px-2.5 py-1 rounded text-xs font-medium border disabled:opacity-50"
                  style={{ borderColor: '#fcd34d', color: '#92400e' }}
                >
                  No
                </button>
              </li>
            ))}
          </ul>
        </div>
        {symbols.length > 1 && (
          <div className="flex gap-2 shrink-0">
            <button
              type="button"
              disabled={busy}
              onClick={() => setAll(true)}
              className="px-3 py-1.5 rounded text-xs font-semibold disabled:opacity-50"
              style={{ backgroundColor: '#1a2744', color: '#fff' }}
            >
              Yes to all
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setAll(false)}
              className="px-3 py-1.5 rounded text-xs font-medium border disabled:opacity-50"
              style={{ borderColor: '#fcd34d', color: '#92400e' }}
            >
              No to all
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

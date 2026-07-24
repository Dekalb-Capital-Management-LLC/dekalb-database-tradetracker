import { useEffect, useState } from 'react'
import { useAnalyst, type ViewMode } from '../auth/AnalystContext'
import { get } from '../api/client'
import ChipToggleList from '../components/ChipToggleList'

export default function Onboarding() {
  const { analyst, categoryOptions, updateAnalyst } = useAnalyst()
  const [mode, setMode] = useState<ViewMode>(analyst?.view_mode ?? 'tickers')
  const [categories, setCategories] = useState<string[]>(analyst?.categories ?? [])
  const [selectedTickers, setSelectedTickers] = useState<string[]>(
    () => (analyst?.tickers ?? []).filter((t) => t.visible).map((t) => t.symbol),
  )
  const [bookSymbols, setBookSymbols] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    get<string[]>('/portfolio/symbols')
      .then(setBookSymbols)
      .catch(() => setBookSymbols([]))
  }, [])

  async function finish() {
    if (!analyst) return
    setBusy(true)
    setError(null)
    try {
      if (mode === 'tickers') {
        const want = new Set(selectedTickers)
        const tickers = bookSymbols.map((symbol) => ({
          symbol,
          visible: want.has(symbol),
        }))
        await updateAnalyst(analyst.id, {
          view_mode: 'tickers',
          tickers,
          onboarded: true,
        })
      } else {
        await updateAnalyst(analyst.id, {
          view_mode: 'categories',
          categories,
          onboarded: true,
        })
      }
    } catch (err: any) {
      setError(err.message ?? 'Could not save')
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4" style={{ backgroundColor: '#e8edf5' }}>
      <div className="w-full max-w-lg bg-white rounded-xl shadow-sm border p-8" style={{ borderColor: '#d0dce8' }}>
        <h1 className="text-xl font-semibold mb-1" style={{ color: '#1a2744' }}>
          Set up your view
        </h1>
        <p className="text-sm mb-5" style={{ color: '#6b7a99' }}>
          Choose how the main dashboard filters positions. The Trades page always shows everything.
        </p>

        <div className="flex gap-2 mb-6">
          {([
            ['tickers', 'Pick tickers'],
            ['categories', 'Pick categories'],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setMode(value)}
              className="flex-1 py-2.5 rounded-lg text-sm font-semibold border transition-colors"
              style={{
                borderColor: mode === value ? '#2563eb' : '#d0dce8',
                backgroundColor: mode === value ? '#eff6ff' : '#fff',
                color: mode === value ? '#1d4ed8' : '#374151',
              }}
            >
              {label}
            </button>
          ))}
        </div>

        {mode === 'tickers' ? (
          <section className="mb-6">
            <h2 className="text-sm font-semibold mb-1" style={{ color: '#1a2744' }}>
              Stocks to show
            </h2>
            <p className="text-xs mb-3" style={{ color: '#6b7a99' }}>
              Select what you want on the dashboard. Unselected symbols are stored as hidden.
              New tickers later will ask Follow / Skip.
            </p>
            <ChipToggleList
              options={bookSymbols}
              selected={selectedTickers}
              onChange={setSelectedTickers}
              emptyText="No symbols in the book yet"
            />
          </section>
        ) : (
          <section className="mb-6">
            <h2 className="text-sm font-semibold mb-1" style={{ color: '#1a2744' }}>
              Categories
            </h2>
            <p className="text-xs mb-3" style={{ color: '#6b7a99' }}>
              Show positions whose trade label matches. Anyone can set labels on the Trades page.
            </p>
            <ChipToggleList
              options={categoryOptions}
              selected={categories}
              onChange={setCategories}
            />
          </section>
        )}

        {error && <p className="text-xs text-red-600 mb-3">{error}</p>}

        <button
          type="button"
          onClick={finish}
          disabled={busy}
          className="w-full py-2.5 rounded-lg text-sm font-semibold disabled:opacity-50"
          style={{ backgroundColor: '#1a2744', color: '#fff' }}
        >
          {busy ? 'Saving…' : 'Continue to dashboard'}
        </button>
      </div>
    </div>
  )
}

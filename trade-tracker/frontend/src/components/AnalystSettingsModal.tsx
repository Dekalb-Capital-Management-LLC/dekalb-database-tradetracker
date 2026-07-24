import { useEffect, useState } from 'react'
import { useAnalyst, type ViewMode } from '../auth/AnalystContext'
import { get } from '../api/client'
import ChipToggleList from './ChipToggleList'
import Modal from './Modal'

interface Props {
  onClose: () => void
}

export default function AnalystSettingsModal({ onClose }: Props) {
  const { analyst, categoryOptions, updateAnalyst, clearAnalyst } = useAnalyst()
  const [mode, setMode] = useState<ViewMode>(analyst?.view_mode ?? 'tickers')
  const [categories, setCategories] = useState<string[]>(analyst?.categories ?? [])
  const [selectedTickers, setSelectedTickers] = useState<string[]>(
    () => (analyst?.tickers ?? []).filter((t) => t.visible).map((t) => t.symbol),
  )
  const [bookSymbols, setBookSymbols] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    get<string[]>('/portfolio/symbols')
      .then(setBookSymbols)
      .catch(() => setBookSymbols([]))
  }, [])

  const tickerOptions = [...new Set([
    ...bookSymbols,
    ...(analyst?.tickers ?? []).map((t) => t.symbol),
  ])].sort()

  async function save() {
    if (!analyst) return
    setBusy(true)
    setError(null)
    setMsg(null)
    try {
      if (mode === 'tickers') {
        const want = new Set(selectedTickers)
        const tickers = tickerOptions.map((symbol) => ({
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
      setMsg('Preferences saved')
    } catch (err: any) {
      setError(err.message ?? 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal onClose={onClose}>
      <div className="space-y-5 max-h-[70vh] overflow-y-auto pr-1">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold" style={{ color: '#1a2744' }}>Settings</h2>
          <button type="button" onClick={onClose} className="text-sm" style={{ color: '#9ca3af' }}>Close</button>
        </div>

        <section>
          <h3 className="text-sm font-semibold mb-1" style={{ color: '#1a2744' }}>
            Viewing as {analyst?.display_name}
          </h3>
          <button
            type="button"
            onClick={() => { clearAnalyst(); onClose() }}
            className="text-xs underline"
            style={{ color: '#6b7a99' }}
          >
            Switch analyst
          </button>
        </section>

        <div className="flex gap-2">
          {([
            ['tickers', 'Tickers'],
            ['categories', 'Categories'],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setMode(value)}
              className="flex-1 py-2 rounded-lg text-sm font-semibold border"
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
          <section>
            <h3 className="text-sm font-semibold mb-2" style={{ color: '#1a2744' }}>Visible tickers</h3>
            <p className="text-xs mb-3" style={{ color: '#6b7a99' }}>
              Selected = show on dashboard. Unselected = stored as hidden.
            </p>
            <ChipToggleList options={tickerOptions} selected={selectedTickers} onChange={setSelectedTickers} />
          </section>
        ) : (
          <section>
            <h3 className="text-sm font-semibold mb-2" style={{ color: '#1a2744' }}>Categories</h3>
            <ChipToggleList options={categoryOptions} selected={categories} onChange={setCategories} />
          </section>
        )}

        <button
          type="button"
          onClick={save}
          disabled={busy}
          className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50"
          style={{ backgroundColor: '#1a2744', color: '#fff' }}
        >
          Save preferences
        </button>

        {msg && <p className="text-xs" style={{ color: '#16a34a' }}>{msg}</p>}
        {error && <p className="text-xs text-red-600">{error}</p>}
      </div>
    </Modal>
  )
}

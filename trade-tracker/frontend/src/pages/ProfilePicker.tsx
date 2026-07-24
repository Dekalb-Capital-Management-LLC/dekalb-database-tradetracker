import { useState } from 'react'
import { useAnalyst } from '../auth/AnalystContext'

export default function ProfilePicker() {
  const { analysts, selectAnalyst, createAnalyst } = useAnalyst()
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setBusy(true)
    setError(null)
    try {
      await createAnalyst(name.trim())
    } catch (err: any) {
      setError(err.message ?? 'Could not create analyst')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4" style={{ backgroundColor: '#e8edf5' }}>
      <div className="w-full max-w-md bg-white rounded-xl shadow-sm border p-8" style={{ borderColor: '#d0dce8' }}>
        <p className="text-xs font-bold tracking-widest uppercase mb-1" style={{ color: '#2563eb' }}>
          DeKalb Capital Management
        </p>
        <h1 className="text-xl font-semibold mb-1" style={{ color: '#1a2744' }}>Who are you?</h1>
        <p className="text-sm mb-6" style={{ color: '#6b7a99' }}>
          Shared login — pick your analyst profile or create one.
        </p>

        {analysts.length > 0 && (
          <ul className="space-y-2 mb-6">
            {analysts.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  onClick={() => selectAnalyst(a.id)}
                  className="w-full text-left px-4 py-3 rounded-lg border text-sm font-medium hover:bg-gray-50 transition-colors"
                  style={{ borderColor: '#d0dce8', color: '#1a2744' }}
                >
                  {a.display_name}
                </button>
              </li>
            ))}
          </ul>
        )}

        {!creating ? (
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="w-full py-2.5 rounded-lg text-sm font-semibold"
            style={{ backgroundColor: '#1a2744', color: '#fff' }}
          >
            Create new analyst
          </button>
        ) : (
          <form onSubmit={handleCreate} className="space-y-3">
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Display name"
              className="w-full px-3 py-2 rounded-lg border text-sm"
              style={{ borderColor: '#d0dce8' }}
            />
            {error && <p className="text-xs text-red-600">{error}</p>}
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={busy || !name.trim()}
                className="flex-1 py-2 rounded-lg text-sm font-semibold disabled:opacity-50"
                style={{ backgroundColor: '#1a2744', color: '#fff' }}
              >
                {busy ? 'Creating…' : 'Create'}
              </button>
              <button
                type="button"
                onClick={() => { setCreating(false); setError(null) }}
                className="px-3 py-2 rounded-lg text-sm border"
                style={{ borderColor: '#d0dce8', color: '#6b7a99' }}
              >
                Cancel
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}

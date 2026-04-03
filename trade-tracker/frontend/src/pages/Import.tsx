import { useState, useEffect } from 'react'
import { Upload } from 'lucide-react'
import type { FidelityImport } from '../types'
import { get, postForm } from '../api/client'

type ImportSource = 'fidelity' | 'ibkr'

interface UploadState {
  file: File | null
  accountId: string
  uploading: boolean
  result: FidelityImport | null
  error: string | null
  drag: boolean
}

const defaultState = (): UploadState => ({
  file: null,
  accountId: '',
  uploading: false,
  result: null,
  error: null,
  drag: false,
})

export default function Import() {
  const [fidelity, setFidelity] = useState<UploadState>(defaultState())
  const [ibkr, setIBKR] = useState<UploadState>(defaultState())
  const [imports, setImports] = useState<FidelityImport[]>([])

  useEffect(() => {
    get<FidelityImport[]>('/import/history').then(setImports).catch(() => {})
  }, [])

  async function handleUpload(source: ImportSource) {
    const state = source === 'fidelity' ? fidelity : ibkr
    const setState = source === 'fidelity' ? setFidelity : setIBKR
    const endpoint = source === 'fidelity' ? '/import/fidelity' : '/import/ibkr'

    if (!state.file) { setState(s => ({ ...s, error: 'Select a CSV or XLSX file.' })); return }
    if (!state.accountId.trim()) { setState(s => ({ ...s, error: 'Enter an account ID.' })); return }

    const form = new FormData()
    form.append('file', state.file)
    form.append('account_id', state.accountId.trim())

    setState(s => ({ ...s, uploading: true, error: null, result: null }))

    try {
      const res = await postForm<FidelityImport>(endpoint, form)
      setState(s => ({ ...s, result: res, file: null, uploading: false }))
      const updated = await get<FidelityImport[]>('/import/history')
      setImports(updated)
    } catch (e: any) {
      setState(s => ({ ...s, error: e.message, uploading: false }))
    }
  }

  function makeDropHandlers(setState: React.Dispatch<React.SetStateAction<UploadState>>) {
    return {
      onDragOver: (e: React.DragEvent) => { e.preventDefault(); setState(s => ({ ...s, drag: true })) },
      onDragLeave: () => setState(s => ({ ...s, drag: false })),
      onDrop: (e: React.DragEvent) => {
        e.preventDefault()
        setState(s => ({ ...s, drag: false }))
        const dropped = e.dataTransfer.files[0]
        const n = dropped?.name.toLowerCase() ?? ''
        if (n.endsWith('.csv') || n.endsWith('.xlsx')) {
          setState(s => ({ ...s, file: dropped, error: null }))
        } else {
          setState(s => ({ ...s, error: 'Please drop a .csv or .xlsx file.' }))
        }
      },
    }
  }

  function UploadCard({
    source,
    state,
    setState,
    title,
    instructions,
    accountPlaceholder,
    inputId,
  }: {
    source: ImportSource
    state: UploadState
    setState: React.Dispatch<React.SetStateAction<UploadState>>
    title: string
    instructions: React.ReactNode
    accountPlaceholder: string
    inputId: string
  }) {
    const dropHandlers = makeDropHandlers(setState)
    return (
      <div
        className="rounded-xl p-6 mb-5"
        style={{ backgroundColor: '#ffffff', border: '1px solid #d0dce8' }}
      >
        <h3 className="text-sm font-semibold mb-1" style={{ color: '#1a2744' }}>{title}</h3>
        <div className="text-xs mb-5" style={{ color: '#9ca3af' }}>{instructions}</div>

        <label className="block text-xs font-medium mb-1" style={{ color: '#6b7a99' }}>Account ID</label>
        <input
          type="text"
          placeholder={accountPlaceholder}
          value={state.accountId}
          onChange={e => setState(s => ({ ...s, accountId: e.target.value }))}
          className="w-full rounded-lg px-3 py-2 text-sm focus:outline-none mb-5"
          style={{
            backgroundColor: '#f8fafc',
            border: '1px solid #d0dce8',
            color: '#1a2744',
          }}
        />

        <div
          {...dropHandlers}
          onClick={() => document.getElementById(inputId)?.click()}
          className="rounded-xl p-8 text-center cursor-pointer transition-colors mb-4"
          style={{
            border: `2px dashed ${state.drag ? '#2563eb' : state.file ? '#16a34a' : '#d0dce8'}`,
            backgroundColor: state.drag ? 'rgba(37,99,235,0.04)' : state.file ? 'rgba(22,163,74,0.03)' : '#fafbfd',
          }}
        >
          <input
            id={inputId}
            type="file"
            accept=".csv,.xlsx"
            className="hidden"
            onChange={e => setState(s => ({ ...s, file: e.target.files?.[0] ?? null, error: null }))}
          />
          {state.file ? (
            <>
              <p className="font-medium text-sm" style={{ color: '#16a34a' }}>{state.file.name}</p>
              <p className="text-xs mt-1" style={{ color: '#9ca3af' }}>{(state.file.size / 1024).toFixed(1)} KB</p>
              <p className="text-xs mt-1" style={{ color: '#c0ccd8' }}>Click to change file</p>
            </>
          ) : (
            <>
              <Upload size={20} color="#c0ccd8" className="mx-auto mb-2" />
              <p className="text-sm" style={{ color: '#6b7a99' }}>Drop your CSV or XLSX here</p>
              <p className="text-xs mt-1" style={{ color: '#c0ccd8' }}>or click to browse</p>
            </>
          )}
        </div>

        {state.error && (
          <div
            className="px-3 py-2 rounded-lg text-xs mb-3"
            style={{ backgroundColor: '#fef2f2', border: '1px solid #fecaca', color: '#dc2626' }}
          >
            {state.error}
          </div>
        )}

        {state.result && (
          <div
            className="px-3 py-2 rounded-lg text-xs mb-3"
            style={
              state.result.status === 'success'
                ? { backgroundColor: '#f0fdf4', border: '1px solid #bbf7d0', color: '#15803d' }
                : state.result.status === 'partial'
                ? { backgroundColor: '#fefce8', border: '1px solid #fde68a', color: '#a16207' }
                : { backgroundColor: '#fef2f2', border: '1px solid #fecaca', color: '#dc2626' }
            }
          >
            {state.result.status === 'success'
              ? `Imported ${state.result.success_count} trades successfully.`
              : `${state.result.success_count} imported, ${state.result.error_count} failed${state.result.error_message ? `: ${state.result.error_message}` : '.'}`}
          </div>
        )}

        <button
          onClick={() => handleUpload(source)}
          disabled={state.uploading || !state.file || !state.accountId.trim()}
          className="w-full py-2.5 rounded-lg text-sm font-medium transition-colors"
          style={{
            backgroundColor: state.uploading || !state.file || !state.accountId.trim() ? '#e8edf5' : '#1a2744',
            color: state.uploading || !state.file || !state.accountId.trim() ? '#9ca3af' : '#ffffff',
          }}
        >
          {state.uploading ? 'Uploading...' : 'Upload File'}
        </button>
      </div>
    )
  }

  return (
    <div className="p-8 max-w-2xl mx-auto">
      <h2 className="text-2xl font-bold mb-1" style={{ color: '#1a2744' }}>Import Trade History</h2>
      <p className="text-sm mb-8" style={{ color: '#9ca3af' }}>
        Upload historical trades once — new IBKR fills sync automatically every hour after that.
        Duplicate trades are skipped automatically.
      </p>

      <UploadCard
        source="ibkr"
        state={ibkr}
        setState={setIBKR}
        title="IBKR History (Activity Statement)"
        instructions={
          <>
            One-time upload of your full IBKR trade history.{' '}
            In IBKR Client Portal: Performance &amp; Reports → Activity Statements →
            set date range → Format: CSV → Run → Download.
            After this, new trades sync automatically — no further uploads needed.
          </>
        }
        accountPlaceholder="e.g. IBKR_U1234567"
        inputId="ibkr-file-input"
      />

      <UploadCard
        source="fidelity"
        state={fidelity}
        setState={setFidelity}
        title="Fidelity CSV"
        instructions={
          <>
            Supports two formats (auto-detected):{' '}
            <strong style={{ color: '#6b7a99' }}>Positions snapshot</strong> — Accounts &amp; Trade → Portfolio → export CSV/XLSX.{' '}
            <strong style={{ color: '#6b7a99' }}>Activity/Orders</strong> — Portfolio → Activity &amp; Orders → Download.
          </>
        }
        accountPlaceholder="e.g. FIDELITY_MAIN or Z12345678"
        inputId="fidelity-file-input"
      />

      {/* Import history */}
      <div
        className="rounded-xl p-5"
        style={{ backgroundColor: '#ffffff', border: '1px solid #d0dce8' }}
      >
        <h3 className="text-sm font-semibold mb-4" style={{ color: '#1a2744' }}>
          Import History
          <span className="ml-2 font-normal" style={{ color: '#9ca3af' }}>({imports.length})</span>
        </h3>
        {imports.length === 0 ? (
          <p className="text-sm" style={{ color: '#9ca3af' }}>No imports yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr
                  className="text-xs uppercase tracking-wider"
                  style={{ borderBottom: '1px solid #e8edf5', color: '#9ca3af' }}
                >
                  <th className="text-left py-2 pr-4 font-medium">Date</th>
                  <th className="text-left py-2 pr-4 font-medium">File</th>
                  <th className="text-left py-2 pr-4 font-medium">Account</th>
                  <th className="text-right py-2 pr-4 font-medium">Rows</th>
                  <th className="text-left py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {imports.map((imp) => (
                  <tr key={imp.import_id} style={{ borderBottom: '1px solid #f1f5f9' }}>
                    <td className="py-2 pr-4 text-xs whitespace-nowrap" style={{ color: '#6b7a99' }}>
                      {new Date(imp.imported_at).toLocaleDateString('en-US', {
                        month: 'short', day: 'numeric', year: 'numeric',
                      })}
                    </td>
                    <td className="py-2 pr-4 text-xs max-w-xs truncate" title={imp.filename} style={{ color: '#374151' }}>
                      {imp.filename}
                    </td>
                    <td className="py-2 pr-4 text-xs" style={{ color: '#9ca3af' }}>{imp.account_id ?? '—'}</td>
                    <td className="py-2 pr-4 text-right text-xs tabular-nums" style={{ color: '#374151' }}>
                      {imp.success_count}/{imp.row_count ?? '?'}
                    </td>
                    <td className="py-2">
                      <span
                        className="text-xs px-2 py-0.5 rounded-md font-medium"
                        style={
                          imp.status === 'success'
                            ? { backgroundColor: '#f0fdf4', color: '#15803d' }
                            : imp.status === 'partial'
                            ? { backgroundColor: '#fefce8', color: '#a16207' }
                            : { backgroundColor: '#fef2f2', color: '#dc2626' }
                        }
                      >
                        {imp.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

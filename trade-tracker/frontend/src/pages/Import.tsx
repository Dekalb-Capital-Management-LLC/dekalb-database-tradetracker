import { useState, useEffect } from 'react'
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
    get<FidelityImport[]>('/import/fidelity').then(setImports).catch(() => {})
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
      const updated = await get<FidelityImport[]>('/import/fidelity')
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
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 mb-6">
        <h3 className="text-sm font-medium text-gray-200 mb-1">{title}</h3>
        <div className="text-xs text-gray-500 mb-5">{instructions}</div>

        <label className="block text-xs text-gray-500 mb-1">Account ID</label>
        <input
          type="text"
          placeholder={accountPlaceholder}
          value={state.accountId}
          onChange={e => setState(s => ({ ...s, accountId: e.target.value }))}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 mb-5"
        />

        <div
          {...dropHandlers}
          onClick={() => document.getElementById(inputId)?.click()}
          className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors mb-4 ${
            state.drag
              ? 'border-blue-500 bg-blue-950/20'
              : state.file
              ? 'border-emerald-600/70 bg-emerald-950/10'
              : 'border-gray-700 hover:border-gray-500'
          }`}
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
              <p className="text-emerald-400 font-medium text-sm">{state.file.name}</p>
              <p className="text-xs text-gray-500 mt-1">{(state.file.size / 1024).toFixed(1)} KB</p>
              <p className="text-xs text-gray-600 mt-1">Click to change file</p>
            </>
          ) : (
            <>
              <p className="text-gray-400 text-sm">Drop your CSV or XLSX here</p>
              <p className="text-xs text-gray-600 mt-1">or click to browse</p>
            </>
          )}
        </div>

        {state.error && (
          <div className="bg-red-950/50 border border-red-800/50 text-red-300 px-3 py-2 rounded-lg text-xs mb-3">
            {state.error}
          </div>
        )}

        {state.result && (
          <div
            className={`px-3 py-2 rounded-lg text-xs mb-3 border ${
              state.result.status === 'success'
                ? 'bg-emerald-950/50 border-emerald-800/50 text-emerald-300'
                : state.result.status === 'partial'
                ? 'bg-yellow-950/50 border-yellow-800/50 text-yellow-300'
                : 'bg-red-950/50 border-red-800/50 text-red-300'
            }`}
          >
            {state.result.status === 'success'
              ? `Imported ${state.result.success_count} trades successfully.`
              : `${state.result.success_count} imported, ${state.result.error_count} failed${state.result.error_message ? `: ${state.result.error_message}` : '.'}`}
          </div>
        )}

        <button
          onClick={() => handleUpload(source)}
          disabled={state.uploading || !state.file || !state.accountId.trim()}
          className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-800 disabled:text-gray-600 text-white rounded-lg text-sm font-medium transition-colors"
        >
          {state.uploading ? 'Uploading...' : 'Upload CSV'}
        </button>
      </div>
    )
  }

  return (
    <div className="p-6 max-w-2xl mx-auto">
      <h2 className="text-xl font-semibold text-white mb-2">Import Trade History</h2>
      <p className="text-xs text-gray-500 mb-8">
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
            <span className="text-gray-400">
              In IBKR Client Portal: Performance &amp; Reports → Activity Statements →
              set date range → Format: CSV → Run → Download
            </span>
            . After this, new trades sync automatically — no further uploads needed.
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
            Supports two Fidelity export formats (auto-detected):{' '}
            <span className="text-gray-400">
              <strong className="text-gray-300">Positions snapshot</strong> — Accounts &amp; Trade → Portfolio → export positions CSV/XLSX.{' '}
              <strong className="text-gray-300">Activity/Orders</strong> — Portfolio → Activity &amp; Orders tab → Download.
            </span>{' '}
            Both CSV and XLSX accepted.
          </>
        }
        accountPlaceholder="e.g. FIDELITY_MAIN or Z12345678"
        inputId="fidelity-file-input"
      />

      {/* Import history */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
        <h3 className="text-sm font-medium text-gray-200 mb-4">
          Import History
          <span className="ml-2 text-gray-600 font-normal">({imports.length})</span>
        </h3>
        {imports.length === 0 ? (
          <p className="text-gray-600 text-sm">No imports yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-xs text-gray-500 uppercase tracking-wider">
                  <th className="text-left py-2 pr-4 font-medium">Date</th>
                  <th className="text-left py-2 pr-4 font-medium">File</th>
                  <th className="text-left py-2 pr-4 font-medium">Account</th>
                  <th className="text-right py-2 pr-4 font-medium">Rows</th>
                  <th className="text-left py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {imports.map((imp) => (
                  <tr key={imp.import_id} className="border-b border-gray-800/40">
                    <td className="py-2 pr-4 text-gray-500 text-xs whitespace-nowrap">
                      {new Date(imp.imported_at).toLocaleDateString('en-US', {
                        month: 'short', day: 'numeric', year: 'numeric',
                      })}
                    </td>
                    <td
                      className="py-2 pr-4 text-gray-300 text-xs max-w-xs truncate"
                      title={imp.filename}
                    >
                      {imp.filename}
                    </td>
                    <td className="py-2 pr-4 text-gray-500 text-xs">{imp.account_id ?? '—'}</td>
                    <td className="py-2 pr-4 text-right text-gray-400 text-xs tabular-nums">
                      {imp.success_count}/{imp.row_count ?? '?'}
                    </td>
                    <td className="py-2">
                      <span
                        className={`text-xs px-2 py-0.5 rounded ${
                          imp.status === 'success'
                            ? 'bg-emerald-900/50 text-emerald-400'
                            : imp.status === 'partial'
                            ? 'bg-yellow-900/50 text-yellow-400'
                            : 'bg-red-900/50 text-red-400'
                        }`}
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

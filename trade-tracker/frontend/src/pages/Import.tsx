import { useState, useEffect } from 'react'
import { Upload, CheckCircle, AlertCircle } from 'lucide-react'
import type { FidelityImport } from '../types'
import { get, postForm } from '../api/client'

interface UploadState {
  file: File | null
  uploading: boolean
  result: FidelityImport | null
  error: string | null
  drag: boolean
}

export default function Import() {
  const [state, setState] = useState<UploadState>({
    file: null, uploading: false, result: null, error: null, drag: false,
  })
  const [imports, setImports] = useState<FidelityImport[]>([])

  useEffect(() => {
    get<FidelityImport[]>('/import/history').then(setImports).catch(() => {})
  }, [])

  async function handleUpload() {
    if (!state.file) return
    const form = new FormData()
    form.append('file', state.file)
    setState(s => ({ ...s, uploading: true, error: null, result: null }))
    try {
      const res = await postForm<FidelityImport>('/import/trades', form)
      setState(s => ({ ...s, result: res, file: null, uploading: false }))
      get<FidelityImport[]>('/import/history').then(setImports).catch(() => {})
    } catch (e: any) {
      setState(s => ({ ...s, error: e.message ?? 'Upload failed', uploading: false }))
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault()
    setState(s => ({ ...s, drag: false }))
    const f = e.dataTransfer.files[0]
    if (!f) return
    const ok = /\.(csv|xlsx|tsv|txt)$/i.test(f.name)
    setState(s => ({ ...s, file: ok ? f : null, error: ok ? null : 'Drop a .csv, .xlsx, or .tsv file.' }))
  }

  const ok = state.result?.status === 'success'
  const partial = state.result?.status === 'partial'

  return (
    <div className="p-8 max-w-2xl mx-auto">
      <h2 className="text-2xl font-bold mb-1" style={{ color: '#1a2744' }}>Import Trades</h2>
      <p className="text-sm mb-8" style={{ color: '#9ca3af' }}>
        Drop any trade file — IBKR Activity Statement, Fidelity CSV/XLSX, or a simple
        Ticker / Date / Amount / Price spreadsheet. Format is detected automatically.
      </p>

      {/* Drop zone */}
      <div
        onDragOver={e => { e.preventDefault(); setState(s => ({ ...s, drag: true })) }}
        onDragLeave={() => setState(s => ({ ...s, drag: false }))}
        onDrop={onDrop}
        onClick={() => document.getElementById('file-input')?.click()}
        className="rounded-xl p-10 text-center cursor-pointer mb-4 transition-all"
        style={{
          border: `2px dashed ${state.drag ? '#2563eb' : state.file ? '#16a34a' : '#d0dce8'}`,
          backgroundColor: state.drag ? 'rgba(37,99,235,0.04)' : state.file ? 'rgba(22,163,74,0.03)' : '#fafbfd',
        }}
      >
        <input
          id="file-input"
          type="file"
          accept=".csv,.xlsx,.tsv,.txt"
          className="hidden"
          onChange={e => {
            const f = e.target.files?.[0] ?? null
            setState(s => ({ ...s, file: f, error: null, result: null }))
          }}
        />
        {state.file ? (
          <>
            <CheckCircle size={24} color="#16a34a" className="mx-auto mb-2" />
            <p className="font-medium text-sm" style={{ color: '#16a34a' }}>{state.file.name}</p>
            <p className="text-xs mt-1" style={{ color: '#9ca3af' }}>
              {(state.file.size / 1024).toFixed(1)} KB · click to change
            </p>
          </>
        ) : (
          <>
            <Upload size={24} color="#c0ccd8" className="mx-auto mb-2" />
            <p className="text-sm font-medium" style={{ color: '#6b7a99' }}>Drop your file here</p>
            <p className="text-xs mt-1" style={{ color: '#c0ccd8' }}>
              CSV, XLSX, or TSV · IBKR · Fidelity · custom spreadsheet
            </p>
          </>
        )}
      </div>

      {/* Status messages */}
      {state.error && (
        <div className="flex items-start gap-2 px-4 py-3 rounded-lg text-sm mb-4"
          style={{ backgroundColor: '#fef2f2', border: '1px solid #fecaca', color: '#dc2626' }}>
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          {state.error}
        </div>
      )}

      {state.result && (
        <div
          className="px-4 py-3 rounded-lg text-sm mb-4"
          style={
            ok ? { backgroundColor: '#f0fdf4', border: '1px solid #bbf7d0', color: '#15803d' }
              : partial ? { backgroundColor: '#fefce8', border: '1px solid #fde68a', color: '#a16207' }
              : { backgroundColor: '#fef2f2', border: '1px solid #fecaca', color: '#dc2626' }
          }
        >
          {ok
            ? `✓ Imported ${state.result.success_count} trades from ${state.result.account_id}. Performance graph rebuilding in the background.`
            : `${state.result.success_count} imported, ${state.result.error_count} skipped${state.result.error_message ? ` — ${state.result.error_message}` : '.'}`
          }
        </div>
      )}

      <button
        onClick={handleUpload}
        disabled={state.uploading || !state.file}
        className="w-full py-3 rounded-xl text-sm font-semibold transition-colors mb-8"
        style={{
          backgroundColor: state.uploading || !state.file ? '#e8edf5' : '#1a2744',
          color: state.uploading || !state.file ? '#9ca3af' : '#ffffff',
        }}
      >
        {state.uploading ? 'Importing…' : 'Import File'}
      </button>

      {/* Import history */}
      <div className="rounded-xl p-5" style={{ backgroundColor: '#ffffff', border: '1px solid #d0dce8' }}>
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
                <tr className="text-xs uppercase tracking-wider"
                  style={{ borderBottom: '1px solid #e8edf5', color: '#9ca3af' }}>
                  <th className="text-left py-2 pr-4 font-medium">Date</th>
                  <th className="text-left py-2 pr-4 font-medium">File</th>
                  <th className="text-left py-2 pr-4 font-medium">Account</th>
                  <th className="text-right py-2 pr-4 font-medium">Rows</th>
                  <th className="text-left py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {imports.map(imp => (
                  <tr key={imp.import_id} style={{ borderBottom: '1px solid #f1f5f9' }}>
                    <td className="py-2 pr-4 text-xs whitespace-nowrap" style={{ color: '#6b7a99' }}>
                      {new Date(imp.imported_at).toLocaleDateString('en-US', {
                        month: 'short', day: 'numeric', year: 'numeric',
                      })}
                    </td>
                    <td className="py-2 pr-4 text-xs max-w-xs truncate" title={imp.filename}
                      style={{ color: '#374151' }}>{imp.filename}</td>
                    <td className="py-2 pr-4 text-xs" style={{ color: '#9ca3af' }}>
                      {imp.account_id ?? '—'}
                    </td>
                    <td className="py-2 pr-4 text-right text-xs tabular-nums" style={{ color: '#374151' }}>
                      {imp.success_count}/{imp.row_count ?? '?'}
                    </td>
                    <td className="py-2">
                      <span className="text-xs px-2 py-0.5 rounded-md font-medium"
                        style={
                          imp.status === 'success'
                            ? { backgroundColor: '#f0fdf4', color: '#15803d' }
                            : imp.status === 'partial'
                            ? { backgroundColor: '#fefce8', color: '#a16207' }
                            : { backgroundColor: '#fef2f2', color: '#dc2626' }
                        }>
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

import { useState, useEffect } from 'react'
import type { FidelityImport } from '../types'
import { get, postForm } from '../api/client'

export default function Import() {
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState<FidelityImport | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [imports, setImports] = useState<FidelityImport[]>([])
  const [drag, setDrag] = useState(false)

  function loadHistory() {
    get<FidelityImport[]>('/import/history').then(setImports).catch(() => {})
  }

  useEffect(() => { loadHistory() }, [])

  async function handleUpload() {
    if (!file) { setUploadError('Select an .xlsx file.'); return }

    const form = new FormData()
    form.append('file', file)

    setUploading(true)
    setUploadError(null)
    setResult(null)

    try {
      const res = await postForm<FidelityImport>('/import/trades', form)
      setResult(res)
      setFile(null)
      loadHistory()
    } catch (e: any) {
      setUploadError(e.message)
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="p-6 max-w-2xl mx-auto">
      <h2 className="text-xl font-semibold text-white mb-2">Import Portfolio</h2>
      <p className="text-xs text-gray-500 mb-6">
        Upload your .xlsx file. Each sheet should have columns: <span className="text-gray-400">Ticker | Date Acquired | Amount | Price Acquired</span>
      </p>

      {/* Upload card */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 mb-8">

        {/* Drop zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDrag(false)
            const dropped = e.dataTransfer.files[0]
            if (dropped?.name.toLowerCase().match(/\.xlsx?$/)) {
              setFile(dropped)
              setUploadError(null)
            } else {
              setUploadError('Please drop an .xlsx file.')
            }
          }}
          onClick={() => document.getElementById('xlsx-file-input')?.click()}
          className={`border-2 border-dashed rounded-lg p-10 text-center cursor-pointer transition-colors mb-4 ${
            drag
              ? 'border-blue-500 bg-blue-950/20'
              : file
              ? 'border-emerald-600/70 bg-emerald-950/10'
              : 'border-gray-700 hover:border-gray-500'
          }`}
        >
          <input
            id="xlsx-file-input"
            type="file"
            accept=".xlsx,.xlsm"
            className="hidden"
            onChange={(e) => { setFile(e.target.files?.[0] ?? null); setUploadError(null) }}
          />
          {file ? (
            <>
              <p className="text-emerald-400 font-medium text-sm">{file.name}</p>
              <p className="text-xs text-gray-500 mt-1">{(file.size / 1024).toFixed(1)} KB</p>
              <p className="text-xs text-gray-600 mt-1">Click to change file</p>
            </>
          ) : (
            <>
              <p className="text-gray-400 text-sm">Drop your portfolio .xlsx here</p>
              <p className="text-xs text-gray-600 mt-1">or click to browse</p>
            </>
          )}
        </div>

        {uploadError && (
          <div className="bg-red-950/50 border border-red-800/50 text-red-300 px-3 py-2 rounded-lg text-xs mb-3">
            {uploadError}
          </div>
        )}

        {result && (
          <div
            className={`px-3 py-2 rounded-lg text-xs mb-3 border ${
              result.status === 'success'
                ? 'bg-emerald-950/50 border-emerald-800/50 text-emerald-300'
                : result.status === 'partial'
                ? 'bg-yellow-950/50 border-yellow-800/50 text-yellow-300'
                : 'bg-red-950/50 border-red-800/50 text-red-300'
            }`}
          >
            {result.status === 'success'
              ? `✓ Imported ${result.success_count} rows. Prices will refresh automatically.`
              : `${result.success_count} imported, ${result.error_count} failed${result.error_message ? `: ${result.error_message}` : '.'}`}
          </div>
        )}

        <button
          onClick={handleUpload}
          disabled={uploading || !file}
          className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-800 disabled:text-gray-600 text-white rounded-lg text-sm font-medium transition-colors"
        >
          {uploading ? 'Uploading...' : 'Upload Portfolio'}
        </button>
      </div>

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
                    <td className="py-2 pr-4 text-gray-300 text-xs max-w-xs truncate" title={imp.filename}>
                      {imp.filename}
                    </td>
                    <td className="py-2 pr-4 text-right text-gray-400 text-xs tabular-nums">
                      {imp.success_count}/{imp.row_count ?? '?'}
                    </td>
                    <td className="py-2">
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        imp.status === 'success' ? 'bg-emerald-900/50 text-emerald-400'
                        : imp.status === 'partial' ? 'bg-yellow-900/50 text-yellow-400'
                        : 'bg-red-900/50 text-red-400'
                      }`}>
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

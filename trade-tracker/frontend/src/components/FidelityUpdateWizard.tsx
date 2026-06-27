import { useEffect, useState } from 'react'
import { Upload, ExternalLink, CheckCircle2, RefreshCw, Trash2, AlertTriangle } from 'lucide-react'
import type { ImportCommitPosition, ImportPreviewResponse, LatestImportSummary, PositionDiffRow } from '../types'
import { del, get, post, postForm } from '../api/client'
import Modal, { ModalFooter } from './Modal'

// TODO: point this at the exact Fidelity page (e.g. Positions or Portfolio
// Summary) once we know the precise URL — left generic so we don't guess.
const FIDELITY_URL = 'https://www.fidelity.com/'

type Step = 1 | 2 | 3 | 4

interface Props {
  defaultAccountId?: string
  onClose: () => void
  onComplete?: () => void
}

export default function FidelityUpdateWizard({ defaultAccountId = '', onClose, onComplete }: Props) {
  const [step, setStep] = useState<Step>(1)
  const [accountId, setAccountId] = useState(defaultAccountId)
  const [latest, setLatest] = useState<LatestImportSummary | null>(null)

  const [file, setFile] = useState<File | null>(null)
  const [drag, setDrag] = useState(false)
  const [uploading, setUploading] = useState(false)

  const [preview, setPreview] = useState<ImportPreviewResponse | null>(null)
  const [diffRows, setDiffRows] = useState<PositionDiffRow[]>([])
  const [positions, setPositions] = useState<ImportCommitPosition[]>([])
  const [committing, setCommitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const isCsv = !!file && /\.csv$/i.test(file.name)

  useEffect(() => {
    get<LatestImportSummary>('/import/latest').then(setLatest).catch(() => setLatest(null))
  }, [])

  function rowKey(r: { account_id: string; symbol: string }) {
    return `${r.account_id}::${r.symbol}`
  }

  function pickFile(f: File | null) {
    if (!f) return
    if (!/\.(csv|xlsx|xlsm)$/i.test(f.name)) {
      setError('Drop the .csv file exported from Fidelity, or your .xlsx tracking sheet.')
      return
    }
    setError(null)
    setFile(f)
  }

  async function handleUpload() {
    if (!file) return
    setUploading(true)
    setError(null)
    const form = new FormData()
    form.append('file', file)
    if (accountId.trim()) form.append('account_id', accountId.trim())
    try {
      const res = await postForm<ImportPreviewResponse>('/import/preview', form)
      setPreview(res)
      setDiffRows(res.diff)
      setPositions(res.positions)
    } catch (e: any) {
      setError(e.message ?? 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  function editQuantity(accountId: string, symbol: string, raw: string) {
    const newQty = Math.max(0, Number(raw) || 0)
    setDiffRows(rows => rows.map(r =>
      r.account_id === accountId && r.symbol === symbol
        ? { ...r, new_quantity: newQty, delta: newQty - r.old_quantity }
        : r
    ))
    setPositions(pos => {
      const row = diffRows.find(r => r.account_id === accountId && r.symbol === symbol)
      const avgCost = row?.avg_cost ?? 0
      if (newQty <= 0) return pos.filter(p => !(p.account_id === accountId && p.symbol === symbol))
      const exists = pos.some(p => p.account_id === accountId && p.symbol === symbol)
      return exists
        ? pos.map(p => (p.account_id === accountId && p.symbol === symbol ? { ...p, quantity: newQty } : p))
        : [...pos, { account_id: accountId, symbol, quantity: newQty, avg_cost: avgCost }]
    })
  }

  async function handleCommit() {
    if (!preview) return
    setCommitting(true)
    setError(null)
    try {
      await post('/import/commit', { preview_id: preview.preview_id, positions })
      setStep(4)
      onComplete?.()
      setTimeout(onClose, 1500)
    } catch (e: any) {
      setError(e.message ?? 'Commit failed')
    } finally {
      setCommitting(false)
    }
  }

  function dismissForSession() {
    onClose()
  }

  function removeDiffRow(accountId: string, symbol: string) {
    editQuantity(accountId, symbol, '0')
  }

  async function handleDeleteCached() {
    setDeleting(true)
    setError(null)
    try {
      const acct = (defaultAccountId || accountId).trim()
      const qs = acct ? `?account_id=${encodeURIComponent(acct)}` : ''
      await del(`/import/positions${qs}`)
      setLatest(l => (l ? { ...l, position_count: 0 } : l))
      setConfirmingDelete(false)
    } catch (e: any) {
      setError(e.message ?? 'Delete failed')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Modal onClose={step === 1 ? dismissForSession : undefined}>
      {step === 1 && (
        <div className="flex flex-col flex-1">
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center mb-4"
            style={{ backgroundColor: '#eff6ff' }}
          >
            <RefreshCw size={18} color="#2563eb" />
          </div>
          <h3 className="text-lg font-semibold mb-1.5" style={{ color: '#1a2744' }}>
            Would you like to update the positions?
          </h3>
          <p className="text-sm mb-4" style={{ color: '#9ca3af' }}>
            Only needed when your actual Fidelity holdings have changed.
          </p>

          <a
            href={FIDELITY_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 text-sm font-medium mb-4 px-3.5 py-2 rounded-lg transition-colors hover:bg-[#dbeafe] self-start"
            style={{ color: '#2563eb', backgroundColor: '#eff6ff', border: '1px solid #bfdbfe' }}
          >
            Open Fidelity to grab the latest export <ExternalLink size={13} />
          </a>

          <div
            className="rounded-lg px-3.5 py-3 text-sm mb-4"
            style={{ backgroundColor: '#f8fafc', border: '1px solid #eef2f7', color: '#475569' }}
          >
            {latest?.imported_at
              ? <>Last updated <span className="font-medium" style={{ color: '#1a2744' }}>
                  {new Date(latest.imported_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                </span> · {latest.position_count} position{latest.position_count === 1 ? '' : 's'} cached.</>
              : 'No Fidelity file has been imported yet — upload one to populate positions.'}
          </div>

          {!!latest?.position_count && (
            <div className="mb-2">
              {confirmingDelete ? (
                <div
                  className="rounded-lg px-3.5 py-3 text-sm flex items-start gap-2"
                  style={{ backgroundColor: '#fef2f2', border: '1px solid #fecaca', color: '#991b1b' }}
                >
                  <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                  <div className="flex-1">
                    <p className="mb-2">Delete all {latest.position_count} cached position{latest.position_count === 1 ? '' : 's'}? This can't be undone.</p>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={handleDeleteCached}
                        disabled={deleting}
                        className="text-xs font-semibold px-3 py-1.5 rounded-md transition-colors disabled:opacity-50"
                        style={{ backgroundColor: '#dc2626', color: '#ffffff' }}
                      >
                        {deleting ? 'Deleting…' : 'Yes, delete'}
                      </button>
                      <button
                        onClick={() => setConfirmingDelete(false)}
                        className="text-xs font-medium px-3 py-1.5 rounded-md border transition-colors hover:bg-white"
                        style={{ borderColor: '#fecaca', color: '#991b1b' }}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                <button
                  onClick={() => setConfirmingDelete(true)}
                  className="inline-flex items-center gap-1.5 text-xs font-medium transition-colors hover:text-[#b91c1c]"
                  style={{ color: '#9ca3af' }}
                >
                  <Trash2 size={13} /> Delete cached positions
                </button>
              )}
            </div>
          )}
          {error && <p className="text-sm mb-2" style={{ color: '#dc2626' }}>{error}</p>}

          <ModalFooter leftLabel="no" onLeft={dismissForSession} rightLabel="next" onRight={() => setStep(2)} />
        </div>
      )}

      {step === 2 && (
        <div className="flex flex-col flex-1">
          <h3 className="text-lg font-semibold mb-4" style={{ color: '#1a2744' }}>Upload your Fidelity sheet</h3>
          <label className="block text-xs font-medium mb-1" style={{ color: '#9ca3af' }}>
            Account {isCsv ? '(optional — detected from the file)' : ''}
          </label>
          <input
            type="text"
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
            placeholder={isCsv ? 'Leave blank to use accounts in the file' : 'PORTFOLIO'}
            className="w-full mb-4 px-3 py-2 rounded-lg text-sm outline-none"
            style={{ border: '1px solid #d0dce8', color: '#1a2744' }}
          />

          <div
            onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); pickFile(e.dataTransfer.files[0] ?? null) }}
            onClick={() => document.getElementById('fidelity-wizard-file')?.click()}
            className="rounded-xl p-6 text-center cursor-pointer mb-2"
            style={{
              border: `2px dashed ${drag ? '#2563eb' : file ? '#16a34a' : '#d0dce8'}`,
              backgroundColor: drag ? 'rgba(37,99,235,0.04)' : file ? 'rgba(22,163,74,0.03)' : '#fafbfd',
            }}
          >
            <input
              id="fidelity-wizard-file"
              type="file"
              accept=".csv,.xlsx,.xlsm"
              className="hidden"
              onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
            />
            <Upload size={20} color={file ? '#16a34a' : '#c0ccd8'} className="mx-auto mb-1.5" />
            <p className="text-sm font-medium" style={{ color: file ? '#16a34a' : '#6b7a99' }}>
              {file ? file.name : 'Drop your Fidelity .csv (or .xlsx sheet) here'}
            </p>
          </div>

          <button
            onClick={handleUpload}
            disabled={!file || uploading}
            className="w-full py-2.5 rounded-xl text-sm font-semibold mb-1"
            style={{
              backgroundColor: !file || uploading ? '#e8edf5' : '#1a2744',
              color: !file || uploading ? '#9ca3af' : '#ffffff',
            }}
          >
            {uploading ? 'Uploading…' : 'Upload'}
          </button>
          {uploading && (
            <div className="h-1.5 rounded-full overflow-hidden mt-2" style={{ backgroundColor: '#e8edf5' }}>
              <div className="h-full rounded-full animate-pulse" style={{ width: '70%', backgroundColor: '#2563eb' }} />
            </div>
          )}
          {error && <p className="text-sm mt-2" style={{ color: '#dc2626' }}>{error}</p>}

          <ModalFooter
            leftLabel="back" onLeft={() => setStep(1)}
            rightLabel="next" onRight={() => setStep(3)}
            rightDisabled={!preview}
          />
        </div>
      )}

      {step === 3 && (
        <div className="flex flex-col flex-1">
          <h3 className="text-lg font-semibold mb-1" style={{ color: '#1a2744' }}>Preview changes &amp; make edits</h3>
          <p className="text-sm mb-4" style={{ color: '#9ca3af' }}>
            Adjust a quantity or remove a row before saving.
          </p>
          {diffRows.length === 0 ? (
            <p className="text-sm" style={{ color: '#9ca3af' }}>No changes detected vs. the cached positions.</p>
          ) : (
            <div className="max-h-72 overflow-y-auto rounded-lg" style={{ border: '1px solid #eef2f7' }}>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs uppercase sticky top-0" style={{ color: '#9ca3af', backgroundColor: '#f8fafc', borderBottom: '1px solid #eef2f7' }}>
                    <th className="text-left py-2 px-3 font-medium">Account</th>
                    <th className="text-left py-2 font-medium">Symbol</th>
                    <th className="text-right py-2 font-medium">Was</th>
                    <th className="text-right py-2 font-medium">New</th>
                    <th className="text-right py-2 font-medium">Δ</th>
                    <th className="py-2 px-2" />
                  </tr>
                </thead>
                <tbody>
                  {diffRows.map((r) => (
                    <tr key={rowKey(r)} style={{ borderBottom: '1px solid #f1f5f9' }}>
                      <td className="py-1.5 px-3 text-xs" style={{ color: '#9ca3af' }}>{r.account_id}</td>
                      <td className="py-1.5 font-medium" style={{ color: '#1a2744' }}>{r.symbol}</td>
                      <td className="py-1.5 text-right" style={{ color: '#9ca3af' }}>{r.old_quantity}</td>
                      <td className="py-1.5 text-right">
                        <input
                          type="number"
                          value={r.new_quantity}
                          onChange={(e) => editQuantity(r.account_id, r.symbol, e.target.value)}
                          className="w-20 text-right px-1.5 py-0.5 rounded outline-none"
                          style={{ border: '1px solid #d0dce8' }}
                        />
                      </td>
                      <td className="py-1.5 text-right font-medium" style={{ color: r.delta >= 0 ? '#16a34a' : '#dc2626' }}>
                        {r.delta >= 0 ? '+' : ''}{r.delta}
                      </td>
                      <td className="py-1.5 px-2 text-center">
                        <button
                          onClick={() => removeDiffRow(r.account_id, r.symbol)}
                          title="Remove this row"
                          className="transition-colors hover:text-[#dc2626]"
                          style={{ color: '#c0ccd8' }}
                        >
                          <Trash2 size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {error && <p className="text-sm mt-2" style={{ color: '#dc2626' }}>{error}</p>}
          <ModalFooter
            leftLabel="back" onLeft={() => setStep(2)}
            rightLabel={committing ? 'Saving…' : 'next'} onRight={handleCommit}
            rightDisabled={committing}
          />
        </div>
      )}

      {step === 4 && (
        <div className="flex flex-col items-center justify-center h-full text-center py-10">
          <CheckCircle2 size={40} color="#16a34a" className="mb-3" />
          <h3 className="text-2xl font-bold" style={{ color: '#1a2744' }}>All set</h3>
        </div>
      )}
    </Modal>
  )
}

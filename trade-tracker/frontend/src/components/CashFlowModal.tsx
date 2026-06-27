import { useState } from 'react'
import { ArrowDownCircle, ArrowUpCircle } from 'lucide-react'
import { post } from '../api/client'
import Modal, { ModalFooter } from './Modal'

interface Props {
  defaultAccountId: string
  onClose: () => void
  onSaved?: () => void
}

export default function CashFlowModal({ defaultAccountId, onClose, onSaved }: Props) {
  const [accountId, setAccountId] = useState(defaultAccountId)
  const [kind, setKind] = useState<'deposit' | 'withdrawal'>('deposit')
  const [flowDate, setFlowDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [amount, setAmount] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSave() {
    const value = Number(amount)
    if (!accountId.trim() || !value || value <= 0) {
      setError('Enter an account and a positive amount.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await post('/portfolio/cash-flows', {
        account_id: accountId.trim().toUpperCase(),
        flow_date: flowDate,
        flow_type: kind,
        amount: kind === 'deposit' ? value : -value,
        source: 'manual',
        notes: notes.trim() || null,
      })
      onSaved?.()
      onClose()
    } catch (e: any) {
      setError(e.message ?? 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal onClose={onClose}>
      <div className="flex flex-col flex-1">
        <h3 className="text-lg font-semibold mb-1" style={{ color: '#1a2744' }}>Update deposits / withdrawals</h3>
        <p className="text-sm mb-4" style={{ color: '#9ca3af' }}>
          Excluded from return % so adding/pulling cash doesn't look like gains or losses.
        </p>

        <div className="flex rounded-lg p-1 mb-4" style={{ backgroundColor: '#f8fafc', border: '1px solid #d0dce8' }}>
          <button
            onClick={() => setKind('deposit')}
            className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-md text-sm font-medium transition-colors"
            style={kind === 'deposit' ? { backgroundColor: '#16a34a', color: '#fff' } : { color: '#6b7a99' }}
          >
            <ArrowDownCircle size={14} /> Deposit
          </button>
          <button
            onClick={() => setKind('withdrawal')}
            className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-md text-sm font-medium transition-colors"
            style={kind === 'withdrawal' ? { backgroundColor: '#dc2626', color: '#fff' } : { color: '#6b7a99' }}
          >
            <ArrowUpCircle size={14} /> Withdrawal
          </button>
        </div>

        <label className="block text-xs font-medium mb-1" style={{ color: '#9ca3af' }}>Account</label>
        <input
          type="text"
          value={accountId}
          onChange={(e) => setAccountId(e.target.value)}
          className="w-full mb-3 px-3 py-2 rounded-lg text-sm outline-none"
          style={{ border: '1px solid #d0dce8', color: '#1a2744' }}
        />

        <div className="grid grid-cols-2 gap-3 mb-3">
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: '#9ca3af' }}>Date</label>
            <input
              type="date"
              value={flowDate}
              onChange={(e) => setFlowDate(e.target.value)}
              className="w-full px-3 py-2 rounded-lg text-sm outline-none"
              style={{ border: '1px solid #d0dce8', color: '#1a2744' }}
            />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: '#9ca3af' }}>Amount ($)</label>
            <input
              type="number"
              min="0"
              step="0.01"
              placeholder="0.00"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              className="w-full px-3 py-2 rounded-lg text-sm outline-none text-right"
              style={{ border: '1px solid #d0dce8', color: '#1a2744' }}
            />
          </div>
        </div>

        <label className="block text-xs font-medium mb-1" style={{ color: '#9ca3af' }}>Notes (optional)</label>
        <input
          type="text"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          className="w-full mb-2 px-3 py-2 rounded-lg text-sm outline-none"
          style={{ border: '1px solid #d0dce8', color: '#1a2744' }}
        />

        {error && <p className="text-sm mt-1" style={{ color: '#dc2626' }}>{error}</p>}

        <ModalFooter
          leftLabel="Cancel" onLeft={onClose}
          rightLabel={saving ? 'Saving…' : 'Save'} onRight={handleSave}
          rightDisabled={saving}
        />
      </div>
    </Modal>
  )
}

import type { ReactNode } from 'react'

interface ModalProps {
  onClose?: () => void
  children: ReactNode
}

export default function Modal({ onClose, children }: ModalProps) {
  return (
    <div
      className="fixed inset-0 flex items-center justify-center z-50 animate-fade-in p-4"
      style={{ backgroundColor: 'rgba(15,23,42,0.5)', backdropFilter: 'blur(2px)' }}
      onClick={onClose}
    >
      <div
        className="rounded-2xl p-8 animate-fade-in-up flex flex-col"
        style={{
          backgroundColor: '#ffffff',
          width: 480,
          minHeight: 320,
          boxShadow: '0 24px 60px -8px rgba(15,23,42,0.3), 0 0 0 1px rgba(15,23,42,0.04)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  )
}

export function ModalFooter({
  leftLabel,
  onLeft,
  leftVariant = 'default',
  rightLabel,
  onRight,
  rightDisabled,
}: {
  leftLabel?: string
  onLeft?: () => void
  leftVariant?: 'default' | 'danger'
  rightLabel?: string
  onRight?: () => void
  rightDisabled?: boolean
}) {
  return (
    <div
      className="flex items-center justify-between mt-auto pt-5"
      style={{ borderTop: '1px solid #f1f5f9' }}
    >
      {leftLabel ? (
        <button
          onClick={onLeft}
          className="text-sm font-medium px-4 py-2 rounded-lg border transition-colors hover:bg-[#f8fafc]"
          style={
            leftVariant === 'danger'
              ? { borderColor: '#fecaca', color: '#dc2626', backgroundColor: '#fef2f2' }
              : { borderColor: '#d0dce8', color: '#374151', backgroundColor: '#ffffff' }
          }
        >
          {leftLabel}
        </button>
      ) : <span />}
      {rightLabel ? (
        <button
          onClick={onRight}
          disabled={rightDisabled}
          className="text-sm font-semibold px-5 py-2.5 rounded-lg transition-all shadow-sm hover:shadow-md disabled:opacity-40 disabled:shadow-none disabled:cursor-not-allowed"
          style={{ backgroundColor: '#1a2744', color: '#ffffff' }}
        >
          {rightLabel}
        </button>
      ) : <span />}
    </div>
  )
}

interface MetricCardProps {
  label: string
  value: string | null
  subValue?: string | null
  /** true = green, false = red, null/undefined = white (neutral) */
  positive?: boolean | null
}

export default function MetricCard({ label, value, subValue, positive }: MetricCardProps) {
  const valueColor =
    positive == null
      ? '#e2e8f0'
      : positive
      ? '#4ade80'
      : '#f87171'

  return (
    <div
      style={{
        backgroundColor: '#0d1117',
        border: '1px solid #1a2030',
        borderRadius: 8,
        padding: '16px',
      }}
    >
      <p
        style={{
          fontSize: 10,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          color: '#64748b',
          marginBottom: 8,
          fontWeight: 500,
        }}
      >
        {label}
      </p>
      <p
        style={{
          fontSize: 22,
          fontWeight: 600,
          color: valueColor,
          fontVariantNumeric: 'tabular-nums',
          lineHeight: 1.2,
        }}
      >
        {value ?? '—'}
      </p>
      {subValue != null && (
        <p style={{ fontSize: 11, color: '#475569', marginTop: 4 }}>{subValue}</p>
      )}
    </div>
  )
}

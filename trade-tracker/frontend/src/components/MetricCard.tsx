interface MetricCardProps {
  label: string
  value: string | null
  subValue?: string | null
  /** true = green, false = red, null/undefined = default dark */
  positive?: boolean | null
}

export default function MetricCard({ label, value, subValue, positive }: MetricCardProps) {
  const valueColor =
    positive == null
      ? '#1a2744'
      : positive
      ? '#16a34a'
      : '#dc2626'

  return (
    <div
      className="transition-all duration-150 hover:shadow-md hover:-translate-y-px"
      style={{
        backgroundColor: '#ffffff',
        border: '1px solid #d0dce8',
        borderRadius: 10,
        padding: '16px 18px',
      }}
    >
      <p
        style={{
          fontSize: 11,
          letterSpacing: '0.06em',
          textTransform: 'uppercase',
          color: '#6b7a99',
          marginBottom: 6,
          fontWeight: 500,
        }}
      >
        {label}
      </p>
      <p
        style={{
          fontSize: 22,
          fontWeight: 700,
          color: valueColor,
          fontVariantNumeric: 'tabular-nums',
          lineHeight: 1.2,
        }}
      >
        {value ?? '—'}
      </p>
      {subValue != null && (
        <p style={{ fontSize: 11, color: '#9ca3af', marginTop: 4 }}>{subValue}</p>
      )}
    </div>
  )
}

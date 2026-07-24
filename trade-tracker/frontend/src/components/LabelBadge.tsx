const COLORS: Record<string, { bg: string; color: string; border: string }> = {
  'event-driven': { bg: '#f3e8ff', color: '#7c3aed', border: '#ddd6fe' },
  'hedge':        { bg: '#fefce8', color: '#a16207', border: '#fde68a' },
  'long-term':    { bg: '#eff6ff', color: '#1d4ed8', border: '#bfdbfe' },
  'short-term':   { bg: '#fff7ed', color: '#c2410c', border: '#fed7aa' },
  'unclassified': { bg: '#f8fafc', color: '#94a3b8', border: '#e2e8f0' },
  'tech':         { bg: '#ecfdf5', color: '#047857', border: '#a7f3d0' },
  'energy':       { bg: '#fffbeb', color: '#b45309', border: '#fde68a' },
  'financials':   { bg: '#eef2ff', color: '#4338ca', border: '#c7d2fe' },
  'healthcare':   { bg: '#fdf2f8', color: '#be185d', border: '#fbcfe8' },
  'consumer':     { bg: '#f0fdfa', color: '#0f766e', border: '#99f6e4' },
  'industrials':  { bg: '#f1f5f9', color: '#475569', border: '#cbd5e1' },
}

export default function LabelBadge({ label }: { label: string | null }) {
  if (!label) return <span style={{ color: '#c0ccd8', fontSize: 12 }}>—</span>
  const c = COLORS[label] ?? { bg: '#f8fafc', color: '#94a3b8', border: '#e2e8f0' }
  return (
    <span
      style={{
        display: 'inline-block',
        fontSize: 11,
        padding: '2px 8px',
        borderRadius: 6,
        backgroundColor: c.bg,
        color: c.color,
        border: `1px solid ${c.border}`,
        whiteSpace: 'nowrap',
        fontWeight: 500,
      }}
    >
      {label}
    </span>
  )
}

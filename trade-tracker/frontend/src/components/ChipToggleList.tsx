/** Multi-select chips matching dashboard styling. */
export default function ChipToggleList({
  options,
  selected,
  onChange,
  emptyText = 'None available',
}: {
  options: string[]
  selected: string[]
  onChange: (next: string[]) => void
  emptyText?: string
}) {
  if (!options.length) {
    return <p className="text-xs" style={{ color: '#9ca3af' }}>{emptyText}</p>
  }

  return (
    <div className="flex flex-wrap gap-2 max-h-52 overflow-y-auto">
      {options.map((item) => {
        const on = selected.includes(item)
        return (
          <button
            key={item}
            type="button"
            onClick={() =>
              onChange(on ? selected.filter((x) => x !== item) : [...selected, item])
            }
            className="px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors"
            style={{
              borderColor: on ? '#2563eb' : '#d0dce8',
              backgroundColor: on ? '#eff6ff' : '#fff',
              color: on ? '#1d4ed8' : '#374151',
            }}
          >
            {item}
          </button>
        )
      })}
    </div>
  )
}

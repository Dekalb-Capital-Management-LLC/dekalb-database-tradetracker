import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { get, patch, post } from '../api/client'

export type ViewMode = 'tickers' | 'categories'

export interface AnalystTicker {
  symbol: string
  visible: boolean
}

export interface Analyst {
  id: number
  display_name: string
  view_mode: ViewMode
  categories: string[]
  tickers: AnalystTicker[]
  onboarded: boolean
  created_at: string
  updated_at: string
}

export type AnalystPatch = {
  view_mode?: ViewMode
  categories?: string[]
  tickers?: AnalystTicker[]
  onboarded?: boolean
}

const ANALYST_KEY = 'dekalb_analyst_id'

const CASH_SYMBOLS = new Set(['CASH', 'XXCASH', 'SPAXX', 'FDRXX', 'FCASH'])

interface AnalystContextValue {
  analysts: Analyst[]
  analyst: Analyst | null
  loading: boolean
  categoryOptions: string[]
  refresh: () => Promise<void>
  selectAnalyst: (id: number) => void
  clearAnalyst: () => void
  createAnalyst: (display_name: string) => Promise<Analyst>
  updateAnalyst: (id: number, body: AnalystPatch) => Promise<Analyst>
}

const AnalystContext = createContext<AnalystContextValue | null>(null)

export function AnalystProvider({ children }: { children: ReactNode }) {
  const [analysts, setAnalysts] = useState<Analyst[]>([])
  const [analyst, setAnalyst] = useState<Analyst | null>(null)
  const [loading, setLoading] = useState(true)
  const [categoryOptions, setCategoryOptions] = useState<string[]>([])

  const refresh = useCallback(async () => {
    const [list, opts] = await Promise.all([
      get<Analyst[]>('/analysts'),
      get<{ options: string[] }>('/analysts/category-options').catch(() => ({ options: [] })),
    ])
    setAnalysts(list)
    setCategoryOptions(opts.options)
    const saved = localStorage.getItem(ANALYST_KEY)
    if (saved) {
      const found = list.find((a) => String(a.id) === saved) ?? null
      setAnalyst(found)
      if (!found) localStorage.removeItem(ANALYST_KEY)
    }
  }, [])

  useEffect(() => {
    refresh().catch(() => {}).finally(() => setLoading(false))
  }, [refresh])

  const selectAnalyst = (id: number) => {
    const found = analysts.find((a) => a.id === id) ?? null
    if (found) {
      localStorage.setItem(ANALYST_KEY, String(id))
      setAnalyst(found)
    }
  }

  const clearAnalyst = () => {
    localStorage.removeItem(ANALYST_KEY)
    setAnalyst(null)
  }

  const createAnalyst = async (display_name: string) => {
    const created = await post<Analyst>('/analysts', { display_name })
    await refresh()
    localStorage.setItem(ANALYST_KEY, String(created.id))
    setAnalyst(created)
    return created
  }

  const updateAnalyst = async (id: number, body: AnalystPatch) => {
    const updated = await patch<Analyst>(`/analysts/${id}`, body)
    setAnalysts((prev) => prev.map((a) => (a.id === id ? updated : a)))
    setAnalyst((cur) => (cur?.id === id ? updated : cur))
    return updated
  }

  return (
    <AnalystContext.Provider
      value={{
        analysts, analyst, loading, categoryOptions,
        refresh, selectAnalyst, clearAnalyst, createAnalyst, updateAnalyst,
      }}
    >
      {children}
    </AnalystContext.Provider>
  )
}

export function useAnalyst(): AnalystContextValue {
  const ctx = useContext(AnalystContext)
  if (!ctx) throw new Error('useAnalyst must be used within AnalystProvider')
  return ctx
}

export function isCashSymbol(symbol: string): boolean {
  return CASH_SYMBOLS.has(symbol.trim().toUpperCase().replace(/\*+$/, ''))
}

/** Dashboard position filter. Trades page should not use this. */
export function matchesDashboardView(
  symbol: string,
  label: string | null | undefined,
  a: Analyst | null | undefined,
): boolean {
  if (!a?.onboarded) return true
  if (isCashSymbol(symbol)) return true
  const sym = symbol.toUpperCase()
  if (a.view_mode === 'categories') {
    if (!a.categories.length) return true
    return !!(label && a.categories.includes(label.toLowerCase()))
  }
  // tickers mode: only explicit visible=true
  const row = a.tickers.find((t) => t.symbol === sym)
  return row?.visible === true
}

/** New held symbols with no analyst_tickers row yet (tickers mode only). */
export function unknownHeldSymbols(
  held: { symbol: string }[],
  a: Analyst | null | undefined,
): string[] {
  if (!a?.onboarded || a.view_mode !== 'tickers') return []
  const known = new Set(a.tickers.map((t) => t.symbol))
  const out: string[] = []
  for (const p of held) {
    const sym = p.symbol.toUpperCase()
    if (!sym || isCashSymbol(sym) || known.has(sym)) continue
    out.push(sym)
  }
  return [...new Set(out)]
}

import { useState, useEffect } from 'react'
import { Search } from 'lucide-react'
import type {
  AccountSummary,
  PerformancePoint,
  Period,
  PortfolioMetrics,
  PortfolioSummary,
  PositionSummary,
} from '../types'
import { get, post } from '../api/client'
import MetricCard from '../components/MetricCard'
import PerformanceChart from '../components/PerformanceChart'
import PositionsTable from '../components/PositionsTable'

const PERIODS: { value: Period; label: string }[] = [
  { value: '1m', label: '1M' },
  { value: '3m', label: '3M' },
  { value: '6m', label: '6M' },
  { value: 'ytd', label: 'YTD' },
  { value: '1y', label: '1Y' },
]

function fmt$(n: number | null | undefined) {
  if (n == null) return null
  const v = Number(n)
  const abs = Math.abs(v)
  const formatted = '$' + abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return v < 0 ? '-' + formatted : formatted
}

function fmtPct(n: number | null | undefined, showSign = true) {
  if (n == null) return null
  const v = Number(n)
  const sign = showSign && v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

function fmtNum(n: number | null | undefined, decimals = 2, suffix = '') {
  if (n == null) return null
  return `${Number(n).toFixed(decimals)}${suffix}`
}

/* ── card shell ── */
function Card({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div
      className="flex flex-col"
      style={{
        backgroundColor: '#ffffff',
        border: '1px solid #d0dce8',
        borderRadius: 12,
        overflow: 'hidden',
      }}
    >
      <div
        className="flex items-center justify-between px-5 pt-4 pb-3"
        style={{ borderBottom: '1px solid #edf2f7' }}
      >
        <h3 className="font-semibold text-sm" style={{ color: '#1a2744' }}>{title}</h3>
        {action}
      </div>
      <div className="flex-1 overflow-auto p-5">
        {children}
      </div>
    </div>
  )
}

export default function Dashboard() {
  const [period, setPeriod] = useState<Period>('ytd')
  const [selectedAccount, setSelectedAccount] = useState<string | null>(null)
  const [symbolSearch, setSymbolSearch] = useState('')

  const [summary, setSummary] = useState<PortfolioSummary | null>(null)
  const [metrics, setMetrics] = useState<PortfolioMetrics | null>(null)
  const [performance, setPerformance] = useState<PerformancePoint[]>([])
  const [loading, setLoading] = useState(true)
  const [chartLoading, setChartLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [updating, setUpdating] = useState(false)
  const [updateMsg, setUpdateMsg] = useState<string | null>(null)

  const accounts: AccountSummary[] = summary?.accounts ?? []

  const accountParam = selectedAccount ? `&account_id=${encodeURIComponent(selectedAccount)}` : ''

  function loadSummary() {
    return get<PortfolioSummary>('/portfolio/summary')
      .then(setSummary)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  async function updatePortfolio() {
    setUpdating(true)
    setUpdateMsg(null)
    try {
      const res = await post<{
        ibkr_positions: number
        yfinance_updated: number
        yfinance_total: number
        snapshot_written: boolean
        portfolio_nav: number | null
      }>('/portfolio/update-all')
      const parts: string[] = []
      if (res.ibkr_positions > 0) parts.push(`IBKR: ${res.ibkr_positions} pos`)
      parts.push(`yf: ${res.yfinance_updated}/${res.yfinance_total}`)
      parts.push(`snapshot ${res.snapshot_written ? '✓' : '✗'}`)
      if (res.portfolio_nav != null)
        parts.push(`NAV $${res.portfolio_nav.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)
      setUpdateMsg(parts.join(' · '))
      await loadSummary()
    } catch (e: any) {
      setUpdateMsg(`Error: ${e.message}`)
    } finally {
      setUpdating(false)
    }
  }

  // Load summary once on mount, auto-refresh every 5 min
  useEffect(() => {
    loadSummary()
    const id = setInterval(loadSummary, 300_000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    setChartLoading(true)
    Promise.allSettled([
      get<PortfolioMetrics>(`/portfolio/metrics?period=${period}${accountParam}`),
      get<PerformancePoint[]>(`/portfolio/performance?period=${period}${accountParam}`),
    ])
      .then(([metricsResult, perfResult]) => {
        if (metricsResult.status === 'fulfilled') setMetrics(metricsResult.value)
        else setMetrics(null)
        if (perfResult.status === 'fulfilled') setPerformance(perfResult.value)
        else setPerformance([])
      })
      .finally(() => setChartLoading(false))
  }, [period, selectedAccount])

  const activeAccount: AccountSummary | null =
    selectedAccount ? accounts.find((a) => a.account_id === selectedAccount) ?? null : null

  const equityValue = activeAccount?.equity_value ?? summary?.combined_equity_value
  const dayPnl = activeAccount?.day_pnl ?? summary?.combined_day_pnl
  const dayPnlPct = activeAccount?.day_pnl_pct ?? summary?.combined_day_pnl_pct
  const unrealizedPnl = activeAccount?.total_unrealized_pnl ?? summary?.total_unrealized_pnl
  const realizedPnl = activeAccount?.total_realized_pnl ?? summary?.total_realized_pnl

  const allPositions: PositionSummary[] = summary?.positions ?? []
  const filteredPositions = allPositions.filter((p) => {
    const matchAccount = selectedAccount ? p.account_id === selectedAccount : true
    const matchSymbol = symbolSearch.trim()
      ? p.symbol.toUpperCase().includes(symbolSearch.trim().toUpperCase())
      : true
    return matchAccount && matchSymbol
  })

  /* ── Tab bar: Overview + per-account ── */
  const tabs = [
    { key: null, label: 'Overview' },
    ...accounts.map((a) => ({ key: a.account_id, label: a.account_id })),
  ]

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h2 className="text-xl font-semibold text-white">Portfolio Overview</h2>
          {summary && (
            <p className="text-xs text-gray-500 mt-0.5">
              As of {new Date(summary.as_of).toLocaleString()}
            </p>
          )}
        </div>

        <div className="flex items-center gap-3">
          {updateMsg && <span className="text-xs text-gray-400">{updateMsg}</span>}
          <button
            onClick={updatePortfolio}
            disabled={updating}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white transition-colors"
          >
            {updating ? '⏳ Updating...' : '↻ Update Portfolio'}
          </button>
        </div>

        {/* Period selector */}
        <div className="flex bg-gray-900 border border-gray-800 rounded-lg p-1 gap-0.5">
          {PERIODS.map((p) => (
            <button
              key={p.value}
              onClick={() => setPeriod(p.value)}
              className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                period === p.value
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>

        {/* Search */}
        <div
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg"
          style={{ backgroundColor: '#ffffff', border: '1px solid #d0dce8', minWidth: 180 }}
        >
          <Search size={13} color="#9ca3af" />
          <input
            type="text"
            placeholder="Search..."
            value={symbolSearch}
            onChange={(e) => setSymbolSearch(e.target.value)}
            className="text-sm bg-transparent outline-none w-full"
            style={{ color: '#1a2744' }}
          />
        </div>
      </div>

      {/* Error bar */}
      {error && (
        <div
          className="mx-8 mt-3 px-4 py-2.5 rounded-lg text-sm"
          style={{ backgroundColor: '#fef2f2', border: '1px solid #fecaca', color: '#dc2626' }}
        >
          {error}
        </div>
      )}

      {/* 2×2 grid */}
      <div className="flex-1 grid grid-cols-2 grid-rows-2 gap-4 p-6 pt-4 min-h-0">
        {/* Top-left: Performance Graph */}
        <Card
          title="Performance Graph"
          action={
            <span className="text-xs" style={{ color: '#9ca3af' }}>
              Cumulative % vs SPY
            </span>
          }
        >
          {chartLoading ? (
            <div className="h-full flex items-center justify-center text-sm" style={{ color: '#9ca3af' }}>
              Loading...
            </div>
          ) : (
            <PerformanceChart data={performance} />
          )}
        </Card>

        {/* Top-right: Portfolio Metrics */}
        <Card title="Portfolio Metrics">
          <div className="grid grid-cols-2 gap-3">
            <MetricCard
              label="Portfolio Value"
              value={loading ? '...' : (fmt$(equityValue != null ? Number(equityValue) : null) ?? '—')}
            />
            <MetricCard
              label="Today's P&L"
              value={loading ? '...' : (fmt$(dayPnl != null ? Number(dayPnl) : null) ?? '—')}
              subValue={fmtPct(dayPnlPct != null ? Number(dayPnlPct) : null)}
              positive={dayPnl == null ? null : Number(dayPnl) >= 0}
            />
            <MetricCard
              label="Unrealized P&L"
              value={loading ? '...' : (fmt$(unrealizedPnl != null ? Number(unrealizedPnl) : null) ?? '—')}
              positive={unrealizedPnl == null ? null : Number(unrealizedPnl) >= 0}
            />
            <MetricCard
              label="Realized P&L"
              value={loading ? '...' : (fmt$(realizedPnl != null ? Number(realizedPnl) : null) ?? '—')}
              positive={realizedPnl == null ? null : Number(realizedPnl) >= 0}
            />
            <MetricCard
              label={`Return (${period.toUpperCase()})`}
              value={
                chartLoading
                  ? '...'
                  : fmtPct(metrics?.total_return_pct != null ? Number(metrics.total_return_pct) : null) ?? '—'
              }
              subValue={
                metrics?.spy_return_pct != null
                  ? `SPY: ${fmtPct(Number(metrics.spy_return_pct))}`
                  : undefined
              }
              positive={metrics?.total_return_pct == null ? null : Number(metrics.total_return_pct) >= 0}
            />
            <MetricCard
              label="Max Drawdown"
              value={
                chartLoading
                  ? '...'
                  : fmtNum(metrics?.max_drawdown_pct != null ? Number(metrics.max_drawdown_pct) : null, 2, '%') ?? '—'
              }
              positive={false}
            />
          </div>
        </Card>

        {/* Bottom-left: Trade Reports */}
        <Card title="Trade Reports">
          <div className="grid grid-cols-2 gap-3">
            <MetricCard
              label="Sharpe Ratio"
              value={
                chartLoading
                  ? '...'
                  : fmtNum(metrics?.sharpe_ratio != null ? Number(metrics.sharpe_ratio) : null) ?? '—'
              }
              positive={metrics?.sharpe_ratio == null ? null : Number(metrics.sharpe_ratio) >= 1}
            />
            <MetricCard
              label="Approx. Win Rate"
              value={
                chartLoading
                  ? '...'
                  : fmtNum(metrics?.win_rate != null ? Number(metrics.win_rate) : null, 1, '%') ?? '—'
              }
              positive={metrics?.win_rate == null ? null : Number(metrics.win_rate) >= 50}
            />
            <MetricCard
              label="Beta (vs SPY)"
              value={
                chartLoading
                  ? '...'
                  : fmtNum(metrics?.beta != null ? Number(metrics.beta) : null) ?? '—'
              }
            />
            <MetricCard
              label="Std Dev (Annual)"
              value={
                chartLoading
                  ? '...'
                  : fmtNum(metrics?.std_dev_annualized != null ? Number(metrics.std_dev_annualized) : null, 2, '%') ?? '—'
              }
            />
          </div>
        </Card>

        {/* Bottom-right: Current Positions */}
        <Card
          title="Current Positions"
          action={
            <span className="text-xs font-normal" style={{ color: '#9ca3af' }}>
              {filteredPositions.length} open
            </span>
          }
        >
          {loading ? (
            <div className="text-sm" style={{ color: '#9ca3af' }}>Loading...</div>
          ) : (
            <PositionsTable positions={filteredPositions} />
          )}
        </Card>
      </div>
    </div>
  )
}

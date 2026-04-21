import { useState, useEffect } from 'react'
import { Search, RefreshCw } from 'lucide-react'
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
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshMsg, setRefreshMsg] = useState<string | null>(null)

  const accounts: AccountSummary[] = summary?.accounts ?? []

  const accountParam = selectedAccount ? `&account_id=${encodeURIComponent(selectedAccount)}` : ''

  function loadSummary() {
    return get<PortfolioSummary>('/portfolio/summary')
      .then(setSummary)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadSummary()
    const id = setInterval(loadSummary, 60_000)
    return () => clearInterval(id)
  }, [])

  async function refreshPrices() {
    setRefreshing(true)
    setRefreshMsg(null)
    try {
      const res = await post<{ updated: number; total_symbols: number; errors: string[] }>('/portfolio/refresh-prices')
      setRefreshMsg(`${res.updated}/${res.total_symbols} updated`)
      await loadSummary()
    } catch (e: any) {
      setRefreshMsg(`Failed: ${e.message}`)
    } finally {
      setRefreshing(false)
    }
  }

  async function syncTrades() {
    setSyncing(true)
    setSyncMsg(null)
    try {
      const res = await post<{ inserted: number; skipped: number }>('/ibkr/sync/trades')
      setSyncMsg(`${res.inserted} new, ${res.skipped} skipped`)
      await loadSummary()
    } catch (e: any) {
      setSyncMsg(`Failed: ${e.message}`)
    } finally {
      setSyncing(false)
    }
  }

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
      <div
        className="px-8 pt-6 pb-0 shrink-0"
        style={{ backgroundColor: 'transparent' }}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-2xl font-bold" style={{ color: '#1a2744' }}>Dashboard</h2>
          <div className="flex items-center gap-3">
            {refreshMsg && (
              <span className="text-xs" style={{ color: '#6b7a99' }}>{refreshMsg}</span>
            )}
            {syncMsg && (
              <span className="text-xs" style={{ color: '#6b7a99' }}>{syncMsg}</span>
            )}
            <button
              onClick={refreshPrices}
              disabled={refreshing}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-50"
              style={{ backgroundColor: '#1a2744', border: '1px solid #1a2744', color: '#ffffff' }}
            >
              <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />
              {refreshing ? 'Fetching...' : 'Pull Live Prices'}
            </button>
            <button
              onClick={syncTrades}
              disabled={syncing}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-50"
              style={{ backgroundColor: '#ffffff', border: '1px solid #d0dce8', color: '#374151' }}
            >
              <RefreshCw size={13} className={syncing ? 'animate-spin' : ''} />
              {syncing ? 'Syncing...' : 'Sync Trades'}
            </button>
          </div>
        </div>

        {/* Tab bar */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1">
            {tabs.map((tab) => {
              const isActive = selectedAccount === tab.key
              return (
                <button
                  key={tab.key ?? 'overview'}
                  onClick={() => setSelectedAccount(tab.key)}
                  className="px-4 py-2 text-sm font-medium transition-colors relative"
                  style={{ color: isActive ? '#1a2744' : '#6b7a99' }}
                >
                  {tab.label}
                  {isActive && (
                    <span
                      className="absolute bottom-0 left-0 right-0 h-0.5 rounded-t"
                      style={{ backgroundColor: '#1a2744' }}
                    />
                  )}
                </button>
              )
            })}
          </div>

          {/* Search + period */}
          <div className="flex items-center gap-2 mb-1">
            {/* Period pills */}
            <div
              className="flex items-center rounded-lg p-0.5"
              style={{ backgroundColor: '#ffffff', border: '1px solid #d0dce8' }}
            >
              {PERIODS.map((p) => (
                <button
                  key={p.value}
                  onClick={() => setPeriod(p.value)}
                  className="px-3 py-1 rounded-md text-xs font-medium transition-colors"
                  style={
                    period === p.value
                      ? { backgroundColor: '#1a2744', color: '#ffffff' }
                      : { color: '#6b7a99' }
                  }
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
                placeholder="Search for..."
                value={symbolSearch}
                onChange={(e) => setSymbolSearch(e.target.value)}
                className="text-sm bg-transparent outline-none w-full"
                style={{ color: '#1a2744' }}
              />
            </div>
          </div>
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
              label="Win Rate"
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

import { useState, useEffect } from 'react'
import type {
  AccountSummary,
  IBKRAccount,
  IBKRLivePosition,
  IBKRStatus,
  PerformancePoint,
  Period,
  PortfolioMetrics,
  PortfolioSummary,
  PositionSummary,
} from '../types'
import { get } from '../api/client'
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

export default function Dashboard() {
  const [period, setPeriod] = useState<Period>('ytd')
  const [sourceFilter, setSourceFilter] = useState<'all' | 'ibkr' | 'fidelity'>('all')
  const [selectedAccount, setSelectedAccount] = useState<string | null>(null)

  const [summary, setSummary] = useState<PortfolioSummary | null>(null)
  const [metrics, setMetrics] = useState<PortfolioMetrics | null>(null)
  const [performance, setPerformance] = useState<PerformancePoint[]>([])
  const [loading, setLoading] = useState(true)
  const [chartLoading, setChartLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [ibkrStatus, setIbkrStatus] = useState<IBKRStatus | null>(null)
  const [ibkrAccount, setIbkrAccount] = useState<IBKRAccount | null>(null)
  const [ibkrPositions, setIbkrPositions] = useState<IBKRLivePosition[]>([])

  const accounts: AccountSummary[] = summary?.accounts ?? []

  // IBKR account chip when summary has no trade-derived accounts
  const displayAccounts: AccountSummary[] =
    accounts.length > 0
      ? accounts
      : ibkrStatus?.account_id
        ? [{
            account_id: ibkrStatus.account_id,
            source: 'ibkr' as const,
            total_nav: ibkrAccount?.total_nav ?? null,
            cash_balance: ibkrAccount?.cash_balance ?? null,
            equity_value: ibkrAccount?.equity_value ?? null,
            day_pnl: null,
            day_pnl_pct: null,
            total_realized_pnl: null,
            total_unrealized_pnl: null,
          }]
        : []

  const visibleAccounts = displayAccounts.filter(
    (a) => sourceFilter === 'all' || a.source === sourceFilter
  )

  const accountParam = selectedAccount ? `&account_id=${encodeURIComponent(selectedAccount)}` : ''

  // Load summary once on mount
  useEffect(() => {
    get<PortfolioSummary>('/portfolio/summary')
      .then(setSummary)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  // IBKR live connection status (separate from DB-derived portfolio)
  useEffect(() => {
    get<IBKRStatus>('/ibkr/status')
      .then((status) => {
        setIbkrStatus(status)
        if (status.connected && status.authenticated) {
          get<IBKRAccount>('/ibkr/account').then(setIbkrAccount).catch(() => setIbkrAccount(null))
          get<IBKRLivePosition[]>('/ibkr/positions').then(setIbkrPositions).catch(() => setIbkrPositions([]))
        }
      })
      .catch(() => setIbkrStatus({ enabled: false, message: 'Could not reach /ibkr/status' }))
  }, [])

  // Load metrics + performance when period or account changes
  // Metrics failure is non-fatal — just show dashes, no red error bar
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

  // Active account data for top-line numbers
  const activeAccount: AccountSummary | null =
    selectedAccount ? accounts.find((a) => a.account_id === selectedAccount) ?? null : null

  const equityValue =
    activeAccount?.equity_value ??
    summary?.combined_nav ??
    summary?.combined_equity_value ??
    ibkrAccount?.total_nav
  const dayPnl = activeAccount?.day_pnl ?? summary?.combined_day_pnl
  const dayPnlPct = activeAccount?.day_pnl_pct ?? summary?.combined_day_pnl_pct
  const unrealizedPnl =
    activeAccount?.total_unrealized_pnl ??
    summary?.total_unrealized_pnl ??
    (ibkrPositions.length
      ? ibkrPositions.reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0)
      : null)
  const realizedPnl = activeAccount?.total_realized_pnl ?? summary?.total_realized_pnl

  // Positions: summary (IBKR-backed when trades table empty), else live IBKR fetch
  const allPositions: PositionSummary[] =
    summary?.positions?.length ? summary.positions : ibkrPositions.map((p) => ({
      symbol: p.symbol,
      account_id: ibkrStatus?.account_id ?? '',
      quantity: p.quantity,
      avg_cost: p.avg_cost ?? null,
      current_price: p.market_price ?? null,
      market_value: p.market_value ?? null,
      unrealized_pnl: p.unrealized_pnl ?? null,
      unrealized_pnl_pct: null,
      label: null,
    }))
  const filteredPositions = allPositions.filter((p) => {
    if (selectedAccount) return p.account_id === selectedAccount
    if (sourceFilter === 'ibkr') return p.account_id.startsWith('U') || p.account_id === ibkrStatus?.account_id
    if (sourceFilter === 'fidelity') {
      const acct = displayAccounts.find((a) => a.account_id === p.account_id)
      return acct?.source === 'fidelity'
    }
    return true
  })

  return (
    <div className="p-6 max-w-screen-xl mx-auto">
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
      </div>

      {/* IBKR live status */}
      {ibkrStatus && (
        <div
          className={`mb-6 px-4 py-3 rounded-lg border text-sm ${
            ibkrStatus.connected && ibkrStatus.authenticated
              ? 'bg-green-950/40 border-green-800/50 text-green-200'
              : ibkrStatus.enabled
                ? 'bg-amber-950/40 border-amber-800/50 text-amber-200'
                : 'bg-gray-900 border-gray-800 text-gray-400'
          }`}
        >
          <span className="font-medium">IBKR</span>
          {' · '}
          {ibkrStatus.enabled ? (
            ibkrStatus.connected && ibkrStatus.authenticated ? (
              <>
                Connected ({ibkrStatus.mode}) · {ibkrStatus.account_id}
                {ibkrAccount?.total_nav != null && (
                  <> · NAV {fmt$(ibkrAccount.total_nav)}</>
                )}
                {ibkrPositions.length > 0 && (
                  <> · {ibkrPositions.length} live positions</>
                )}
              </>
            ) : (
              <>Not connected — {ibkrStatus.message ?? 'check API logs'}</>
            )
          ) : (
            <>{ibkrStatus.message ?? 'disabled'}</>
          )}
        </div>
      )}

      {/* Source + account toggles */}
      <div className="flex flex-wrap gap-2 mb-6">
        {/* Source filter */}
        <div className="flex bg-gray-900 border border-gray-800 rounded-lg p-1 gap-0.5 text-xs">
          {(['all', 'ibkr', 'fidelity'] as const).map((s) => (
            <button
              key={s}
              onClick={() => {
                setSourceFilter(s)
                setSelectedAccount(null)
              }}
              className={`px-3 py-1.5 rounded capitalize transition-colors ${
                sourceFilter === s && !selectedAccount
                  ? 'bg-gray-700 text-white font-medium'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              {s === 'all' ? 'All Accounts' : s === 'ibkr' ? 'IBKR' : 'Fidelity'}
            </button>
          ))}
        </div>

        {/* Per-account chips */}
        {visibleAccounts.map((a) => (
          <button
            key={a.account_id}
            onClick={() =>
              setSelectedAccount(selectedAccount === a.account_id ? null : a.account_id)
            }
            className={`px-3 py-1.5 rounded-lg border text-xs transition-colors ${
              selectedAccount === a.account_id
                ? 'bg-blue-600/20 border-blue-500/50 text-blue-300 font-medium'
                : 'border-gray-700 text-gray-400 hover:text-white hover:border-gray-500'
            }`}
          >
            {a.account_id}
            <span className="ml-1 text-gray-600">
              · {a.source === 'ibkr' ? 'IB' : 'Fidelity'}
            </span>
          </button>
        ))}
      </div>

      {error && (
        <div className="bg-red-950/50 border border-red-800/50 text-red-300 px-4 py-3 rounded-lg mb-6 text-sm">
          {error}
        </div>
      )}

      {/* Top-line metrics row 1 */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-3">
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
          positive={
            metrics?.total_return_pct == null ? null : Number(metrics.total_return_pct) >= 0
          }
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

      {/* Metrics row 2 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
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
        <MetricCard
          label="Sharpe Ratio"
          value={
            chartLoading
              ? '...'
              : fmtNum(metrics?.sharpe_ratio != null ? Number(metrics.sharpe_ratio) : null) ?? '—'
          }
          positive={
            metrics?.sharpe_ratio == null ? null : Number(metrics.sharpe_ratio) >= 1
          }
        />
        <MetricCard
          label="Win Rate"
          value={
            chartLoading
              ? '...'
              : fmtNum(metrics?.win_rate != null ? Number(metrics.win_rate) : null, 1, '%') ?? '—'
          }
          positive={
            metrics?.win_rate == null ? null : Number(metrics.win_rate) >= 50
          }
        />
      </div>

      {/* Performance chart */}
      <div className="bg-gray-900 rounded-lg border border-gray-800 p-5 mb-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-medium text-gray-200">Performance vs SPY</h3>
          <span className="text-xs text-gray-600">Cumulative % return from period start</span>
        </div>
        {chartLoading ? (
          <div className="h-64 flex items-center justify-center text-gray-600 text-sm">
            Loading...
          </div>
        ) : (
          <PerformanceChart data={performance} />
        )}
      </div>

      {/* Open positions */}
      <div className="bg-gray-900 rounded-lg border border-gray-800 p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-medium text-gray-200">
            Open Positions
            <span className="ml-2 text-gray-600 font-normal">({filteredPositions.length})</span>
          </h3>
          {selectedAccount && (
            <span className="text-xs text-blue-400">{selectedAccount}</span>
          )}
        </div>
        {loading ? (
          <div className="text-gray-600 text-sm py-4">Loading...</div>
        ) : (
          <PositionsTable positions={filteredPositions} />
        )}
      </div>
    </div>
  )
}

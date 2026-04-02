import { useState, useEffect } from 'react'
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
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState<string | null>(null)

  const accounts: AccountSummary[] = summary?.accounts ?? []

  const visibleAccounts = accounts.filter(
    (a) => sourceFilter === 'all' || a.source === sourceFilter
  )

  const accountParam = selectedAccount ? `&account_id=${encodeURIComponent(selectedAccount)}` : ''

  function loadSummary() {
    return get<PortfolioSummary>('/portfolio/summary')
      .then(setSummary)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  // Load summary on mount + auto-refresh every 60s
  useEffect(() => {
    loadSummary()
    const id = setInterval(loadSummary, 60_000)
    return () => clearInterval(id)
  }, [])

  async function syncTrades() {
    setSyncing(true)
    setSyncMsg(null)
    try {
      const res = await post<{ inserted: number; skipped: number }>('/ibkr/sync/trades')
      setSyncMsg(`Synced: ${res.inserted} new, ${res.skipped} skipped`)
      await loadSummary()
    } catch (e: any) {
      setSyncMsg(`Sync failed: ${e.message}`)
    } finally {
      setSyncing(false)
    }
  }

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

  const equityValue = activeAccount?.equity_value ?? summary?.combined_equity_value
  const dayPnl = activeAccount?.day_pnl ?? summary?.combined_day_pnl
  const dayPnlPct = activeAccount?.day_pnl_pct ?? summary?.combined_day_pnl_pct
  const unrealizedPnl = activeAccount?.total_unrealized_pnl ?? summary?.total_unrealized_pnl
  const realizedPnl = activeAccount?.total_realized_pnl ?? summary?.total_realized_pnl

  // Filter positions by source/account
  const allPositions: PositionSummary[] = summary?.positions ?? []
  const filteredPositions = allPositions.filter((p) => {
    if (selectedAccount) return p.account_id === selectedAccount
    if (sourceFilter !== 'all') {
      const acct = accounts.find((a) => a.account_id === p.account_id)
      return acct?.source === sourceFilter
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

        <div className="flex items-center gap-3">
          {/* Sync IBKR trades */}
          <div className="flex flex-col items-end">
            <button
              onClick={syncTrades}
              disabled={syncing}
              className="px-3 py-1.5 rounded text-xs font-medium transition-colors disabled:opacity-50"
              style={{ border: '1px solid #1a2030', color: '#64748b' }}
              onMouseOver={e => { if (!syncing) (e.currentTarget as HTMLButtonElement).style.color = '#e2e8f0' }}
              onMouseOut={e => (e.currentTarget as HTMLButtonElement).style.color = '#64748b'}
            >
              {syncing ? 'Syncing...' : '↻ Sync Trades'}
            </button>
            {syncMsg && <p className="text-xs mt-0.5" style={{ color: '#64748b' }}>{syncMsg}</p>}
          </div>

        {/* Period selector */}
        <div
          className="flex rounded-lg p-1 gap-0.5"
          style={{ backgroundColor: '#0d1117', border: '1px solid #1a2030' }}
        >
          {PERIODS.map((p) => (
            <button
              key={p.value}
              onClick={() => setPeriod(p.value)}
              className="px-3 py-1.5 rounded text-sm font-medium transition-colors"
              style={
                period === p.value
                  ? { backgroundColor: '#2563eb', color: '#fff' }
                  : { color: '#64748b' }
              }
            >
              {p.label}
            </button>
          ))}
        </div>
        </div>
      </div>

      {/* Source + account toggles */}
      <div className="flex flex-wrap gap-2 mb-6">
        {/* Source filter */}
        <div
          className="flex rounded-lg p-1 gap-0.5 text-xs"
          style={{ backgroundColor: '#0d1117', border: '1px solid #1a2030' }}
        >
          {(['all', 'ibkr', 'fidelity'] as const).map((s) => (
            <button
              key={s}
              onClick={() => {
                setSourceFilter(s)
                setSelectedAccount(null)
              }}
              style={
                sourceFilter === s && !selectedAccount
                  ? { backgroundColor: '#1e293b', color: '#e2e8f0' }
                  : { color: '#64748b' }
              }
              className="px-3 py-1.5 rounded capitalize transition-colors hover:text-white font-medium"
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
            className="px-3 py-1.5 rounded-lg text-xs transition-colors"
            style={
              selectedAccount === a.account_id
                ? { backgroundColor: 'rgba(59,130,246,0.15)', border: '1px solid rgba(59,130,246,0.4)', color: '#93c5fd' }
                : { border: '1px solid #1a2030', color: '#64748b' }
            }
          >
            {a.account_id}
            <span style={{ color: '#374151' }} className="ml-1">
              · {a.source === 'ibkr' ? 'IB' : 'Fidelity'}
            </span>
          </button>
        ))}
      </div>

      {error && (
        <div
          className="px-4 py-3 rounded-lg mb-6 text-sm"
          style={{ backgroundColor: 'rgba(127,29,29,0.3)', border: '1px solid rgba(185,28,28,0.4)', color: '#fca5a5' }}
        >
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
      <div
        className="rounded-lg p-5 mb-6"
        style={{ backgroundColor: '#0d1117', border: '1px solid #1a2030' }}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-medium" style={{ color: '#e2e8f0' }}>Performance vs SPY</h3>
          <span className="text-xs" style={{ color: '#374151' }}>Cumulative % return from period start</span>
        </div>
        {chartLoading ? (
          <div className="h-64 flex items-center justify-center text-sm" style={{ color: '#374151' }}>
            Loading...
          </div>
        ) : (
          <PerformanceChart data={performance} />
        )}
      </div>

      {/* Open positions */}
      <div
        className="rounded-lg p-5"
        style={{ backgroundColor: '#0d1117', border: '1px solid #1a2030' }}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-medium" style={{ color: '#e2e8f0' }}>
            Open Positions
            <span className="ml-2 font-normal" style={{ color: '#374151' }}>({filteredPositions.length})</span>
          </h3>
          {selectedAccount && (
            <span className="text-xs" style={{ color: '#60a5fa' }}>{selectedAccount}</span>
          )}
        </div>
        {loading ? (
          <div className="text-sm py-4" style={{ color: '#374151' }}>Loading...</div>
        ) : (
          <PositionsTable positions={filteredPositions} />
        )}
      </div>
    </div>
  )
}

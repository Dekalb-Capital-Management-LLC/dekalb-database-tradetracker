import { useState, useEffect, useCallback, type ReactNode } from 'react'
import { Search, Construction, Database, RefreshCw, Settings, Bell, LogOut, User, Wallet } from 'lucide-react'

import type {
  AccountSummary,
  DashboardCompatibilityStatus,
  IBKRAccount,
  IBKRStatus,
  MarketDataStatus,
  PerformancePoint,
  Period,
  PortfolioMetrics,
  PortfolioSummary,
  PositionSummary,
} from '../types'
import { get, post } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import {
  isCashSymbol,
  matchesDashboardView,
  unknownHeldSymbols,
  useAnalyst,
  type Analyst,
} from '../auth/AnalystContext'
import MetricCard from '../components/MetricCard'
import PerformanceChart from '../components/PerformanceChart'
import PositionsTable from '../components/PositionsTable'
import FidelityUpdateWizard from '../components/FidelityUpdateWizard'
import CashFlowModal from '../components/CashFlowModal'
import AnalystSettingsModal from '../components/AnalystSettingsModal'
import NewTickerPrompt from '../components/NewTickerPrompt'
import FactorAnalysisPanel from '../components/FactorAnalysisPanel'
import ErrorBoundary from '../components/ErrorBoundary'
import Trades from './Trades'

const FIDELITY_WIZARD_SESSION_KEY = 'fidelity_wizard_prompted'

const PERIODS: { value: Period; label: string }[] = [
  { value: '1m', label: '1M' },
  { value: '3m', label: '3M' },
  { value: '6m', label: '6M' },
  { value: 'ytd', label: 'YTD' },
  { value: '1y', label: '1Y' },
]

type TabKey = 'ibkr' | 'fidelity' | 'ironbeam' | 'trades'

/** Symbols for the chart's "My stocks" overlay (held names, analyst-filtered). */
function watchlistSymbols(
  analyst: Analyst | null,
  positions: PositionSummary[],
): string[] {
  const held = positions
    .filter((p) => {
      const sym = p.symbol.toUpperCase()
      if (!sym || isCashSymbol(sym)) return false
      return matchesDashboardView(p.symbol, p.label, analyst)
    })
    .sort((a, b) => Math.abs(Number(b.market_value ?? 0)) - Math.abs(Number(a.market_value ?? 0)))
    .map((p) => p.symbol.toUpperCase())

  // Cap at 6 (same as factor-analysis top holdings) so the chart stays readable.
  const unique = [...new Set(held)].slice(0, 6)
  if (unique.length) return unique

  // Tickers mode with selections but no open lots yet — still chart selected names.
  if (analyst?.onboarded && analyst.view_mode === 'tickers') {
    return analyst.tickers.filter((t) => t.visible).map((t) => t.symbol.toUpperCase()).slice(0, 6)
  }
  return []
}

const TABS: { key: TabKey; label: string; disabled?: boolean }[] = [
  { key: 'ibkr', label: 'IBKR' },
  { key: 'fidelity', label: 'Fidelity' },
  { key: 'ironbeam', label: 'IronBeam', disabled: true },
  { key: 'trades', label: 'Trades' },
]

function sumOrNull(values: (number | null | undefined)[]) {
  const nums = values.filter((v) => v != null).map(Number)
  if (!nums.length) return null
  return nums.reduce((a, b) => a + b, 0)
}

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

function marketProviderLabel(provider: string | null | undefined) {
  switch ((provider ?? '').toLowerCase()) {
    case 'firstrate':
      return 'FirstRateData'
    case 'ibkr':
      return 'IBKR'
    case 'yfinance':
      return 'yfinance'
    default:
      return provider || 'market data'
  }
}

function quantCompatLabel(status: string | null | undefined) {
  switch ((status ?? '').toLowerCase()) {
    case 'active':
    case 'configured':
      return 'Quant ready'
    case 'planned':
      return 'Quant hooks'
    default:
      return 'Quant'
  }
}

/* ── card shell ── */
function Card({
  title,
  children,
  action,
  delay = 0,
  className = '',
}: {
  title: string
  children: ReactNode
  action?: ReactNode
  delay?: number
  className?: string
}) {
  return (
    <div
      className={`flex flex-col animate-fade-in-up ${className}`}
      style={{
        backgroundColor: '#ffffff',
        border: '1px solid #d0dce8',
        borderRadius: 8,
        overflow: 'hidden',
        animationDelay: `${delay}ms`,
        boxShadow: '0 1px 2px rgba(16,24,40,0.04)',
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
  const { signOut } = useAuth()
  const { analyst } = useAnalyst()
  const [period, setPeriod] = useState<Period>('ytd')
  const [selectedTab, setSelectedTab] = useState<TabKey>('ibkr')
  const [symbolSearch, setSymbolSearch] = useState('')
  const [logoError, setLogoError] = useState(false)

  const [summary, setSummary] = useState<PortfolioSummary | null>(null)
  const [metrics, setMetrics] = useState<PortfolioMetrics | null>(null)
  const [performance, setPerformance] = useState<PerformancePoint[]>([])
  const [loading, setLoading] = useState(true)
  const [chartLoading, setChartLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [updating, setUpdating] = useState(false)
  const [updateMsg, setUpdateMsg] = useState<string | null>(null)
  const [ibkrStatus, setIbkrStatus] = useState<IBKRStatus | null>(null)
  const [ibkrAccount, setIbkrAccount] = useState<IBKRAccount | null>(null)
  const [marketDataStatus, setMarketDataStatus] = useState<MarketDataStatus | null>(null)
  const [dashboardCompat, setDashboardCompat] = useState<DashboardCompatibilityStatus | null>(null)
  const [showFidelityWizard, setShowFidelityWizard] = useState(false)
  const [showCashFlowModal, setShowCashFlowModal] = useState(false)
  const [showSettings, setShowSettings] = useState(false)

  const accounts: AccountSummary[] = summary?.accounts ?? []

  // Accounts belonging to the active tab (IronBeam/Trades never match — no source yet)
  const brokerAccounts = accounts.filter((a) => a.source === selectedTab)
  // Metrics/performance endpoints only take a single account_id, so we use the
  // first matching account for the active broker (today there's at most one per broker)
  const brokerAccountId = brokerAccounts[0]?.account_id ?? null

  const loadSummary = useCallback(() => {
    return get<PortfolioSummary>('/portfolio/summary')
      .then(setSummary)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const watchSymbols = watchlistSymbols(
    analyst,
    (summary?.positions ?? []).filter(
      (p) => !brokerAccountId || p.account_id === brokerAccountId,
    ),
  )

  const loadAnalytics = useCallback(() => {
    if (selectedTab === 'trades') return Promise.resolve()
    const param = brokerAccountId ? `&account_id=${encodeURIComponent(brokerAccountId)}` : ''
    const symParam = watchSymbols.length
      ? `&symbols=${encodeURIComponent(watchSymbols.join(','))}`
      : ''
    setChartLoading(true)
    return Promise.allSettled([
      get<PortfolioMetrics>(`/portfolio/metrics?period=${period}${param}`),
      get<PerformancePoint[]>(`/portfolio/performance?period=${period}${param}${symParam}`),
    ])
      .then(([metricsResult, perfResult]) => {
        if (metricsResult.status === 'fulfilled') setMetrics(metricsResult.value)
        else setMetrics(null)
        if (perfResult.status === 'fulfilled') setPerformance(perfResult.value)
        else setPerformance([])
      })
      .finally(() => setChartLoading(false))
  }, [period, selectedTab, brokerAccountId, watchSymbols.join(',')])

  async function updatePortfolio() {
    setUpdating(true)
    setUpdateMsg(null)
    try {
      const res = await post<{
        ibkr_positions: number
        ibkr_trades_synced: number
        market_data_updated?: number
        market_data_total?: number
        yfinance_updated?: number
        yfinance_total?: number
        snapshot_written: boolean
        portfolio_nav: number | null
      }>('/portfolio/update-all')
      const marketUpdated = res.market_data_updated ?? res.yfinance_updated ?? 0
      const marketTotal = res.market_data_total ?? res.yfinance_total ?? 0
      const parts: string[] = []
      if (res.ibkr_positions > 0) parts.push(`IBKR: ${res.ibkr_positions} pos`)
      if (res.ibkr_trades_synced > 0) parts.push(`${res.ibkr_trades_synced} new trades`)
      parts.push(`market: ${marketUpdated}/${marketTotal}`)
      parts.push(`snapshot ${res.snapshot_written ? '✓' : '✗'}`)
      if (res.portfolio_nav != null)
        parts.push(`NAV $${res.portfolio_nav.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)
      setUpdateMsg(parts.join(' · '))
      await Promise.all([loadSummary(), loadAnalytics()])
    } catch (e: any) {
      setUpdateMsg(`Error: ${e.message}`)
    } finally {
      setUpdating(false)
    }
  }

  // The single "Update Portfolio" action is contextual: on Fidelity it opens
  // the upload wizard (there's no live feed to refresh), everywhere else it
  // triggers the real IBKR/market-data update.
  function handleUpdateClick() {
    if (selectedTab === 'fidelity') {
      setShowFidelityWizard(true)
    } else {
      updatePortfolio()
    }
  }

  // Load summary on mount; auto-refresh every 5 min — matches backend cadence.
  useEffect(() => {
    loadSummary()
  }, [loadSummary])

  useEffect(() => {
    const id = setInterval(() => {
      loadSummary()
      loadAnalytics()
    }, 300_000)
    return () => clearInterval(id)
  }, [loadSummary, loadAnalytics])

  useEffect(() => {
    if (selectedTab === 'fidelity' && !sessionStorage.getItem(FIDELITY_WIZARD_SESSION_KEY)) {
      sessionStorage.setItem(FIDELITY_WIZARD_SESSION_KEY, '1')
      setShowFidelityWizard(true)
    }
  }, [selectedTab])

  useEffect(() => {
    get<IBKRStatus>('/ibkr/status')
      .then((status) => {
        setIbkrStatus(status)
        if (status.connected && status.authenticated) {
          get<IBKRAccount>('/ibkr/account').then(setIbkrAccount).catch(() => setIbkrAccount(null))
        }
      })
      .catch(() => setIbkrStatus(null))
  }, [])

  useEffect(() => {
    get<MarketDataStatus>('/market/provider/status')
      .then(setMarketDataStatus)
      .catch(() => setMarketDataStatus(null))
  }, [])

  useEffect(() => {
    get<DashboardCompatibilityStatus>('/dashboard/capabilities')
      .then(setDashboardCompat)
      .catch(() => setDashboardCompat(null))
  }, [])

  // Wait for summary before analytics on broker tabs so account_id is known.
  useEffect(() => {
    if (selectedTab === 'trades') return
    if (loading) return
    const needsAccount = selectedTab === 'ibkr' || selectedTab === 'fidelity'
    if (needsAccount && !brokerAccountId) {
      setMetrics(null)
      setPerformance([])
      setChartLoading(false)
      return
    }

    loadAnalytics()
  }, [period, selectedTab, brokerAccountId, loading, loadAnalytics])

  const hasBrokerData = brokerAccounts.length > 0

  // Portfolio Value = full account NAV (equities + cash), not stock-only equity.
  const portfolioValue = hasBrokerData
    ? (
        sumOrNull(brokerAccounts.map((a) => a.total_nav))
        ?? sumOrNull(
          brokerAccounts.map((a) => {
            const eq = a.equity_value != null ? Number(a.equity_value) : null
            const cash = a.cash_balance != null ? Number(a.cash_balance) : 0
            return eq != null ? eq + cash : null
          }),
        )
        ?? ibkrAccount?.total_nav
        ?? null
      )
    : null
  const dayPnl = hasBrokerData ? sumOrNull(brokerAccounts.map((a) => a.day_pnl)) : null
  const dayPnlPct = brokerAccounts.length === 1 ? brokerAccounts[0].day_pnl_pct : null
  const unrealizedPnl = hasBrokerData ? sumOrNull(brokerAccounts.map((a) => a.total_unrealized_pnl)) : null
  const realizedPnl = hasBrokerData ? sumOrNull(brokerAccounts.map((a) => a.total_realized_pnl)) : null

  const brokerAccountIds = new Set(brokerAccounts.map((a) => a.account_id))
  const allPositions: PositionSummary[] = summary?.positions ?? []
  const filteredPositions = allPositions.filter((p) => {
    const matchAccount = brokerAccountIds.has(p.account_id)
    const matchSymbol = symbolSearch.trim()
      ? p.symbol.toUpperCase().includes(symbolSearch.trim().toUpperCase())
      : true
    const matchView = matchesDashboardView(p.symbol, p.label, analyst)
    return matchAccount && matchSymbol && matchView
  })
  const promptSymbols = unknownHeldSymbols(allPositions, analyst)

  const ibkrDotColor = !ibkrStatus?.enabled
    ? '#c0ccd8'
    : ibkrStatus.connected && ibkrStatus.authenticated
    ? '#16a34a'
    : '#d97706'
  const quantModule = dashboardCompat?.modules.find((module) => module.key === 'quant-ingestion')

  return (
    <div className="flex flex-col min-h-screen" style={{ backgroundColor: '#e8edf5' }}>
      {/* Top white bar — logo, title/timestamp, IBKR dot, update action, account, settings */}
      <header
        className="flex items-center justify-between px-3 sm:px-6 shrink-0 gap-2 sm:gap-4"
        style={{ backgroundColor: '#ffffff', borderBottom: '1px solid #e2e8f0', height: 64, zIndex: 10 }}
      >
        <div className="flex items-center gap-4 min-w-0">
          {!logoError ? (
            <img
              src="/logo.png"
              alt="DeKalb Capital"
              className="h-7 sm:h-9 w-auto max-w-[100px] sm:max-w-[160px] object-contain"
              onError={() => setLogoError(true)}
            />
          ) : (
            <div
              className="flex items-center justify-center rounded font-bold text-sm shrink-0"
              style={{ width: 36, height: 36, backgroundColor: '#1a2744', color: '#ffffff' }}
            >
              DC
            </div>
          )}
          <div className="hidden sm:block min-w-0" style={{ borderLeft: '1px solid #e2e8f0', paddingLeft: 16 }}>
            <h2 className="text-base font-semibold truncate" style={{ color: '#1a2744' }}>Portfolio Overview</h2>
            <p className="text-xs truncate" style={{ color: '#9ca3af' }}>
              {summary ? `As of ${new Date(summary.as_of).toLocaleString()}` : ' '}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1 sm:gap-3 shrink-0">
          {updateMsg && <span className="text-xs hidden lg:inline" style={{ color: '#9ca3af' }}>{updateMsg}</span>}
          {marketDataStatus && (
            <span
              className="hidden md:flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs font-medium"
              style={{ borderColor: '#d0dce8', color: '#374151', backgroundColor: '#f8fafc' }}
              title={`Market data provider: ${marketProviderLabel(marketDataStatus.active_provider)}`}
            >
              <Database size={14} color="#6b7a99" strokeWidth={1.8} />
              {marketProviderLabel(marketDataStatus.active_provider)}
            </span>
          )}
          {quantModule && (
            <span
              className="hidden xl:flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs font-medium"
              style={{ borderColor: '#d0dce8', color: '#374151', backgroundColor: '#f8fafc' }}
              title={`Quant dashboard compatibility: ${quantModule.status}`}
            >
              <Construction size={14} color="#6b7a99" strokeWidth={1.8} />
              {quantCompatLabel(quantModule.status)}
            </span>
          )}
          {ibkrStatus?.enabled && (
            <span
              title={ibkrStatus.connected && ibkrStatus.authenticated ? 'IBKR connected' : 'IBKR connecting'}
              className={`w-2 h-2 rounded-full ${ibkrStatus.connected ? 'animate-pulse' : ''}`}
              style={{ backgroundColor: ibkrDotColor }}
            />
          )}
          <button
            onClick={() => setShowCashFlowModal(true)}
            aria-label="Log deposit or withdrawal"
            className="flex items-center gap-1.5 p-2 lg:px-3 lg:py-2 rounded-lg text-sm font-medium border transition-colors hover:bg-gray-50"
            style={{ borderColor: '#d0dce8', color: '#374151' }}
            title="Log a deposit or withdrawal so it doesn't get counted as gain/loss"
          >
            <Wallet size={15} />
            <span className="hidden lg:inline">Deposit/Withdrawal</span>
          </button>
          <button
            onClick={handleUpdateClick}
            disabled={updating}
            aria-label={updating ? 'Updating portfolio' : 'Update portfolio'}
            className="flex items-center gap-2 p-2 sm:px-4 sm:py-2 rounded-lg text-sm font-semibold shadow-sm hover:shadow-md disabled:opacity-50 disabled:shadow-none transition-all"
            style={{ backgroundColor: '#1a2744', color: '#ffffff' }}
          >
            <RefreshCw size={15} className={updating ? 'animate-spin' : ''} />
            <span className="hidden sm:inline">{updating ? 'Updating…' : 'Update Portfolio'}</span>
          </button>

          <div className="hidden sm:flex items-center" style={{ borderLeft: '1px solid #e2e8f0', paddingLeft: 12, gap: 6 }}>
            <div
              className="flex items-center justify-center rounded-full transition-transform duration-150 hover:scale-105 cursor-pointer"
              style={{ width: 34, height: 34, backgroundColor: '#d1dce8' }}
              title={analyst?.display_name ?? 'Account'}
              onClick={() => setShowSettings(true)}
            >
              <User size={16} color="#6b7a99" />
            </div>
            <button
              className="p-2 rounded-lg hover:bg-gray-50 transition-colors"
              title="Settings"
              onClick={() => setShowSettings(true)}
            >
              <Settings size={16} color="#9ca3af" strokeWidth={1.8} />
            </button>
            <button className="p-2 rounded-lg hover:bg-gray-50 transition-colors" title="Notifications">
              <Bell size={16} color="#9ca3af" strokeWidth={1.8} />
            </button>
            <button onClick={signOut} className="p-2 rounded-lg hover:bg-gray-50 transition-colors" title="Sign out">
              <LogOut size={16} color="#9ca3af" strokeWidth={1.8} />
            </button>
          </div>
          <button
            onClick={signOut}
            className="sm:hidden p-2 rounded-lg hover:bg-gray-50 transition-colors"
            title="Sign out"
            aria-label="Sign out"
          >
            <LogOut size={16} color="#9ca3af" strokeWidth={1.8} />
          </button>
        </div>
      </header>

      <div className="flex-1 p-4 sm:p-6 pb-0 flex flex-col min-w-0">
        {/* Tab row + period selector & search */}
        <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
          <div className="flex items-end gap-1" style={{ borderBottom: '1px solid #e2e8f0' }}>
            {TABS.map((t) => {
              const isActive = selectedTab === t.key
              return (
                <button
                  key={t.key}
                  onClick={() => !t.disabled && setSelectedTab(t.key)}
                  disabled={t.disabled && !isActive}
                  className="relative px-4 py-2 text-sm font-medium transition-colors"
                  style={{
                    color: isActive ? '#2563eb' : t.disabled ? '#c0ccd8' : '#6b7a99',
                    cursor: t.disabled ? 'default' : 'pointer',
                  }}
                >
                  {t.label}
                  {t.disabled && (
                    <span
                      className="ml-1.5 text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded"
                      style={{ backgroundColor: '#f1f5f9', color: '#9ca3af' }}
                    >
                      Soon
                    </span>
                  )}
                  <span
                    className="absolute left-0 right-0 -bottom-px h-0.5 rounded-full transition-all duration-200"
                    style={{
                      backgroundColor: isActive ? '#2563eb' : 'transparent',
                      transform: isActive ? 'scaleX(1)' : 'scaleX(0.6)',
                      opacity: isActive ? 1 : 0,
                    }}
                  />
                </button>
              )
            })}
          </div>

          {selectedTab !== 'trades' && (
            <div className="flex w-full sm:w-auto items-center gap-3 flex-wrap sm:flex-nowrap">
              <div className="flex bg-white border rounded-lg p-1 gap-0.5" style={{ borderColor: '#d0dce8' }}>
                {PERIODS.map((p) => (
                  <button
                    key={p.value}
                    onClick={() => setPeriod(p.value)}
                    className="px-3 py-1.5 rounded text-sm font-medium transition-all"
                    style={{
                      backgroundColor: period === p.value ? '#2563eb' : 'transparent',
                      color: period === p.value ? '#ffffff' : '#6b7a99',
                      boxShadow: period === p.value ? '0 1px 4px rgba(37,99,235,0.35)' : 'none',
                    }}
                  >
                    {p.label}
                  </button>
                ))}
              </div>

              <div
                className="flex flex-1 sm:flex-none min-w-[140px] sm:min-w-[180px] items-center gap-2 px-3 py-1.5 rounded-lg transition-all focus-within:shadow-md"
                style={{ backgroundColor: '#ffffff', border: '1px solid #d0dce8' }}
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
          )}
        </div>

        {selectedTab === 'fidelity' && (
          <div
            className="mb-3 px-4 py-2.5 rounded-lg text-sm animate-fade-in"
            style={{ backgroundColor: '#eff6ff', border: '1px solid #bfdbfe', color: '#1e40af' }}
          >
            <span className="font-medium">Fidelity</span>
            {' · '}positions are cached from your last upload — no need to re-upload every visit.
            Use Update Portfolio above to refresh.
          </div>
        )}

        {showFidelityWizard && (
          <FidelityUpdateWizard
            defaultAccountId={brokerAccounts.find((a) => a.source === 'fidelity')?.account_id ?? ''}
            onClose={() => setShowFidelityWizard(false)}
            // A fresh commit only writes quantity/avg_cost — it isn't priced
            // and no snapshot exists for it yet. updatePortfolio() runs the
            // real pricing + snapshot pass (skipping IBKR-sourced rows) and
            // then reloads the summary, so the tab actually reflects the
            // upload instead of showing stale/unpriced numbers.
            onComplete={updatePortfolio}
          />
        )}

        {showCashFlowModal && (
          <CashFlowModal
            defaultAccountId={brokerAccountId ?? ''}
            onClose={() => setShowCashFlowModal(false)}
            onSaved={() => { loadSummary(); loadAnalytics() }}
          />
        )}

        {showSettings && (
          <AnalystSettingsModal onClose={() => setShowSettings(false)} />
        )}

        <NewTickerPrompt symbols={promptSymbols} />

        {/* Error bar */}
        {error && (
          <div
            className="mb-3 px-4 py-2.5 rounded-lg text-sm animate-fade-in"
            style={{ backgroundColor: '#fef2f2', border: '1px solid #fecaca', color: '#dc2626' }}
          >
            {error}
          </div>
        )}

        {selectedTab === 'trades' ? (
          <div className="flex-1 -mx-6">
            <Trades />
          </div>
        ) : selectedTab === 'ironbeam' ? (
          <div
            className="flex-1 flex flex-col items-center justify-center gap-3 rounded-xl pb-6 mb-6 animate-fade-in-up"
            style={{ backgroundColor: '#ffffff', border: '1px dashed #d0dce8', color: '#9ca3af' }}
          >
            <Construction size={32} strokeWidth={1.5} />
            <p className="text-sm font-medium" style={{ color: '#6b7a99' }}>IronBeam integration coming soon</p>
            <p className="text-xs max-w-sm text-center">
              We don't have an IronBeam connection wired up yet. This tab will show positions,
              performance, and trade reports once that integration lands.
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-4 pb-6 min-h-0">
            {/* Stats row — replaces the old per-broker status banner */}
            <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))' }}>
              <MetricCard
                label="Portfolio Value"
                value={loading ? '...' : (fmt$(portfolioValue != null ? Number(portfolioValue) : null) ?? '—')}
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
                    ? `${metrics.benchmark_symbol}: ${fmtPct(Number(metrics.spy_return_pct))}`
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
                label="Std Dev (Annual)"
                value={
                  chartLoading
                    ? '...'
                    : fmtNum(metrics?.std_dev_annualized != null ? Number(metrics.std_dev_annualized) : null, 2, '%') ?? '—'
                }
              />
            </div>

            {/* Big performance graph, full width */}
            <Card
              title="Performance Graph"
              delay={0}
              action={<span className="text-xs" style={{ color: '#9ca3af' }}>Portfolio · My stocks (TWR) · {metrics?.benchmark_symbol ?? 'SPY'}</span>}
            >
              <div style={{ height: 360 }}>
                {chartLoading ? (
                  <div className="h-full flex items-center justify-center text-sm" style={{ color: '#9ca3af' }}>
                    Loading...
                  </div>
                ) : (
                  <PerformanceChart
                    data={performance}
                    benchmarkSymbol={metrics?.benchmark_symbol}
                  />
                )}
              </div>
            </Card>

            <Card
              title="Factor Analysis"
              delay={80}
              action={
                <span className="text-xs" style={{ color: '#9ca3af' }}>
                  Regression beta · top holdings
                </span>
              }
            >
              <ErrorBoundary label="Factor analysis">
                <FactorAnalysisPanel
                  period={period}
                  accountId={brokerAccountId}
                  defaultBenchmark={metrics?.benchmark_symbol ?? 'SPY'}
                  refreshSignal={summary?.as_of}
                />
              </ErrorBoundary>
            </Card>

            {/* Current positions, full width, cash pinned + highlighted inside PositionsTable */}
            <Card
              title="Current Positions"
              delay={120}
              action={
                <span className="text-xs font-normal" style={{ color: '#9ca3af' }}>
                  {filteredPositions.filter((p) => !['CASH', 'XXCASH', 'SPAXX', 'FDRXX', 'FCASH'].includes(p.symbol.trim().toUpperCase().replace(/\*+$/, ''))).length} open
                </span>
              }
            >
              {loading ? (
                <div className="text-sm" style={{ color: '#9ca3af' }}>Loading...</div>
              ) : (
                <PositionsTable
                  positions={filteredPositions}
                  onLabelChange={(accountId, symbol, label) => {
                    setSummary((prev) => {
                      if (!prev) return prev
                      return {
                        ...prev,
                        positions: prev.positions.map((p) =>
                          p.account_id === accountId && p.symbol === symbol
                            ? { ...p, label }
                            : p,
                        ),
                      }
                    })
                  }}
                />
              )}
            </Card>
          </div>
        )}
      </div>
    </div>
  )
}

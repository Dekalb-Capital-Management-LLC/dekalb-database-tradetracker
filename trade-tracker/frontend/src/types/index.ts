export interface AccountSummary {
  account_id: string
  source: 'ibkr' | 'fidelity'
  total_nav: number | null
  cash_balance: number | null
  equity_value: number | null
  day_pnl: number | null
  day_pnl_pct: number | null
  total_realized_pnl: number | null
  total_unrealized_pnl: number | null
}

export interface PositionSummary {
  symbol: string
  account_id: string
  quantity: number
  avg_cost: number | null
  current_price: number | null
  market_value: number | null
  unrealized_pnl: number | null
  unrealized_pnl_pct: number | null
  label: string | null
}

export interface PortfolioSummary {
  accounts: AccountSummary[]
  combined_nav: number | null
  combined_equity_value: number | null
  combined_day_pnl: number | null
  combined_day_pnl_pct: number | null
  total_realized_pnl: number | null
  total_unrealized_pnl: number | null
  positions: PositionSummary[]
  as_of: string
}

export interface PerformancePoint {
  date: string
  portfolio_nav: number
  portfolio_pct_change: number | null
  spy_pct_change: number | null
  spy_cumulative_pct: number | null
  portfolio_cumulative_pct: number | null
}

export interface PortfolioMetrics {
  period: string
  beta: number | null
  std_dev_annualized: number | null
  sharpe_ratio: number | null
  total_return_pct: number | null
  spy_return_pct: number | null
  alpha: number | null
  max_drawdown_pct: number | null
  win_rate: number | null
  as_of: string
}

export interface Trade {
  id: number
  source: 'ibkr' | 'fidelity'
  account_id: string
  trade_date: string
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  price: number
  commission: number
  gross_amount: number
  net_amount: number
  label: string | null
  is_hedge: boolean
  notes: string | null
  ibkr_order_id: string | null
  fidelity_import_id: number | null
  created_at: string
  updated_at: string
}

export interface FidelityImport {
  import_id: number
  filename: string
  account_id: string | null
  status: string
  row_count: number | null
  success_count: number
  error_count: number
  error_message: string | null
  imported_at: string
}

export type Period = '1m' | '3m' | '6m' | 'ytd' | '1y'

export type TradeLabel = 'event-driven' | 'hedge' | 'long-term' | 'short-term' | 'unclassified'

export interface IBKRStatus {
  enabled: boolean
  mode?: 'disabled' | 'oauth' | 'gateway'
  connected?: boolean
  authenticated?: boolean
  api_url?: string
  account_id?: string
  positions_count?: number
  message?: string
}

export interface IBKRAccount {
  account_id: string
  total_nav: number | null
  cash_balance: number | null
  equity_value: number | null
}

export interface IBKRLivePosition {
  symbol: string
  quantity: number
  market_price: number | null
  market_value: number | null
  avg_cost: number | null
  unrealized_pnl: number | null
}

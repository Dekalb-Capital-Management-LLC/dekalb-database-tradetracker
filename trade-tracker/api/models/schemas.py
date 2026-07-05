"""
Pydantic request/response models for the Trade Tracker API.
Separating DB models (asyncpg rows) from API contracts here.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums / Literals
# ---------------------------------------------------------------------------

TradeSource = Literal["ibkr", "fidelity", "portfolio"]  # "portfolio" is legacy/deprecated — new rows use "fidelity"; kept here so old un-migrated rows still validate
TradeSide = Literal["BUY", "SELL"]
TradeLabel = Literal["event-driven", "hedge", "long-term", "short-term", "unclassified"]


# ---------------------------------------------------------------------------
# Trade models
# ---------------------------------------------------------------------------

class TradeBase(BaseModel):
    source: TradeSource
    account_id: str
    trade_date: datetime
    symbol: str
    side: TradeSide
    quantity: Decimal
    price: Decimal
    commission: Decimal = Decimal("0")
    gross_amount: Decimal
    net_amount: Decimal
    label: Optional[TradeLabel] = None
    is_hedge: bool = False
    notes: Optional[str] = None


class TradeCreate(TradeBase):
    ibkr_order_id: Optional[str] = None
    fidelity_import_id: Optional[int] = None
    raw_data: Optional[dict] = None


class TradeResponse(TradeBase):
    id: int
    ibkr_order_id: Optional[str] = None
    fidelity_import_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TradeLabelUpdate(BaseModel):
    label: TradeLabel
    is_hedge: Optional[bool] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Portfolio / Position models
# ---------------------------------------------------------------------------

class PositionSummary(BaseModel):
    symbol: str
    account_id: str
    quantity: Decimal
    avg_cost: Optional[Decimal]           # calculated from trade history
    current_price: Optional[Decimal]      # from IBKR
    market_value: Optional[Decimal]
    unrealized_pnl: Optional[Decimal]
    unrealized_pnl_pct: Optional[Decimal]
    label: Optional[str] = None           # most common label on related trades


class AccountSummary(BaseModel):
    account_id: str
    source: TradeSource
    total_nav: Optional[Decimal]
    cash_balance: Optional[Decimal]
    equity_value: Optional[Decimal]
    day_pnl: Optional[Decimal]
    day_pnl_pct: Optional[Decimal]
    total_realized_pnl: Optional[Decimal]
    total_unrealized_pnl: Optional[Decimal]


class PortfolioSummary(BaseModel):
    accounts: list[AccountSummary]
    combined_nav: Optional[Decimal]
    combined_equity_value: Optional[Decimal]
    combined_day_pnl: Optional[Decimal]
    combined_day_pnl_pct: Optional[Decimal]
    total_realized_pnl: Optional[Decimal]
    total_unrealized_pnl: Optional[Decimal]
    positions: list[PositionSummary]
    as_of: datetime


# ---------------------------------------------------------------------------
# Portfolio metrics (beta, std dev, NAV history)
# ---------------------------------------------------------------------------

class PerformancePoint(BaseModel):
    date: date
    portfolio_nav: Decimal
    portfolio_pct_change: Optional[Decimal]   # daily % return
    spy_pct_change: Optional[Decimal]         # SPY daily % return (for overlay)
    spy_cumulative_pct: Optional[Decimal]     # cumulative SPY return from period start
    portfolio_cumulative_pct: Optional[Decimal]


class PortfolioMetrics(BaseModel):
    period: str                               # e.g. 'ytd', '1y', '3m'
    beta: Optional[Decimal]                   # vs SPY
    std_dev_annualized: Optional[Decimal]     # annualized daily std dev
    sharpe_ratio: Optional[Decimal]           # simplified: (return - rf) / std_dev
    total_return_pct: Optional[Decimal]
    spy_return_pct: Optional[Decimal]         # benchmark return over same period
    alpha: Optional[Decimal]                  # portfolio return - beta * spy return
    max_drawdown_pct: Optional[Decimal]
    win_rate: Optional[Decimal]               # % of trades that were profitable
    as_of: datetime


# ---------------------------------------------------------------------------
# Snapshot model
# ---------------------------------------------------------------------------

class PortfolioSnapshotResponse(BaseModel):
    id: int
    snapshot_date: date
    account_id: Optional[str]
    total_nav: Decimal
    cash_balance: Optional[Decimal]
    equity_value: Optional[Decimal]
    daily_pnl: Optional[Decimal]
    daily_pnl_pct: Optional[Decimal]
    spy_close: Optional[Decimal]
    spy_daily_pct: Optional[Decimal]
    created_at: datetime


# ---------------------------------------------------------------------------
# Market data models
# ---------------------------------------------------------------------------

class PriceQuote(BaseModel):
    symbol: str
    price: Decimal
    change: Optional[Decimal]               # absolute change vs previous close
    change_pct: Optional[Decimal]
    previous_close: Optional[Decimal]
    source: str                             # 'ibkr' | 'cache'
    as_of: datetime


class HistoricalBar(BaseModel):
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


# ---------------------------------------------------------------------------
# Dashboard compatibility models
# ---------------------------------------------------------------------------

DashboardModuleStatus = Literal["active", "configured", "planned", "disabled"]
DashboardModuleOwner = Literal["equities", "quant", "shared"]


class DashboardCapability(BaseModel):
    key: str
    label: str
    owner: DashboardModuleOwner
    status: DashboardModuleStatus
    description: str
    endpoints: list[str] = Field(default_factory=list)
    data_contracts: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class QuantDashboardConfig(BaseModel):
    compat_enabled: bool
    event_source: str
    postgres_db: str
    questdb_http_url: str
    questdb_ilp_host: str


class DashboardCompatibilityResponse(BaseModel):
    schema_version: str
    dashboard: str
    generated_at: datetime
    modules: list[DashboardCapability]
    extension_points: list[str] = Field(default_factory=list)
    quant_config: QuantDashboardConfig


# ---------------------------------------------------------------------------
# Fidelity import models
# ---------------------------------------------------------------------------

class FidelityImportResponse(BaseModel):
    import_id: int
    filename: str
    account_id: Optional[str]
    status: str
    row_count: Optional[int]
    success_count: int
    error_count: int
    error_message: Optional[str]
    imported_at: datetime


class PositionDiffRow(BaseModel):
    account_id: str
    symbol: str
    old_quantity: Decimal
    new_quantity: Decimal
    delta: Decimal
    avg_cost: Decimal


class ImportCommitPosition(BaseModel):
    account_id: str
    symbol: str
    quantity: Decimal
    avg_cost: Decimal


class ImportPreviewResponse(BaseModel):
    preview_id: str
    account_ids: list[str]                    # all accounts touched by this file (1 for .xlsx, possibly many for a multi-account Fidelity CSV)
    filename: str
    diff: list[PositionDiffRow]               # changed rows only, for display
    positions: list[ImportCommitPosition]     # full new snapshot, echo back (with edits) on commit
    errors: list[str]


class ImportCommitRequest(BaseModel):
    preview_id: str
    positions: list[ImportCommitPosition]


class LatestImportSummary(BaseModel):
    account_id: Optional[str]
    filename: Optional[str]
    imported_at: Optional[datetime]
    position_count: int


# ---------------------------------------------------------------------------
# Cash flows (deposits/withdrawals excluded from performance; dividends/
# interest are real return and stay in)
# ---------------------------------------------------------------------------

CashFlowType = Literal["deposit", "withdrawal", "dividend", "interest"]


class CashFlowCreate(BaseModel):
    account_id: str
    flow_date: datetime
    flow_type: CashFlowType
    amount: Decimal           # positive = inflow, negative = outflow
    source: TradeSource | Literal["manual"] = "manual"
    notes: Optional[str] = None


class CashFlowResponse(CashFlowCreate):
    id: int
    created_at: datetime

-- ============================================================
-- Trade Tracker Schema
-- Canonical definition for all seven application tables. Runtime migrations
-- remain in db.py only to bring existing deployments up to this contract.
-- ============================================================

-- trades: unified trade ledger for both IBKR and Fidelity
-- This is the source of truth for all historical trade data.
-- IBKR trades are either imported via gateway API or CSV export.
-- Fidelity trades come in via CSV upload.
CREATE TABLE IF NOT EXISTS trades (
    id                  BIGSERIAL PRIMARY KEY,
    source              VARCHAR(20)     NOT NULL,           -- 'ibkr' | 'fidelity'
    account_id          VARCHAR(50)     NOT NULL,
    trade_date          TIMESTAMPTZ     NOT NULL,
    symbol              VARCHAR(20)     NOT NULL,
    side                VARCHAR(4)      NOT NULL,           -- 'BUY' | 'SELL'
    quantity            DECIMAL(18, 8)  NOT NULL,
    price               DECIMAL(18, 8)  NOT NULL,
    commission          DECIMAL(10, 4)  NOT NULL DEFAULT 0,
    gross_amount        DECIMAL(18, 2)  NOT NULL,           -- quantity * price
    net_amount          DECIMAL(18, 2)  NOT NULL,           -- gross_amount + commission (signed)
    label               VARCHAR(30),                        -- 'event-driven' | 'hedge' | 'long-term' | 'short-term' | NULL
    is_hedge            BOOLEAN         NOT NULL DEFAULT FALSE,
    notes               TEXT,
    raw_data            JSONB,                              -- original row from CSV or API response
    -- foreign keys (nullable - only set if applicable)
    ibkr_order_id       VARCHAR(50),                        -- links to orders.order_id if source='ibkr'
    fidelity_import_id  BIGINT,                             -- links to fidelity_imports.id if source='fidelity'
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trades_account_date    ON trades(account_id, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_trades_symbol          ON trades(symbol, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_trades_label           ON trades(label) WHERE label IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trades_source          ON trades(source);
CREATE INDEX IF NOT EXISTS idx_trades_ibkr_order      ON trades(ibkr_order_id) WHERE ibkr_order_id IS NOT NULL;


-- portfolio_snapshots: daily NAV snapshots for performance graphing
-- Populated by a nightly job (or on-demand calc).
-- NAV excludes deposits & withdrawals - measures pure investment performance.
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    snapshot_date   DATE            NOT NULL,
    account_id      VARCHAR(50),                        -- NULL = combined portfolio total
    total_nav       DECIMAL(18, 2)  NOT NULL,           -- Net Asset Value
    cash_balance    DECIMAL(18, 2),
    equity_value    DECIMAL(18, 2),                     -- market value of all open positions
    daily_pnl       DECIMAL(18, 2),                     -- absolute P&L vs prior day
    daily_pnl_pct   NUMERIC,                            -- pct vs prior day NAV
    spy_close       DECIMAL(18, 4),                     -- SPY closing price (for overlay calc)
    spy_daily_pct   NUMERIC,                            -- SPY daily % change (for overlay)
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
    -- NOTE: no inline UNIQUE here — PostgreSQL treats NULL != NULL in standard
    -- UNIQUE constraints, so ON CONFLICT (snapshot_date, account_id) silently
    -- never fires for combined rows (account_id IS NULL). Partial indexes below.
);

CREATE INDEX IF NOT EXISTS idx_snapshots_date         ON portfolio_snapshots(snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_account_date ON portfolio_snapshots(account_id, snapshot_date DESC);

-- Unique combined snapshot per day (account_id IS NULL = combined portfolio)
CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_uq_combined
    ON portfolio_snapshots (snapshot_date)
    WHERE account_id IS NULL;

-- Unique per-account snapshot per day
CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_uq_account
    ON portfolio_snapshots (snapshot_date, account_id)
    WHERE account_id IS NOT NULL;


-- fidelity_imports: audit log for CSV uploads
CREATE TABLE IF NOT EXISTS fidelity_imports (
    id              BIGSERIAL PRIMARY KEY,
    filename        VARCHAR(255)    NOT NULL,
    account_id      VARCHAR(50),                        -- extracted from CSV header if present
    source          VARCHAR(20)     NOT NULL DEFAULT 'fidelity', -- 'ibkr' | 'fidelity'
    imported_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    row_count       INTEGER,                            -- number of trade rows parsed
    success_count   INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0,
    status          VARCHAR(20)     NOT NULL DEFAULT 'pending',  -- 'pending' | 'success' | 'partial' | 'error'
    error_message   TEXT,
    raw_csv         TEXT                                -- full CSV content stored for reprocessing
);


-- cash_flows: deposits and withdrawals (excluded from NAV performance calc)
-- Record these so we can properly isolate investment returns.
CREATE TABLE IF NOT EXISTS cash_flows (
    id          BIGSERIAL PRIMARY KEY,
    account_id  VARCHAR(50)     NOT NULL,
    flow_date   TIMESTAMPTZ     NOT NULL,
    flow_type   VARCHAR(20)     NOT NULL,               -- 'deposit' | 'withdrawal' | 'dividend' | 'interest'
    amount      DECIMAL(18, 2)  NOT NULL,               -- positive = inflow, negative = outflow
    source      VARCHAR(20)     NOT NULL,               -- 'ibkr' | 'fidelity' | 'manual'
    notes       TEXT,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cashflows_account_date ON cash_flows(account_id, flow_date DESC);


-- imported_positions: latest holdings imported from Fidelity or IBKR.
-- The portfolio and factor-analysis paths use these values directly.
CREATE TABLE IF NOT EXISTS imported_positions (
    id               SERIAL PRIMARY KEY,
    import_id        INT,
    account_id       VARCHAR(50)  NOT NULL,
    symbol           VARCHAR(20)  NOT NULL,
    quantity         DECIMAL,
    last_price       DECIMAL,
    current_value    DECIMAL,
    today_gain_loss  DECIMAL,
    today_gl_pct     DECIMAL,
    total_gain_loss  DECIMAL,
    total_gl_pct     DECIMAL,
    cost_basis_total DECIMAL,
    avg_cost         DECIMAL,
    source           VARCHAR(20)  NOT NULL DEFAULT 'fidelity',
    snapshot_date    DATE         NOT NULL DEFAULT CURRENT_DATE,
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (account_id, symbol)
);


-- ibkr_tokens: persisted OAuth state for the hosted IBKR integration.
CREATE TABLE IF NOT EXISTS ibkr_tokens (
    id            INTEGER      PRIMARY KEY DEFAULT 1,
    access_token  TEXT         NOT NULL,
    refresh_token TEXT,
    token_type    VARCHAR(50)  NOT NULL DEFAULT 'Bearer',
    expires_at    TIMESTAMPTZ,
    account_id    VARCHAR(50),
    scope         TEXT,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);


-- instrument_conids: persistent symbol-to-IBKR contract ID cache.
CREATE TABLE IF NOT EXISTS instrument_conids (
    symbol       VARCHAR(20) PRIMARY KEY,
    conid        BIGINT      NOT NULL,
    description  TEXT,
    asset_class  VARCHAR(16),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_instrument_conids_conid ON instrument_conids(conid);


-- trigger to keep trades.updated_at current
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trades_updated_at ON trades;
CREATE TRIGGER trades_updated_at
    BEFORE UPDATE ON trades
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

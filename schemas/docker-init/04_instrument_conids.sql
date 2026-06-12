-- Symbol → IBKR contract ID mapping.
-- Populated from IBKR Activity Statement "Financial Instrument Information"
-- section on import, and from /trsrv/stocks lookups at query time.
CREATE TABLE IF NOT EXISTS instrument_conids (
    symbol       VARCHAR(20) PRIMARY KEY,
    conid        BIGINT      NOT NULL,
    description  TEXT,
    asset_class  VARCHAR(16),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_instrument_conids_conid
    ON instrument_conids(conid);

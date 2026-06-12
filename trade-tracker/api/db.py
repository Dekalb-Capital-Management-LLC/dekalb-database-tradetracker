import asyncpg
import logging
import os
import ssl

import config

logger = logging.getLogger(__name__)
_pool: asyncpg.Pool | None = None
_SCHEMA_FILE = "/etc/schemas/trade_tracker_schema.sql"


def _pool_kwargs() -> dict:
    kwargs: dict = {
        "host": config.DB_HOST,
        "port": config.POSTGRES_PORT,
        "database": config.POSTGRES_DB,
        "user": config.POSTGRES_USER,
        "password": config.POSTGRES_PASSWORD,
    }
    if config.DB_SSL != "disable":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl"] = ctx
    return kwargs


async def _ensure_db_exists() -> None:
    logger.warning("Database '%s' not found — creating it now...", config.POSTGRES_DB)
    conn = await asyncpg.connect(**{**_pool_kwargs(), "database": "postgres"})
    try:
        await conn.execute(f'CREATE DATABASE "{config.POSTGRES_DB}"')
        logger.info("Created database '%s'", config.POSTGRES_DB)
    finally:
        await conn.close()
    if os.path.exists(_SCHEMA_FILE):
        schema_conn = await asyncpg.connect(**_pool_kwargs())
        try:
            with open(_SCHEMA_FILE) as f:
                ddl = f.read()
            await schema_conn.execute(ddl)
            logger.info("Schema applied to '%s'", config.POSTGRES_DB)
        finally:
            await schema_conn.close()
    else:
        logger.warning("Schema file not found at %s", _SCHEMA_FILE)


async def _apply_schema_if_empty(conn: asyncpg.Connection) -> None:
    count = await conn.fetchval(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
    )
    if count == 0 and os.path.exists(_SCHEMA_FILE):
        logger.warning("Database '%s' is empty — applying schema...", config.POSTGRES_DB)
        with open(_SCHEMA_FILE) as f:
            ddl = f.read()
        await conn.execute(ddl)
        logger.info("Schema applied to '%s'", config.POSTGRES_DB)


async def _apply_migrations(conn: asyncpg.Connection) -> None:
    """
    Idempotent migrations for schema changes added after initial deployment.
    Safe to run on every startup.
    """
    # ibkr_tokens: stores OAuth 2.0 tokens for IBKR Web API (added for hosted deployment)
    await conn.execute("""
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
        )
    """)

    # fidelity_imports.source: distinguish Fidelity vs IBKR history CSV uploads
    await conn.execute("""
        ALTER TABLE fidelity_imports
        ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'fidelity'
    """)

    # instrument_conids: persistent symbol → IBKR contract ID cache
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS instrument_conids (
            symbol       VARCHAR(20) PRIMARY KEY,
            conid        BIGINT      NOT NULL,
            description  TEXT,
            asset_class  VARCHAR(16),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_instrument_conids_conid ON instrument_conids(conid)"
    )

    # imported_positions: direct position snapshot from Fidelity/IBKR files.
    # Stores the exact values from the file so portfolio view doesn't need to
    # recompute from trades × live prices.
    await conn.execute("""
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
        )
    """)

    # Widen daily_pnl_pct and spy_daily_pct from DECIMAL(10,6) to NUMERIC so
    # large % swings (first snapshot vs tiny prev_nav) don't overflow the column.
    for col in ("daily_pnl_pct", "spy_daily_pct"):
        await conn.execute(f"""
            ALTER TABLE portfolio_snapshots
            ALTER COLUMN {col} TYPE NUMERIC
        """)

    # Partial unique indexes for portfolio_snapshots ON CONFLICT upserts.
    # These must exist for upsert_snapshot to work correctly. Safe to re-run.
    await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_uq_combined
        ON portfolio_snapshots (snapshot_date)
        WHERE account_id IS NULL
    """)
    await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_uq_account
        ON portfolio_snapshots (snapshot_date, account_id)
        WHERE account_id IS NOT NULL
    """)


async def init_pool() -> None:
    global _pool
    pool_kwargs = {**_pool_kwargs(), "min_size": config.DB_MIN_CONNECTIONS, "max_size": config.DB_MAX_CONNECTIONS}
    try:
        _pool = await asyncpg.create_pool(**pool_kwargs)
    except asyncpg.InvalidCatalogNameError:
        await _ensure_db_exists()
        _pool = await asyncpg.create_pool(**pool_kwargs)
    async with _pool.acquire() as conn:
        await _apply_schema_if_empty(conn)
        await _apply_migrations(conn)


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        logger.info("PostgreSQL pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool has not been initialized")
    return _pool

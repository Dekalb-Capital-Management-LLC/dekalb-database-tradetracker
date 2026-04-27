"""
Database connection pool management.
Uses asyncpg for async PostgreSQL access throughout the FastAPI app.

On startup, if the target database (trade_tracker) doesn't exist yet — e.g.
because Postgres volume was carried over from before the monorepo split — this
module creates it automatically and applies the schema, so the service never
crashes due to a missing database.
"""
import asyncpg
import logging
import os

import config

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

# Schema file is mounted into the container at this path (see docker-compose.yml)
_SCHEMA_FILE = "/etc/schemas/trade_tracker_schema.sql"


async def _ensure_db_exists() -> None:
    """
    Connect to the default 'postgres' database and create trade_tracker if
    it doesn't exist, then apply the schema DDL.
    """
    logger.warning(
        "Database '%s' not found — creating it now...", config.POSTGRES_DB
    )

    # Connect to the maintenance DB (always exists)
    conn = await asyncpg.connect(
        host=config.DB_HOST,
        port=config.POSTGRES_PORT,
        database="postgres",
        user=config.POSTGRES_USER,
        password=config.POSTGRES_PASSWORD,
    )
    try:
        await conn.execute(f'CREATE DATABASE "{config.POSTGRES_DB}"')
        logger.info("Created database '%s'", config.POSTGRES_DB)
    finally:
        await conn.close()

    # Now apply the schema to the new database
    if os.path.exists(_SCHEMA_FILE):
        schema_conn = await asyncpg.connect(
            host=config.DB_HOST,
            port=config.POSTGRES_PORT,
            database=config.POSTGRES_DB,
            user=config.POSTGRES_USER,
            password=config.POSTGRES_PASSWORD,
        )
        try:
            with open(_SCHEMA_FILE) as f:
                ddl = f.read()
            await schema_conn.execute(ddl)
            logger.info("Schema applied to '%s'", config.POSTGRES_DB)
        finally:
            await schema_conn.close()
    else:
        logger.warning(
            "Schema file not found at %s — database created but tables not initialised",
            _SCHEMA_FILE,
        )


async def _apply_schema_if_empty(conn: asyncpg.Connection) -> None:
    """If the database has no tables yet, apply the schema DDL."""
    table_count = await conn.fetchval(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
    )
    if table_count == 0:
        if os.path.exists(_SCHEMA_FILE):
            logger.warning(
                "Database '%s' is empty — applying schema...", config.POSTGRES_DB
            )
            with open(_SCHEMA_FILE) as f:
                ddl = f.read()
            await conn.execute(ddl)
            logger.info("Schema applied to '%s'", config.POSTGRES_DB)
        else:
            logger.warning(
                "Schema file not found at %s — tables not initialised", _SCHEMA_FILE
            )


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
    """Create the connection pool. Called once at application startup."""
    global _pool
    try:
        _pool = await asyncpg.create_pool(
            host=config.DB_HOST,
            port=config.POSTGRES_PORT,
            database=config.POSTGRES_DB,
            user=config.POSTGRES_USER,
            password=config.POSTGRES_PASSWORD,
            min_size=config.DB_MIN_CONNECTIONS,
            max_size=config.DB_MAX_CONNECTIONS,
        )
    except asyncpg.InvalidCatalogNameError:
        # Database doesn't exist yet — create it, then retry
        await _ensure_db_exists()
        _pool = await asyncpg.create_pool(
            host=config.DB_HOST,
            port=config.POSTGRES_PORT,
            database=config.POSTGRES_DB,
            user=config.POSTGRES_USER,
            password=config.POSTGRES_PASSWORD,
            min_size=config.DB_MIN_CONNECTIONS,
            max_size=config.DB_MAX_CONNECTIONS,
        )

    # Also handle the case where DB exists but schema was never applied
    async with _pool.acquire() as conn:
        await _apply_schema_if_empty(conn)
        await _apply_migrations(conn)


async def close_pool() -> None:
    """Close the connection pool. Called at application shutdown."""
    global _pool
    if _pool:
        await _pool.close()
        logger.info("PostgreSQL pool closed")


def get_pool() -> asyncpg.Pool:
    """Return the active pool. Raises if init_pool() was never called."""
    if _pool is None:
        raise RuntimeError("Database pool has not been initialized")
    return _pool

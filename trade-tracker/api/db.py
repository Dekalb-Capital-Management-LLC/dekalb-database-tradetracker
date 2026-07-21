import asyncio
import logging
from collections.abc import Iterable
from pathlib import Path

import asyncpg

import config

logger = logging.getLogger(__name__)
_pool: asyncpg.Pool | None = None
_SCHEMA_FILES = (
    Path("/etc/schemas/trade_tracker_schema.sql"),
    Path(__file__).resolve().parent / "schemas" / "trade_tracker_schema.sql",
)
REQUIRED_TABLES = frozenset(
    {
        "cash_flows",
        "fidelity_imports",
        "ibkr_tokens",
        "imported_positions",
        "instrument_conids",
        "portfolio_snapshots",
        "trades",
    }
)


def _pool_kwargs(database: str | None = None) -> dict:
    if config.DATABASE_URL:
        # Preserve the DSN so asyncpg handles URL-escaped credentials and any
        # supported connection parameters supplied by Railway.
        kwargs: dict = {"dsn": config.DATABASE_URL}
    else:
        kwargs = {
            "host": config.DB_HOST,
            "port": config.POSTGRES_PORT,
            "database": config.POSTGRES_DB,
            "user": config.POSTGRES_USER,
            "password": config.POSTGRES_PASSWORD,
        }
    if database is not None:
        kwargs["database"] = database
    kwargs["ssl"] = config.DB_SSL
    return kwargs


def _schema_file() -> Path:
    for path in _SCHEMA_FILES:
        if path.exists():
            return path
    searched = ", ".join(str(path) for path in _SCHEMA_FILES)
    raise RuntimeError(f"Trade Tracker schema file not found; searched: {searched}")


async def _ensure_db_exists() -> None:
    logger.warning("Database '%s' not found; creating it now...", config.POSTGRES_DB)
    conn = await asyncpg.connect(**_pool_kwargs(database="postgres"))
    try:
        database_name = config.POSTGRES_DB.replace('"', '""')
        await conn.execute(f'CREATE DATABASE "{database_name}"')
        logger.info("Created database '%s'", config.POSTGRES_DB)
    finally:
        await conn.close()


async def _apply_canonical_schema_if_needed(conn: asyncpg.Connection) -> None:
    rows = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = ANY($1::text[])
        """,
        sorted(REQUIRED_TABLES),
    )
    missing = missing_required_tables(row["table_name"] for row in rows)
    if not missing:
        return

    logger.warning(
        "Database '%s' is missing required tables (%s); applying the canonical schema...",
        config.POSTGRES_DB,
        ", ".join(missing),
    )
    with _schema_file().open(encoding="utf-8") as schema_file:
        await conn.execute(schema_file.read())
    logger.info("Schema applied to '%s'", config.POSTGRES_DB)


async def _apply_migrations(conn: asyncpg.Connection) -> None:
    """Apply idempotent compatibility migrations on every startup."""
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

    await conn.execute("""
        ALTER TABLE fidelity_imports
        ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'fidelity'
    """)

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

    # NUMERIC avoids overflow when a first snapshot follows a very small NAV.
    for column in ("daily_pnl_pct", "spy_daily_pct"):
        await conn.execute(f"""
            ALTER TABLE portfolio_snapshots
            ALTER COLUMN {column} TYPE NUMERIC
        """)

    # Partial indexes make combined and per-account snapshot upserts reliable.
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


def missing_required_tables(existing_tables: Iterable[str]) -> list[str]:
    return sorted(REQUIRED_TABLES.difference(existing_tables))


async def database_readiness(pool: asyncpg.Pool | None = None) -> dict:
    """Return a credential-safe connectivity and schema readiness snapshot."""
    try:
        active_pool = pool or get_pool()
    except RuntimeError as exc:
        return {
            "ready": False,
            "connected": False,
            "schema": "unknown",
            "missing_tables": [],
            "error": type(exc).__name__,
        }

    try:
        await active_pool.fetchval("SELECT 1")
        rows = await active_pool.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = ANY($1::text[])
            """,
            sorted(REQUIRED_TABLES),
        )
    except Exception as exc:
        return {
            "ready": False,
            "connected": False,
            "schema": "unknown",
            "missing_tables": [],
            "error": type(exc).__name__,
        }

    missing = missing_required_tables(row["table_name"] for row in rows)
    return {
        "ready": not missing,
        "connected": True,
        "schema": "ready" if not missing else "incomplete",
        "missing_tables": missing,
    }


async def _create_pool_with_retry(pool_kwargs: dict) -> asyncpg.Pool:
    for attempt in range(1, config.DB_CONNECT_RETRIES + 1):
        try:
            try:
                return await asyncpg.create_pool(**pool_kwargs)
            except asyncpg.InvalidCatalogNameError:
                await _ensure_db_exists()
                return await asyncpg.create_pool(**pool_kwargs)
        except Exception as exc:
            if attempt == config.DB_CONNECT_RETRIES:
                logger.error(
                    "Database connection failed after %s attempts (%s)",
                    attempt,
                    type(exc).__name__,
                )
                raise
            logger.warning(
                "Database connection attempt %s/%s failed (%s); retrying in %.1fs",
                attempt,
                config.DB_CONNECT_RETRIES,
                type(exc).__name__,
                config.DB_CONNECT_RETRY_SECONDS,
            )
            await asyncio.sleep(config.DB_CONNECT_RETRY_SECONDS)

    raise RuntimeError("Database connection retry loop exited unexpectedly")


async def init_pool() -> None:
    global _pool
    if _pool is not None:
        return

    pool_kwargs = {
        **_pool_kwargs(),
        "min_size": config.DB_MIN_CONNECTIONS,
        "max_size": config.DB_MAX_CONNECTIONS,
    }
    new_pool = await _create_pool_with_retry(pool_kwargs)
    try:
        async with new_pool.acquire() as conn:
            await _apply_canonical_schema_if_needed(conn)
            await _apply_migrations(conn)
        readiness = await database_readiness(new_pool)
        if not readiness["ready"]:
            missing = ", ".join(readiness["missing_tables"]) or "unknown"
            raise RuntimeError(f"Database schema is incomplete; missing tables: {missing}")
    except Exception:
        await new_pool.close()
        raise

    _pool = new_pool
    logger.info(
        "PostgreSQL pool ready for database '%s' (%s required tables)",
        config.POSTGRES_DB,
        len(REQUIRED_TABLES),
    )


async def close_pool() -> None:
    global _pool
    active_pool, _pool = _pool, None
    if active_pool:
        await active_pool.close()
        logger.info("PostgreSQL pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool has not been initialized")
    return _pool

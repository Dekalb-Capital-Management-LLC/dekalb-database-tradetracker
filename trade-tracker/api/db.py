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
    logger.info("PostgreSQL pool created: %s:%s/%s", config.DB_HOST, config.POSTGRES_PORT, config.POSTGRES_DB)


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        logger.info("PostgreSQL pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool has not been initialized")
    return _pool

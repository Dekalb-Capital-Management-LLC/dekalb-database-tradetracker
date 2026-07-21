import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "trade-tracker" / "api"
sys.path.insert(0, str(API_DIR))

import config  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402


class FakePool:
    def __init__(self, tables=(), error=None):
        self.tables = tables
        self.error = error

    async def fetchval(self, _query):
        if self.error:
            raise self.error
        return 1

    async def fetch(self, _query, _required_tables):
        if self.error:
            raise self.error
        return [{"table_name": table} for table in self.tables]


class DatabaseRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_database_url = config.DATABASE_URL
        self.original_db_ssl = config.DB_SSL
        self.original_retries = config.DB_CONNECT_RETRIES
        self.original_retry_seconds = config.DB_CONNECT_RETRY_SECONDS
        self.addCleanup(self._restore_config)

    def _restore_config(self):
        config.DATABASE_URL = self.original_database_url
        config.DB_SSL = self.original_db_ssl
        config.DB_CONNECT_RETRIES = self.original_retries
        config.DB_CONNECT_RETRY_SECONDS = self.original_retry_seconds

    def test_pool_kwargs_preserve_railway_dsn(self):
        dsn = "postgresql://user:p%40ss@postgres.railway.internal:5432/railway"
        config.DATABASE_URL = dsn
        config.DB_SSL = "require"

        kwargs = db._pool_kwargs()

        self.assertEqual(kwargs, {"dsn": dsn, "ssl": "require"})
        self.assertNotIn("password", kwargs)

    async def test_readiness_requires_every_canonical_table(self):
        tables = db.REQUIRED_TABLES.difference({"imported_positions"})

        result = await db.database_readiness(FakePool(tables))

        self.assertFalse(result["ready"])
        self.assertTrue(result["connected"])
        self.assertEqual(result["schema"], "incomplete")
        self.assertEqual(result["missing_tables"], ["imported_positions"])

    async def test_readiness_reports_connection_failure_without_details(self):
        result = await db.database_readiness(FakePool(error=OSError("secret host")))

        self.assertFalse(result["ready"])
        self.assertFalse(result["connected"])
        self.assertEqual(result["error"], "OSError")
        self.assertNotIn("secret host", str(result))

    async def test_pool_creation_retries_transient_startup_failure(self):
        config.DB_CONNECT_RETRIES = 2
        config.DB_CONNECT_RETRY_SECONDS = 0
        expected_pool = object()
        create_pool = AsyncMock(side_effect=[OSError("not ready"), expected_pool])

        with patch.object(db.asyncpg, "create_pool", create_pool):
            result = await db._create_pool_with_retry({"dsn": "postgresql://example"})

        self.assertIs(result, expected_pool)
        self.assertEqual(create_pool.await_count, 2)


class SchemaContractTests(unittest.TestCase):
    def test_host_runtime_falls_back_to_bundled_schema(self):
        self.assertEqual(
            db._schema_file(),
            API_DIR / "schemas" / "trade_tracker_schema.sql",
        )

    def test_canonical_and_bundled_schemas_match(self):
        canonical = (ROOT / "schemas" / "trade_tracker_schema.sql").read_text(encoding="utf-8")
        bundled = (API_DIR / "schemas" / "trade_tracker_schema.sql").read_text(encoding="utf-8")

        self.assertEqual(canonical, bundled)

    def test_schema_defines_all_required_tables(self):
        schema = (ROOT / "schemas" / "trade_tracker_schema.sql").read_text(encoding="utf-8")

        for table in db.REQUIRED_TABLES:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table} (", schema)


class HealthEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_readiness_returns_503_for_degraded_database(self):
        database = {
            "ready": False,
            "connected": False,
            "schema": "unknown",
            "missing_tables": [],
        }

        with patch.object(main.db, "database_readiness", AsyncMock(return_value=database)):
            response = await main.readiness()

        self.assertEqual(response.status_code, 503)

    async def test_readiness_returns_200_for_ready_database(self):
        database = {
            "ready": True,
            "connected": True,
            "schema": "ready",
            "missing_tables": [],
        }

        with patch.object(main.db, "database_readiness", AsyncMock(return_value=database)):
            response = await main.readiness()

        self.assertEqual(response.status_code, 200)

    def test_readiness_bypasses_auth_for_railway(self):
        self.assertIn("/health/ready", main.AuthMiddleware._BYPASS_PATHS)


if __name__ == "__main__":
    unittest.main()

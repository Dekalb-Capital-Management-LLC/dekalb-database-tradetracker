import asyncio
import sys
import types
import unittest
from datetime import date
from decimal import Decimal
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


API_DIR = Path(__file__).resolve().parents[1] / "trade-tracker" / "api"
sys.path.insert(0, str(API_DIR))

if find_spec("asyncpg") is None:
    asyncpg_stub = types.ModuleType("asyncpg")
    asyncpg_stub.Pool = object
    asyncpg_stub.Record = dict
    sys.modules["asyncpg"] = asyncpg_stub

if find_spec("pydantic") is None:
    models_stub = types.ModuleType("models")
    schemas_stub = types.ModuleType("models.schemas")
    schemas_stub.PerformancePoint = object
    schemas_stub.PortfolioMetrics = object
    sys.modules["models"] = models_stub
    sys.modules["models.schemas"] = schemas_stub

from services import portfolio_metrics  # noqa: E402


class PortfolioBetaTests(unittest.TestCase):
    def test_beta_uses_regression_slope(self):
        benchmark = [0.01, 0.02, -0.01, 0.00, 0.03]
        portfolio = [0.002 + 1.5 * value for value in benchmark]

        self.assertAlmostEqual(
            portfolio_metrics._beta(portfolio, benchmark),
            1.5,
            places=12,
        )

    def test_beta_returns_none_for_flat_benchmark(self):
        self.assertIsNone(portfolio_metrics._beta([0.01, 0.02], [0.0, 0.0]))

    def test_beta_rejects_non_finite_returns(self):
        self.assertIsNone(
            portfolio_metrics._beta([0.01, float("nan")], [0.02, 0.03])
        )

    def test_beta_pairs_returns_within_the_same_performance_point(self):
        points = [
            SimpleNamespace(portfolio_pct_change=None, spy_pct_change=None),
            SimpleNamespace(
                portfolio_pct_change=Decimal("2.0"), spy_pct_change=Decimal("1.0")
            ),
            SimpleNamespace(portfolio_pct_change=Decimal("3.0"), spy_pct_change=None),
            SimpleNamespace(portfolio_pct_change=None, spy_pct_change=Decimal("2.0")),
            SimpleNamespace(
                portfolio_pct_change=Decimal("4.0"), spy_pct_change=Decimal("2.0")
            ),
            SimpleNamespace(portfolio_pct_change=float("inf"), spy_pct_change=3.0),
        ]

        portfolio, benchmark = portfolio_metrics._paired_beta_returns(points)

        self.assertEqual(portfolio, [0.02, 0.04])
        self.assertEqual(benchmark, [0.01, 0.02])

    def test_snapshot_uses_configured_benchmark(self):
        class Pool:
            async def execute(self, *args, **kwargs):
                return None

        with (
            patch.object(portfolio_metrics.config, "BENCHMARK_SYMBOL", "QQQ"),
            patch.object(portfolio_metrics, "get_historical_bars", return_value=[]) as history,
        ):
            asyncio.run(
                portfolio_metrics.upsert_snapshot(
                    Pool(),
                    date(2026, 7, 15),
                    Decimal("1000"),
                    "TEST",
                )
            )

        history.assert_called_once_with("QQQ", date(2026, 7, 15), date(2026, 7, 15))


if __name__ == "__main__":
    unittest.main()

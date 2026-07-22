import asyncio
import sys
import types
import unittest
from datetime import date, timedelta
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
    class SchemaObject:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    schemas_stub.FactorAnalysis = SchemaObject
    schemas_stub.FactorSeries = SchemaObject
    schemas_stub.PerformancePoint = SchemaObject
    schemas_stub.PortfolioMetrics = SchemaObject
    schemas_stub.PositionSummary = SchemaObject
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

    def test_daily_returns_keep_prior_close_as_period_baseline(self):
        bars = [
            SimpleNamespace(date=date(2026, 7, 10), close=Decimal("100")),
            SimpleNamespace(date=date(2026, 7, 11), close=Decimal("110")),
            SimpleNamespace(date=date(2026, 7, 12), close=Decimal("99")),
        ]

        returns = portfolio_metrics._daily_returns_by_date(
            bars,
            date(2026, 7, 11),
            date(2026, 7, 12),
        )

        self.assertAlmostEqual(returns[date(2026, 7, 11)], 0.1)
        self.assertAlmostEqual(returns[date(2026, 7, 12)], -0.1)

    def test_correlation_pairs_only_matching_dates(self):
        left = {
            date(2026, 7, 10): 0.01,
            date(2026, 7, 11): 0.02,
            date(2026, 7, 12): 0.03,
        }
        right = {
            date(2026, 7, 10): -0.01,
            date(2026, 7, 12): -0.03,
            date(2026, 7, 13): -0.04,
        }

        value, observations = portfolio_metrics._correlation(left, right)

        self.assertEqual(observations, 2)
        self.assertAlmostEqual(value, -1.0)

    def test_factor_analysis_returns_beta_matrix_and_position_weights(self):
        end = date.today()
        baseline = end - timedelta(days=4)
        dates = [end - timedelta(days=3), end - timedelta(days=2), end - timedelta(days=1)]
        benchmark_closes = [100.0, 101.0, 99.99, 101.9898]
        benchmark_bars = [
            SimpleNamespace(date=bar_date, close=close)
            for bar_date, close in zip([baseline, *dates], benchmark_closes)
        ]
        position_bars = [
            SimpleNamespace(date=bar_date, close=close)
            for bar_date, close in zip([baseline, *dates], [50.0, 51.0, 50.0, 52.0])
        ]
        points = [
            SimpleNamespace(date=dates[0], portfolio_pct_change=Decimal("2.0")),
            SimpleNamespace(date=dates[1], portfolio_pct_change=Decimal("-2.0")),
            SimpleNamespace(date=dates[2], portfolio_pct_change=Decimal("4.0")),
        ]
        positions = [
            SimpleNamespace(
                symbol="AAPL",
                market_value=Decimal("600"),
                current_price=Decimal("60"),
                quantity=Decimal("10"),
            ),
            SimpleNamespace(
                symbol="CASH",
                market_value=Decimal("400"),
                current_price=Decimal("1"),
                quantity=Decimal("400"),
            ),
        ]

        with (
            patch.object(
                portfolio_metrics,
                "get_performance_series",
                return_value=points,
            ),
            patch.object(
                portfolio_metrics,
                "get_historical_bars_batch",
                return_value={"SPY": benchmark_bars, "AAPL": position_bars},
            ),
        ):
            result = asyncio.run(
                portfolio_metrics.calculate_factor_analysis(
                    pool=None,
                    period="1m",
                    account_id="TEST",
                    positions=positions,
                    benchmark_symbol="spy",
                    max_positions=6,
                )
            )

        self.assertEqual(result.benchmark_symbol, "SPY")
        self.assertEqual(result.beta_observations, 3)
        self.assertAlmostEqual(float(result.beta), 2.0, places=4)
        self.assertEqual([item.symbol for item in result.series], ["PORTFOLIO", "SPY", "AAPL"])
        self.assertEqual(result.series[2].portfolio_weight_pct, Decimal("100.0"))
        self.assertEqual(len(result.correlations), 3)
        self.assertEqual(result.correlation_observations[0][1], 3)


if __name__ == "__main__":
    unittest.main()

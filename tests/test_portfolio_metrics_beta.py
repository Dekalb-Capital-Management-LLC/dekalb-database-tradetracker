import sys
import types
import unittest
from importlib.util import find_spec
from pathlib import Path


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

from services import portfolio_metrics


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

    def test_beta_pairs_returns_within_the_same_snapshot_row(self):
        rows = [
            {"snapshot_date": "2026-01-01", "daily_pnl_pct": None, "spy_daily_pct": None},
            {"snapshot_date": "2026-01-02", "daily_pnl_pct": 2.0, "spy_daily_pct": 1.0},
            {"snapshot_date": "2026-01-03", "daily_pnl_pct": 3.0, "spy_daily_pct": None},
            {"snapshot_date": "2026-01-04", "daily_pnl_pct": None, "spy_daily_pct": 2.0},
            {"snapshot_date": "2026-01-05", "daily_pnl_pct": 4.0, "spy_daily_pct": 2.0},
        ]

        portfolio, benchmark = portfolio_metrics._paired_beta_returns(rows)

        self.assertEqual(portfolio, [0.02, 0.04])
        self.assertEqual(benchmark, [0.01, 0.02])


if __name__ == "__main__":
    unittest.main()

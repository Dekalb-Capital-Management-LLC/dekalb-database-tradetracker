import asyncio
import sys
import tempfile
import types
import unittest
from datetime import date
from decimal import Decimal
from importlib.util import find_spec
from pathlib import Path


API_DIR = Path(__file__).resolve().parents[1] / "trade-tracker" / "api"
sys.path.insert(0, str(API_DIR))

if find_spec("yfinance") is None:
    yfinance_stub = types.ModuleType("yfinance")
    yfinance_stub.Ticker = lambda *args, **kwargs: None
    yfinance_stub.download = lambda *args, **kwargs: None
    sys.modules["yfinance"] = yfinance_stub

if find_spec("pydantic") is None:
    class _Model:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    models_stub = types.ModuleType("models")
    schemas_stub = types.ModuleType("models.schemas")
    schemas_stub.HistoricalBar = _Model
    schemas_stub.PriceQuote = _Model
    sys.modules["models"] = models_stub
    sys.modules["models.schemas"] = schemas_stub

import config  # noqa: E402
from services import first_rate_data, market_data  # noqa: E402


DAILY_CSV = """timestamp,open,high,low,close,volume
2026-06-11,728.76,740,724.405,737.76,86330522
2026-06-12,740.71,744.44,735.03,741.75,57079533
2026-06-15,751.85,756.68,751.76,754.83,60176425
"""


class MarketDataFirstRateIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        Path(self.tmp.name, "SPY_1day_sample.csv").write_text(DAILY_CSV, encoding="utf-8")

        self.old_provider = config.MARKET_DATA_PROVIDER
        self.old_path = config.FIRST_RATE_DATA_PATH
        self.old_ibkr_enabled = config.IBKR_ENABLED
        self.addCleanup(self._restore_config)

        config.MARKET_DATA_PROVIDER = "auto"
        config.FIRST_RATE_DATA_PATH = self.tmp.name
        config.IBKR_ENABLED = False
        first_rate_data.reset_provider()
        market_data._price_cache.clear()
        market_data._hist_cache.clear()

    def _restore_config(self):
        config.MARKET_DATA_PROVIDER = self.old_provider
        config.FIRST_RATE_DATA_PATH = self.old_path
        config.IBKR_ENABLED = self.old_ibkr_enabled
        first_rate_data.reset_provider()
        market_data._price_cache.clear()
        market_data._hist_cache.clear()

    def test_historical_bars_prefer_first_rate_data(self):
        bars = market_data.get_historical_bars("SPY", date(2026, 6, 11), date(2026, 6, 15))

        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[-1].close, Decimal("754.83"))

    def test_quote_uses_first_rate_data_source(self):
        quote = asyncio.run(market_data.get_quote(None, "SPY"))

        self.assertIsNotNone(quote)
        self.assertEqual(quote.source, "firstrate")
        self.assertEqual(quote.price, Decimal("754.83"))

    def test_provider_status_reports_first_rate_data(self):
        status = market_data.provider_status()

        self.assertEqual(status["active_provider"], "firstrate")
        self.assertTrue(status["firstrate_configured"])


if __name__ == "__main__":
    unittest.main()

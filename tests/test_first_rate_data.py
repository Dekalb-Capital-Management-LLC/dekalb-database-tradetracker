import sys
import tempfile
import unittest
import zipfile
from datetime import date
from decimal import Decimal
from importlib.util import find_spec
from pathlib import Path


API_DIR = Path(__file__).resolve().parents[1] / "trade-tracker" / "api"
sys.path.insert(0, str(API_DIR))

if find_spec("requests") is None:
    import types

    requests_stub = types.ModuleType("requests")
    sys.modules["requests"] = requests_stub

if find_spec("pydantic") is None:
    import types

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

from services.first_rate_data import FirstRateDataProvider  # noqa: E402


DAILY_CSV = """timestamp,open,high,low,close,volume
2026-06-11,728.76,740,724.405,737.76,86330522
2026-06-12,740.71,744.44,735.03,741.75,57079533
2026-06-15,751.85,756.68,751.76,754.83,60176425
"""


class FirstRateDataProviderTests(unittest.TestCase):
    def test_reads_daily_bars_from_sample_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample_path = Path(tmp) / "SPY_1day_sample.csv"
            sample_path.write_text(DAILY_CSV, encoding="utf-8")

            provider = FirstRateDataProvider(tmp)
            bars = provider.get_historical_bars(
                "SPY",
                date(2026, 6, 12),
                date(2026, 6, 15),
            )

            self.assertEqual([bar.date for bar in bars], [date(2026, 6, 12), date(2026, 6, 15)])
            self.assertEqual(bars[-1].close, Decimal("754.83"))
            self.assertEqual(bars[-1].volume, 60176425)

    def test_reads_daily_bars_from_production_style_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "first_rate_bundle.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("daily/SPY_1day.csv", DAILY_CSV)

            provider = FirstRateDataProvider(zip_path)
            bars = provider.get_historical_bars(
                "spy",
                date(2026, 6, 11),
                date(2026, 6, 15),
            )

            self.assertEqual(len(bars), 3)
            self.assertEqual(bars[0].open, Decimal("728.76"))

    def test_latest_quote_uses_last_daily_close_and_previous_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample_path = Path(tmp) / "SPY_1day_sample.csv"
            sample_path.write_text(DAILY_CSV, encoding="utf-8")

            provider = FirstRateDataProvider(tmp)
            quote = provider.get_latest_quote("spy")

            self.assertIsNotNone(quote)
            self.assertEqual(quote.symbol, "SPY")
            self.assertEqual(quote.source, "firstrate")
            self.assertEqual(quote.price, Decimal("754.83"))
            self.assertEqual(quote.previous_close, Decimal("741.75"))
            self.assertEqual(quote.change, Decimal("13.08"))


if __name__ == "__main__":
    unittest.main()

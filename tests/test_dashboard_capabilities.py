import sys
import types
import unittest
from importlib.util import find_spec
from pathlib import Path


API_DIR = Path(__file__).resolve().parents[1] / "trade-tracker" / "api"
sys.path.insert(0, str(API_DIR))

if find_spec("yfinance") is None:
    yfinance_stub = types.ModuleType("yfinance")
    yfinance_stub.Ticker = lambda *args, **kwargs: None
    yfinance_stub.download = lambda *args, **kwargs: None
    sys.modules["yfinance"] = yfinance_stub

import config  # noqa: E402
from services.dashboard_capabilities import get_dashboard_capabilities  # noqa: E402


class DashboardCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.old_quant_enabled = config.QUANT_DASHBOARD_COMPAT_ENABLED
        self.addCleanup(self._restore_config)

    def _restore_config(self):
        config.QUANT_DASHBOARD_COMPAT_ENABLED = self.old_quant_enabled

    def test_manifest_exposes_stable_dashboard_modules(self):
        config.QUANT_DASHBOARD_COMPAT_ENABLED = False

        manifest = get_dashboard_capabilities()
        modules = {module["key"]: module for module in manifest["modules"]}

        self.assertEqual(manifest["schema_version"], "2026-07-21")
        self.assertEqual(manifest["dashboard"], "trade-tracker")
        self.assertIn("portfolio", modules)
        self.assertIn("market-data", modules)
        self.assertIn("factor-analysis", modules)
        self.assertIn("quant-ingestion", modules)
        self.assertEqual(
            modules["factor-analysis"]["endpoints"],
            ["/portfolio/factor-analysis"],
        )
        self.assertEqual(modules["quant-ingestion"]["status"], "planned")
        self.assertIn("postgres:trading.positions", modules["quant-ingestion"]["data_contracts"])

    def test_quant_compat_flag_marks_manifest_configured(self):
        config.QUANT_DASHBOARD_COMPAT_ENABLED = True

        manifest = get_dashboard_capabilities()
        quant_module = next(module for module in manifest["modules"] if module["key"] == "quant-ingestion")

        self.assertEqual(quant_module["status"], "configured")
        self.assertTrue(manifest["quant_config"]["compat_enabled"])


if __name__ == "__main__":
    unittest.main()

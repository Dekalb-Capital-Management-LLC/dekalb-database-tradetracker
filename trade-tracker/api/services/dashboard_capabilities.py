"""Read-only capability manifest for dashboard integrations."""
from __future__ import annotations

from datetime import datetime, timezone

import config
from services import market_data

SCHEMA_VERSION = "2026-07-21"


def _now_iso() -> datetime:
    return datetime.now(timezone.utc)


def get_dashboard_capabilities() -> dict:
    """Return a stable frontend contract for current and planned dashboard modules."""
    market_status = market_data.provider_status()
    quant_status = "configured" if config.QUANT_DASHBOARD_COMPAT_ENABLED else "planned"

    return {
        "schema_version": SCHEMA_VERSION,
        "dashboard": "trade-tracker",
        "generated_at": _now_iso(),
        "modules": [
            {
                "key": "portfolio",
                "label": "Portfolio",
                "owner": "equities",
                "status": "active",
                "description": "Trade Tracker portfolio summary, positions, performance, and metrics.",
                "endpoints": [
                    "/portfolio/summary",
                    "/portfolio/positions",
                    "/portfolio/performance",
                    "/portfolio/metrics",
                ],
                "data_contracts": [
                    "trade_tracker.imported_positions",
                    "trade_tracker.trades",
                    "trade_tracker.portfolio_snapshots",
                    "trade_tracker.cash_flows",
                ],
            },
            {
                "key": "market-data",
                "label": "Market Data",
                "owner": "shared",
                "status": "active",
                "description": "Provider-ordered quote and history service for dashboard pricing.",
                "endpoints": [
                    "/market/provider/status",
                    "/market/quote/{symbol}",
                    "/market/history/{symbol}",
                    "/market/spy",
                ],
                "data_contracts": [
                    f"provider:{market_status['active_provider']}",
                    f"order:{','.join(market_status['provider_order'])}",
                ],
            },
            {
                "key": "factor-analysis",
                "label": "Factor Analysis",
                "owner": "shared",
                "status": "active",
                "description": (
                    "Configurable benchmark beta and pairwise daily-return correlations "
                    "for the portfolio and its largest positions."
                ),
                "endpoints": ["/portfolio/factor-analysis"],
                "data_contracts": [
                    "FactorAnalysis",
                    "method:ols_slope",
                    "frequency:daily",
                ],
            },
            {
                "key": "quant-ingestion",
                "label": "Quant Ingestion",
                "owner": "quant",
                "status": quant_status,
                "description": (
                    "Compatibility placeholder for orders, executions, positions, logs, "
                    "signals, and tick data produced by the quant ingestion service."
                ),
                "endpoints": [],
                "data_contracts": [
                    f"postgres:{config.QUANT_POSTGRES_DB}.orders",
                    f"postgres:{config.QUANT_POSTGRES_DB}.positions",
                    f"postgres:{config.QUANT_POSTGRES_DB}.accounts",
                    "questdb:executions",
                    "questdb:strategy_signals",
                    "questdb:tick_data",
                ],
                "notes": (
                    "Set QUANT_DASHBOARD_COMPAT_ENABLED=true when dashboard-facing "
                    "quant endpoints are wired behind auth."
                ),
            },
        ],
        "extension_points": [
            "Add quant-facing FastAPI routers under /quant/* without changing existing portfolio endpoints.",
            "Keep dashboard modules keyed by stable module keys instead of broker/provider names.",
            "Expose new quant data through typed API responses before adding visible dashboard panels.",
            "Consume /portfolio/factor-analysis for shared equities and quant risk views.",
        ],
        "quant_config": {
            "compat_enabled": config.QUANT_DASHBOARD_COMPAT_ENABLED,
            "event_source": config.QUANT_EVENT_SOURCE,
            "postgres_db": config.QUANT_POSTGRES_DB,
            "questdb_http_url": config.QUANT_QUESTDB_HTTP_URL,
            "questdb_ilp_host": config.QUANT_QUESTDB_ILP_HOST,
        },
    }

"""
Market data service.

Current implementation: yfinance (free, no auth required).
IBKR path is wired in but gated behind IBKR_ENABLED=true.

When IBKR gateway is ready:
1. Set IBKR_ENABLED=true in env
2. Set IBKR_GATEWAY_URL to your gateway address (default https://localhost:5001)
3. The gateway must be running and authenticated via 2FA
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

import requests
import yfinance as yf

import config
from models.schemas import HistoricalBar, PriceQuote

# Spoof a browser User-Agent — Yahoo Finance blocks plain script/Docker requests
_yf_session = requests.Session()
_yf_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
})

logger = logging.getLogger(__name__)

# Simple in-process TTL cache to avoid hammering yfinance
_price_cache: dict[str, tuple[float, PriceQuote]] = {}  # symbol -> (expires_at, quote)


def _cached_quote(symbol: str) -> Optional[PriceQuote]:
    entry = _price_cache.get(symbol)
    if entry and entry[0] > time.time():
        return entry[1]
    return None


def _store_quote(symbol: str, quote: PriceQuote) -> None:
    expires_at = time.time() + config.PRICE_CACHE_TTL_SECONDS
    _price_cache[symbol] = (expires_at, quote)


# ---------------------------------------------------------------------------
# yfinance implementation
# ---------------------------------------------------------------------------

def _fetch_quote_yfinance(symbol: str) -> Optional[PriceQuote]:
    try:
        ticker = yf.Ticker(symbol, session=_yf_session)
        info = ticker.fast_info  # lighter call than .info
        price = info.last_price
        prev_close = info.previous_close

        if price is None:
            logger.warning("yfinance returned no price for %s", symbol)
            return None

        change = Decimal(str(price)) - Decimal(str(prev_close)) if prev_close else None
        change_pct = (change / Decimal(str(prev_close)) * 100) if (change and prev_close) else None

        quote = PriceQuote(
            symbol=symbol,
            price=Decimal(str(round(price, 4))),
            change=round(change, 4) if change else None,
            change_pct=round(change_pct, 4) if change_pct else None,
            previous_close=Decimal(str(round(prev_close, 4))) if prev_close else None,
            source="yfinance",
            as_of=datetime.utcnow(),
        )
        _store_quote(symbol, quote)
        logger.debug("yfinance price for %s: %s", symbol, price)
        return quote

    except Exception as exc:
        logger.error("yfinance error for %s: %s", symbol, exc)
        return None


def get_historical_bars(
    symbol: str,
    start: date,
    end: date,
    interval: str = "1d",
) -> list[HistoricalBar]:
    """
    Fetch OHLCV bars via yfinance.
    interval: '1d', '1wk', '1mo'  (daily is most useful for portfolio metrics)
    """
    try:
        ticker = yf.Ticker(symbol, session=_yf_session)
        df = ticker.history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),  # yfinance end is exclusive
            interval=interval,
            auto_adjust=True,
        )
        if df.empty:
            logger.warning("No historical data for %s %s-%s", symbol, start, end)
            return []

        bars: list[HistoricalBar] = []
        for ts, row in df.iterrows():
            bars.append(
                HistoricalBar(
                    date=ts.date(),
                    open=Decimal(str(round(row["Open"], 4))),
                    high=Decimal(str(round(row["High"], 4))),
                    low=Decimal(str(round(row["Low"], 4))),
                    close=Decimal(str(round(row["Close"], 4))),
                    volume=int(row["Volume"]),
                )
            )
        logger.info("Fetched %d bars for %s via yfinance", len(bars), symbol)
        return bars

    except Exception as exc:
        logger.error("yfinance historical error for %s: %s", symbol, exc)
        return []


# ---------------------------------------------------------------------------
# IBKR implementation (requires gateway running and authenticated)
# ---------------------------------------------------------------------------

def _fetch_quote_ibkr(symbol: str) -> Optional[PriceQuote]:
    """Fetch a live price quote from the IBKR Client Portal Gateway."""
    from services.ibkr_client import ibkr_client

    conid = ibkr_client.get_conid(symbol)
    if conid is None:
        logger.warning("IBKR: could not find conid for %s, falling back to yfinance", symbol)
        return None

    snap = ibkr_client.get_market_snapshot(conid)
    if snap is None:
        logger.warning("IBKR: no snapshot for %s (conid=%s)", symbol, conid)
        return None

    # Field 31 = last price
    raw_price = snap.get("31")
    if raw_price is None:
        logger.warning("IBKR: snapshot for %s has no price field", symbol)
        return None

    try:
        price = Decimal(str(raw_price))
    except Exception:
        return None

    quote = PriceQuote(
        symbol=symbol,
        price=price,
        change=None,       # snapshot doesn't include change; would need prev close
        change_pct=None,
        previous_close=None,
        source="ibkr",
        as_of=datetime.utcnow(),
    )
    _store_quote(symbol, quote)
    logger.debug("IBKR price for %s: %s", symbol, price)
    return quote


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def get_quote(symbol: str) -> Optional[PriceQuote]:
    """
    Get current price for a symbol.
    Uses IBKR if gateway is enabled, otherwise falls back to yfinance.
    Results are cached for PRICE_CACHE_TTL_SECONDS.
    """
    cached = _cached_quote(symbol)
    if cached:
        logger.debug("Cache hit for %s", symbol)
        return cached

    if config.IBKR_ENABLED:
        quote = _fetch_quote_ibkr(symbol)
        if quote:
            return quote
        logger.warning("IBKR quote failed for %s, falling back to yfinance", symbol)

    return _fetch_quote_yfinance(symbol)


def get_spy_history(start: date, end: date) -> list[HistoricalBar]:
    """Convenience wrapper for SPY benchmark data."""
    return get_historical_bars(config.BENCHMARK_SYMBOL, start, end)

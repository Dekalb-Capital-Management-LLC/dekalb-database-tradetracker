"""
Market data service.

Primary source: IBKR Web API (live prices via market snapshot).
yfinance is used ONLY for historical bars (performance chart, SPY benchmark).
Live price quotes always come from IBKR when connected.
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

# yfinance session with browser User-Agent (only used for historical bars)
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
    """Fetch OHLCV bars via yfinance for a single symbol."""
    try:
        ticker = yf.Ticker(symbol, session=_yf_session)
        df = ticker.history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
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
        return bars

    except Exception as exc:
        logger.error("yfinance historical error for %s: %s", symbol, exc)
        return []


def get_historical_bars_batch(
    symbols: list[str],
    start: date,
    end: date,
) -> dict[str, dict[date, Decimal]]:
    """
    Fetch close prices for multiple symbols in a single yfinance call.
    Returns {symbol: {date: close_price}}.
    This is ~10-50x faster than individual per-symbol calls for large portfolios.
    """
    import pandas as pd

    result: dict[str, dict[date, Decimal]] = {s: {} for s in symbols}
    if not symbols:
        return result

    try:
        df = yf.download(
            symbols,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        if df.empty:
            logger.warning("Batch yfinance returned no data for %s", symbols)
            return result

        if len(symbols) == 1:
            # Single-symbol download: flat column index
            sym = symbols[0]
            close_series = df["Close"] if "Close" in df.columns else None
            if close_series is not None:
                for ts, val in close_series.items():
                    if not pd.isna(val):
                        result[sym][ts.date()] = Decimal(str(round(float(val), 4)))
        else:
            # Multi-symbol download: MultiIndex columns ('Close', 'AAPL'), etc.
            if isinstance(df.columns, pd.MultiIndex):
                if "Close" in df.columns.get_level_values(0):
                    close_df = df["Close"]
                    for sym in symbols:
                        if sym in close_df.columns:
                            for ts, val in close_df[sym].items():
                                if not pd.isna(val):
                                    result[sym][ts.date()] = Decimal(str(round(float(val), 4)))

        total = sum(len(v) for v in result.values())
        logger.info("Batch yfinance: %d symbols, %d price points", len(symbols), total)

    except Exception as exc:
        logger.error("Batch yfinance download failed: %s", exc)

    return result


# ---------------------------------------------------------------------------
# IBKR implementation (placeholder - requires gateway running)
# ---------------------------------------------------------------------------

def _fetch_quote_ibkr(symbol: str) -> Optional[PriceQuote]:
    """Fetch a live price quote from IBKR Web API."""
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

    def _dec(val) -> Optional[Decimal]:
        try:
            return Decimal(str(val)) if val is not None else None
        except Exception:
            return None

    prev_close = _dec(snap.get("7296"))
    change = _dec(snap.get("82"))
    change_pct = _dec(snap.get("83"))

    quote = PriceQuote(
        symbol=symbol,
        price=price,
        change=change,
        change_pct=change_pct,
        previous_close=prev_close,
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
    Priority: cache → IBKR (if enabled) → yfinance fallback.
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

    # Always fall back to yfinance so portfolio values work without IBKR
    return _fetch_quote_yfinance(symbol)


def get_spy_history(start: date, end: date) -> list[HistoricalBar]:
    """Convenience wrapper for SPY benchmark data."""
    return get_historical_bars(config.BENCHMARK_SYMBOL, start, end)

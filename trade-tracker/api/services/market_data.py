"""
Market data service.

Uses FirstRateData when configured, with the existing IBKR/yfinance stack as
fallback for quotes and historical bars.
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import requests
import yfinance as yf

import config
from models.schemas import HistoricalBar, PriceQuote
from services import first_rate_data

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
_hist_cache: dict[tuple, tuple[float, list[HistoricalBar]]] = {}  # cache key -> (expires_at, bars)
_last_yf_request_at: float = 0.0

# Cash-sweep symbols (IBKR's XXCASH, Fidelity's money-market funds) aren't real
# tickers — quoting them via IBKR/yfinance always 404s. Checked here, the lowest
# common layer every quote path (get_quote, warm_quote_cache) funnels through,
# so any caller is covered even if it forgets its own cash-symbol filter.
CASH_SYMBOLS = {"XXCASH", "CASH", "SPAXX", "FDRXX", "FCASH"}
FIRST_RATE_PROVIDER_NAMES = {"auto", "firstrate", "first_rate", "first-rate"}


def is_cash_symbol(symbol: str) -> bool:
    return symbol.strip().upper().rstrip("*") in CASH_SYMBOLS


def _use_first_rate_data() -> bool:
    return config.MARKET_DATA_PROVIDER in FIRST_RATE_PROVIDER_NAMES


def _first_rate_provider() -> first_rate_data.FirstRateDataProvider:
    return first_rate_data.get_provider()


def provider_status() -> dict:
    provider = _first_rate_provider()
    first_rate_configured = _use_first_rate_data() and provider.is_configured
    provider_order = []
    if first_rate_configured:
        provider_order.append("firstrate")
    if config.IBKR_ENABLED:
        provider_order.append("ibkr")
    provider_order.append("yfinance")
    return {
        "mode": config.MARKET_DATA_PROVIDER,
        "active_provider": provider_order[0],
        "provider_order": provider_order,
        "firstrate_configured": first_rate_configured,
        "firstrate_path": str(provider.data_path) if provider.data_path else None,
        "ibkr_enabled": config.IBKR_ENABLED,
        "cache_ttl_seconds": config.PRICE_CACHE_TTL_SECONDS,
        "historical_cache_ttl_seconds": config.HISTORICAL_CACHE_TTL_SECONDS,
    }


def _cash_quote(symbol: str) -> PriceQuote:
    return PriceQuote(
        symbol=symbol.upper(),
        price=Decimal("1"),
        change=None,
        change_pct=None,
        previous_close=None,
        source="cash",
        as_of=datetime.now(timezone.utc),
    )


def _throttle_yfinance() -> None:
    """ponytail: serialise yfinance calls — Yahoo rate-limits burst traffic."""
    global _last_yf_request_at
    delay = config.YFINANCE_REQUEST_DELAY_SECONDS
    elapsed = time.time() - _last_yf_request_at
    if elapsed < delay:
        time.sleep(delay - elapsed)
    _last_yf_request_at = time.time()


def _hist_cache_key(symbol: str, start: date, end: date, interval: str) -> tuple:
    return (symbol.upper(), start.isoformat(), end.isoformat(), interval)


def _cached_bars(key: tuple) -> Optional[list[HistoricalBar]]:
    entry = _hist_cache.get(key)
    if entry and entry[0] > time.time():
        return entry[1]
    return None


def _store_bars(key: tuple, bars: list[HistoricalBar]) -> None:
    _hist_cache[key] = (time.time() + config.HISTORICAL_CACHE_TTL_SECONDS, bars)


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
        _throttle_yfinance()
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
            as_of=datetime.now(timezone.utc),
        )
        _store_quote(symbol, quote)
        logger.debug("yfinance price for %s: %s", symbol, price)
        return quote

    except Exception as exc:
        logger.error("yfinance error for %s: %s", symbol, exc)
        return None


def _fetch_hist_ibkr(
    symbol: str,
    start: date,
    end: date,
    interval: str = "1d",
) -> list[HistoricalBar]:
    """IBKR iserver history — preferred when OAuth session is live."""
    if interval != "1d" or not config.IBKR_ENABLED:
        return []

    from services.ibkr_client import ibkr_client

    if not ibkr_client.is_connected:
        return []

    conid = ibkr_client.get_conid(symbol)
    if conid is None:
        return []

    raw = ibkr_client.get_market_history_bars(conid, start, end, bar="1d")
    bars: list[HistoricalBar] = []
    for row in raw:
        close = row.get("close")
        if close is None:
            continue
        bars.append(
            HistoricalBar(
                date=row["date"],
                open=Decimal(str(round(row["open"] or close, 4))),
                high=Decimal(str(round(row["high"] or close, 4))),
                low=Decimal(str(round(row["low"] or close, 4))),
                close=Decimal(str(round(close, 4))),
                volume=int(row.get("volume") or 0),
            )
        )
    if bars:
        logger.info("Fetched %d bars for %s via IBKR", len(bars), symbol)
    return bars


def _fetch_hist_yfinance(
    symbol: str,
    start: date,
    end: date,
    interval: str = "1d",
) -> list[HistoricalBar]:

    try:
        _throttle_yfinance()
        ticker = yf.Ticker(symbol, session=_yf_session)
        df = ticker.history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            interval=interval,
            auto_adjust=True,
        )
        if df.empty:
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
        if "rate limit" in str(exc).lower():
            time.sleep(config.YFINANCE_REQUEST_DELAY_SECONDS * 3)
        return []


def get_historical_bars(
    symbol: str,
    start: date,
    end: date,
    interval: str = "1d",
) -> list[HistoricalBar]:
    """
    Fetch OHLCV bars from FirstRateData when configured, then use the existing
    yfinance/IBKR fallback order.
    """
    key = _hist_cache_key(symbol, start, end, interval)
    cached = _cached_bars(key)
    if cached is not None:
        return cached

    if _use_first_rate_data():
        provider = _first_rate_provider()
        if provider.is_configured:
            bars = provider.get_historical_bars(symbol, start, end, interval)
            if bars:
                _store_bars(key, bars)
                logger.info("Fetched %d bars for %s via FirstRateData", len(bars), symbol)
                return bars

    yf_bars = _fetch_hist_yfinance(symbol, start, end, interval)
    if yf_bars:
        _store_bars(key, yf_bars)
        logger.info("Fetched %d bars for %s via yfinance", len(yf_bars), symbol)
        return yf_bars

    ibkr_bars = _fetch_hist_ibkr(symbol, start, end, interval)
    if ibkr_bars:
        _store_bars(key, ibkr_bars)
        return ibkr_bars

    _store_bars(key, [])
    logger.warning("No historical data for %s %s-%s", symbol, start, end)
    return []


def get_historical_bars_batch(
    symbols: list[str],
    start: date,
    end: date,
    interval: str = "1d",
) -> dict[str, list[HistoricalBar]]:
    """One yfinance round-trip for many symbols — avoids per-ticker rate limits."""
    symbols = sorted({s.upper() for s in symbols if s})
    if not symbols:
        return {}

    result: dict[str, list[HistoricalBar]] = {}
    missing: list[str] = []
    for sym in symbols:
        key = _hist_cache_key(sym, start, end, interval)
        cached = _cached_bars(key)
        if cached is not None:
            result[sym] = cached
        else:
            missing.append(sym)

    if not missing:
        return result

    if _use_first_rate_data():
        provider = _first_rate_provider()
        if provider.is_configured:
            unresolved: list[str] = []
            for symbol in missing:
                key = _hist_cache_key(symbol, start, end, interval)
                bars = provider.get_historical_bars(symbol, start, end, interval)
                if bars:
                    _store_bars(key, bars)
                    result[symbol] = bars
                else:
                    unresolved.append(symbol)
            missing = unresolved

    if not missing:
        return result

    chunk_size = 5
    for i in range(0, len(missing), chunk_size):
        chunk = missing[i : i + chunk_size]
        _download_hist_chunk(chunk, start, end, interval, result)

    # IBKR fallback for symbols yfinance still missed
    if config.IBKR_ENABLED:
        still_missing = [sym for sym in missing if sym not in result or not result.get(sym)]
        if still_missing:
            with ThreadPoolExecutor(max_workers=min(8, len(still_missing))) as pool:
                future_to_sym = {
                    pool.submit(_fetch_hist_ibkr, sym, start, end, interval): sym
                    for sym in still_missing
                }
                for future in future_to_sym:
                    sym = future_to_sym[future]
                    try:
                        ibkr_bars = future.result()
                    except Exception as exc:
                        logger.warning("IBKR history fetch failed for %s: %s", sym, exc)
                        ibkr_bars = []
                    if ibkr_bars:
                        key = _hist_cache_key(sym, start, end, interval)
                        _store_bars(key, ibkr_bars)
                        result[sym] = ibkr_bars

    return result


def _download_hist_chunk(
    symbols: list[str],
    start: date,
    end: date,
    interval: str,
    result: dict[str, list[HistoricalBar]],
) -> None:
    """Download a small symbol chunk with throttle + per-symbol fallback."""
    for attempt in range(2):
        try:
            _throttle_yfinance()
            df = yf.download(
                symbols,
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),
                interval=interval,
                auto_adjust=True,
                group_by="ticker",
                progress=False,
                session=_yf_session,
            )
            if df.empty:
                raise ValueError("empty dataframe")

            for sym in symbols:
                bars: list[HistoricalBar] = []
                if len(symbols) == 1:
                    sub = df
                else:
                    if sym not in df.columns.get_level_values(0):
                        continue
                    sub = df[sym].dropna(how="all")
                for ts, row in sub.iterrows():
                    if row.isna().all():
                        continue
                    vol = row.get("Volume", 0)
                    bars.append(
                        HistoricalBar(
                            date=ts.date(),
                            open=Decimal(str(round(row["Open"], 4))),
                            high=Decimal(str(round(row["High"], 4))),
                            low=Decimal(str(round(row["Low"], 4))),
                            close=Decimal(str(round(row["Close"], 4))),
                            volume=int(vol) if vol == vol else 0,
                        )
                    )
                key = _hist_cache_key(sym, start, end, interval)
                _store_bars(key, bars)
                result[sym] = bars
            logger.info("Batch fetched history for %d symbols via yfinance", len(symbols))
            return
        except Exception as exc:
            logger.warning("yfinance chunk %s attempt %d: %s", symbols, attempt + 1, exc)
            if "rate limit" in str(exc).lower():
                time.sleep(config.YFINANCE_REQUEST_DELAY_SECONDS * (attempt + 2))

    # Per-symbol fallback when batch fails
    for sym in symbols:
        if sym in result:
            continue
        bars = get_historical_bars(sym, start, end, interval)
        result[sym] = bars


# ---------------------------------------------------------------------------
# IBKR quotes (snapshot batch, then position price fallback)
# ---------------------------------------------------------------------------

def _fetch_quote_ibkr(symbol: str) -> Optional[PriceQuote]:
    """Fetch live price from IBKR; bounded by snapshot poll settings (~4s max)."""
    from services.ibkr_client import ibkr_client

    sym = symbol.upper()
    price: Optional[float] = None

    conid = ibkr_client.get_conid(sym)
    if conid is not None:
        batch = ibkr_client.get_market_snapshot_batch([conid])
        price = batch.get(conid)

    if price is None:
        price = ibkr_client.get_price_from_positions(sym)

    if price is None:
        logger.debug("IBKR: no price for %s (conid=%s)", sym, conid)
        return None

    quote = PriceQuote(
        symbol=sym,
        price=Decimal(str(round(price, 4))),
        change=None,
        change_pct=None,
        previous_close=None,
        source="ibkr",
        as_of=datetime.now(timezone.utc),
    )
    _store_quote(sym, quote)
    logger.debug("quote_source=ibkr symbol=%s price=%s", sym, price)
    return quote


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def _fetch_quote_yfinance_only(symbol: str) -> Optional[PriceQuote]:
    """Live quote via yfinance only — for non-IBKR/Fidelity refresh paths."""
    if is_cash_symbol(symbol):
        return _cash_quote(symbol)
    cached = _cached_quote(symbol.upper())
    if cached and cached.source in ("yfinance", "cash"):
        return cached
    return _fetch_quote_yfinance(symbol)


def _fetch_quote(symbol: str) -> Optional[PriceQuote]:
    """
    Get current price for a symbol (sync).
    Uses FirstRateData when configured, then IBKR/yfinance. Results are cached.
    """
    if is_cash_symbol(symbol):
        return _cash_quote(symbol)

    cached = _cached_quote(symbol)
    if cached:
        logger.debug("Cache hit for %s", symbol)
        return cached

    if _use_first_rate_data():
        provider = _first_rate_provider()
        if provider.is_configured:
            quote = provider.get_latest_quote(symbol)
            if quote:
                _store_quote(symbol.upper(), quote)
                logger.debug("quote_source=firstrate symbol=%s", symbol.upper())
                return quote

    if config.IBKR_ENABLED:
        quote = _fetch_quote_ibkr(symbol)
        if quote:
            return quote
        logger.debug("quote_source=yfinance symbol=%s reason=ibkr_miss", symbol.upper())

    quote = _fetch_quote_yfinance(symbol)
    if quote:
        logger.debug("quote_source=yfinance symbol=%s", symbol.upper())
    return quote


# Async API used by routers (main branch compatibility)
async def warm_yfinance_quote_cache(pool, symbols: list[str]) -> None:
    """Pre-fetch yfinance-only quotes (skips IBKR)."""
    uncached = [s.upper() for s in symbols if s and not _cached_quote(s.upper())]
    if not uncached:
        return

    def _fetch_all():
        for sym in uncached:
            _fetch_quote_yfinance_only(sym)

    await asyncio.to_thread(_fetch_all)


async def warm_quote_cache(pool, symbols: list[str]) -> None:
    """Pre-fetch quotes for many symbols through the shared provider cache.

    Runs on a worker thread: _fetch_quote is synchronous and throttles itself
    with time.sleep between IBKR/yfinance calls, which would otherwise block
    the single asyncio event loop — freezing every other concurrent request
    (every other user's dashboard) for as long as this batch takes.
    """
    uncached = [s.upper() for s in symbols if s and not _cached_quote(s.upper())]
    if not uncached:
        return

    def _fetch_all():
        for sym in uncached:
            _fetch_quote(sym)

    await asyncio.to_thread(_fetch_all)


async def get_yfinance_quote(pool, symbol: str) -> Optional[PriceQuote]:
    """Async yfinance-only quote — avoids IBKR for non-IBKR account rows."""
    sym = symbol.upper()
    cached = _cached_quote(sym)
    if cached and cached.source in ("yfinance", "cash"):
        return cached
    return await asyncio.to_thread(_fetch_quote_yfinance_only, sym)


async def get_quote(pool, symbol: str) -> Optional[PriceQuote]:
    """Async wrapper for routers — pool unused but kept for API compatibility."""
    cached = _cached_quote(symbol.upper())
    if cached:
        return cached
    return await asyncio.to_thread(_fetch_quote, symbol.upper())


async def get_latest_prices(pool, symbols: list[str]) -> tuple[dict[str, float], list[str]]:
    """Return latest prices through the shared provider stack."""
    prices: dict[str, float] = {}
    errors: list[str] = []
    unique_symbols = sorted({symbol.upper() for symbol in symbols if symbol})
    if not unique_symbols:
        return prices, errors

    await warm_quote_cache(pool, unique_symbols)
    for symbol in unique_symbols:
        quote = await get_quote(pool, symbol)
        if quote and quote.price > 0:
            prices[symbol] = float(quote.price)
        else:
            errors.append(f"{symbol}: no market price")
    return prices, errors


def get_spy_history(start: date, end: date) -> list[HistoricalBar]:
    """Compatibility wrapper for the configured benchmark's history."""
    return get_historical_bars(config.BENCHMARK_SYMBOL, start, end)

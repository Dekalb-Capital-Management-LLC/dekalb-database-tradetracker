"""
Market data service — IBKR Web API only.

All live quotes and historical bars come from IBKR's /iserver/marketdata/*
endpoints. Symbols are resolved to conids via the local `instrument_conids`
table (populated from IBKR Activity Statement imports) with a fallback to
/trsrv/stocks for symbols we haven't seen before.

Quotes are cached for PRICE_CACHE_TTL_SECONDS.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import config
from models.schemas import HistoricalBar, PriceQuote

logger = logging.getLogger(__name__)

# In-process TTL cache
_price_cache: dict[str, tuple[float, PriceQuote]] = {}   # symbol -> (expires_at, quote)
_conid_cache: dict[str, int] = {}                        # symbol -> conid (session)


def _cached_quote(symbol: str) -> Optional[PriceQuote]:
    entry = _price_cache.get(symbol)
    if entry and entry[0] > time.time():
        return entry[1]
    return None


def _store_quote(symbol: str, quote: PriceQuote) -> None:
    _price_cache[symbol] = (time.time() + config.PRICE_CACHE_TTL_SECONDS, quote)


# ---------------------------------------------------------------------------
# Symbol → conid resolution
# ---------------------------------------------------------------------------

async def _resolve_conids(pool, symbols: list[str]) -> dict[str, int]:
    """
    Resolve a batch of symbols to IBKR conids.
    Checks process cache, then the persistent instrument_conids table, then
    falls back to /trsrv/stocks for anything still missing (and caches it).
    """
    result: dict[str, int] = {}
    missing: list[str] = []
    for sym in symbols:
        s = sym.upper()
        if s in _conid_cache:
            result[s] = _conid_cache[s]
        else:
            missing.append(s)

    if missing:
        rows = await pool.fetch(
            "SELECT symbol, conid FROM instrument_conids WHERE symbol = ANY($1::text[])",
            missing,
        )
        for r in rows:
            result[r["symbol"]] = r["conid"]
            _conid_cache[r["symbol"]] = r["conid"]
        missing = [s for s in missing if s not in result]

    if missing:
        try:
            from services.ibkr_client import ibkr_client
            if ibkr_client.is_connected():
                looked_up = ibkr_client.get_conids_batch(missing)
                for sym, conid in looked_up.items():
                    result[sym] = conid
                    _conid_cache[sym] = conid
                    await pool.execute(
                        """
                        INSERT INTO instrument_conids (symbol, conid, updated_at)
                        VALUES ($1, $2, NOW())
                        ON CONFLICT (symbol) DO UPDATE
                        SET conid = EXCLUDED.conid, updated_at = NOW()
                        """,
                        sym, conid,
                    )
        except Exception as exc:
            logger.warning("conid lookup failed for %s: %s", missing, exc)

    return result


# ---------------------------------------------------------------------------
# Quote building
# ---------------------------------------------------------------------------

def _dec(val) -> Optional[Decimal]:
    if val is None:
        return None
    try:
        s = str(val).strip().lstrip("C")  # IBKR sometimes prefixes change fields
        return Decimal(s) if s else None
    except Exception:
        return None


def _snapshot_to_quote(symbol: str, snap: dict) -> Optional[PriceQuote]:
    price = _dec(snap.get("31"))
    if price is None:
        return None
    prev_close = _dec(snap.get("7296"))
    change = _dec(snap.get("82"))
    change_pct = _dec(snap.get("83"))
    return PriceQuote(
        symbol=symbol,
        price=price,
        change=change,
        change_pct=change_pct,
        previous_close=prev_close,
        source="ibkr",
        as_of=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# Public: batch warming (preferred path for portfolio valuation)
# ---------------------------------------------------------------------------

async def warm_quote_cache(pool, symbols: list[str]) -> None:
    """
    Fetch current prices for N symbols in ONE IBKR snapshot call.
    Populates the in-process cache so subsequent get_quote() calls are instant.
    """
    if not symbols:
        return
    uncached = [s.upper() for s in symbols if not _cached_quote(s.upper())]
    if not uncached:
        return

    try:
        from services.ibkr_client import ibkr_client
    except Exception as exc:
        logger.error("IBKR client import failed: %s", exc)
        return

    if not ibkr_client.is_connected():
        logger.warning("IBKR not connected — cannot fetch quotes for %d symbols", len(uncached))
        return

    conid_map = await _resolve_conids(pool, uncached)
    if not conid_map:
        logger.warning("Could not resolve any conids for %s", uncached)
        return

    conids = list(conid_map.values())
    snapshots = ibkr_client.get_market_snapshot_batch(conids)

    # Reverse lookup: conid -> symbol
    by_conid = {cid: sym for sym, cid in conid_map.items()}
    for cid, snap in snapshots.items():
        sym = by_conid.get(cid)
        if not sym:
            continue
        quote = _snapshot_to_quote(sym, snap)
        if quote:
            _store_quote(sym, quote)

    logger.info(
        "warm_quote_cache: cached %d/%d via IBKR",
        sum(1 for s in uncached if _cached_quote(s)),
        len(uncached),
    )


# ---------------------------------------------------------------------------
# Public: single quote
# ---------------------------------------------------------------------------

async def get_quote(pool, symbol: str) -> Optional[PriceQuote]:
    """Get current price for one symbol (cached). Use warm_quote_cache for N symbols."""
    sym = symbol.upper()
    cached = _cached_quote(sym)
    if cached:
        return cached
    await warm_quote_cache(pool, [sym])
    return _cached_quote(sym)


# ---------------------------------------------------------------------------
# Historical bars (via IBKR /iserver/marketdata/history)
# ---------------------------------------------------------------------------

def _period_for(days: int) -> str:
    if days <= 31:
        return "1m"
    if days <= 91:
        return "3m"
    if days <= 182:
        return "6m"
    if days <= 365:
        return "1y"
    return "2y"


async def get_historical_bars(
    pool,
    symbol: str,
    start: date,
    end: date,
) -> list[HistoricalBar]:
    """Fetch OHLCV daily bars via IBKR for one symbol."""
    try:
        from services.ibkr_client import ibkr_client
        if not ibkr_client.is_connected():
            return []
        conid_map = await _resolve_conids(pool, [symbol])
        conid = conid_map.get(symbol.upper())
        if conid is None:
            return []

        period = _period_for((end - start).days)
        raw = ibkr_client._get(
            "/iserver/marketdata/history",
            params={"conid": conid, "period": period, "bar": "1d", "outsideRth": True},
        )
        if not raw or "data" not in raw:
            return []

        bars: list[HistoricalBar] = []
        for b in raw["data"]:
            ts_ms = b.get("t")
            if ts_ms is None:
                continue
            d = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).date()
            if d < start or d > end:
                continue
            try:
                bars.append(HistoricalBar(
                    date=d,
                    open=Decimal(str(round(float(b.get("o", 0) or 0), 4))),
                    high=Decimal(str(round(float(b.get("h", 0) or 0), 4))),
                    low=Decimal(str(round(float(b.get("l", 0) or 0), 4))),
                    close=Decimal(str(round(float(b.get("c", 0) or 0), 4))),
                    volume=int(b.get("v", 0) or 0),
                ))
            except Exception:
                continue
        return bars
    except Exception as exc:
        logger.error("IBKR historical bars failed for %s: %s", symbol, exc)
        return []


async def get_historical_bars_batch(
    pool,
    symbols: list[str],
    start: date,
    end: date,
) -> dict[str, dict[date, Decimal]]:
    """
    Fetch close prices for multiple symbols via IBKR.
    Returns {symbol: {date: close_price}}.
    """
    result: dict[str, dict[date, Decimal]] = {s.upper(): {} for s in symbols}
    if not symbols:
        return result

    try:
        from services.ibkr_client import ibkr_client
        if not ibkr_client.is_connected():
            logger.warning("IBKR not connected — historical batch returning empty")
            return result
    except Exception as exc:
        logger.error("IBKR client unavailable: %s", exc)
        return result

    conid_map = await _resolve_conids(pool, list(result.keys()))
    period = _period_for((end - start).days)

    for sym, conid in conid_map.items():
        try:
            raw = ibkr_client._get(
                "/iserver/marketdata/history",
                params={"conid": conid, "period": period, "bar": "1d", "outsideRth": True},
            )
            if not raw or "data" not in raw:
                continue
            for b in raw["data"]:
                ts_ms = b.get("t")
                close = b.get("c")
                if ts_ms is None or close is None:
                    continue
                d = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).date()
                if start <= d <= end:
                    result[sym][d] = Decimal(str(round(float(close), 4)))
        except Exception as exc:
            logger.warning("IBKR history failed for %s: %s", sym, exc)

    covered = sum(1 for v in result.values() if v)
    logger.info("IBKR historical batch: %d/%d symbols covered", covered, len(symbols))
    return result


async def get_spy_history(pool, start: date, end: date) -> list[HistoricalBar]:
    """SPY benchmark series."""
    return await get_historical_bars(pool, config.BENCHMARK_SYMBOL, start, end)

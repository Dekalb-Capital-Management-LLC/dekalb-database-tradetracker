"""
Market data service — IBKR Web API.

Quotes come from /iserver/marketdata/snapshot with the standard pre-flight +
retry pattern (handled inside ibkr_client.get_market_snapshot_batch).
Historical bars come from /iserver/marketdata/history.

All live data is cached in-process for PRICE_CACHE_TTL_SECONDS.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

import config
from models.schemas import HistoricalBar, PriceQuote

logger = logging.getLogger(__name__)

# symbol -> (expires_at, quote)
_price_cache: dict[str, tuple[float, PriceQuote]] = {}
# symbol -> conid  (session-lifetime)
_conid_cache: dict[str, int] = {}


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
    Resolve symbols to IBKR conids.
    Checks: process cache → instrument_conids table → /trsrv/stocks live lookup.
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
            logger.warning("conid batch lookup failed: %s", exc)

    return result


# ---------------------------------------------------------------------------
# Quote building from IBKR snapshot field map
# ---------------------------------------------------------------------------

def _dec(val) -> Optional[Decimal]:
    if val is None:
        return None
    try:
        s = str(val).strip().lstrip("C")  # IBKR prefixes change fields with "C"
        return Decimal(s) if s else None
    except Exception:
        return None


def _snapshot_to_quote(symbol: str, snap: dict) -> Optional[PriceQuote]:
    price = _dec(snap.get("31"))  # last traded price
    if price is None:
        return None
    return PriceQuote(
        symbol=symbol,
        price=price,
        change=_dec(snap.get("82")),
        change_pct=_dec(snap.get("83")),
        previous_close=_dec(snap.get("7296")),
        source="ibkr",
        as_of=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# Public: batch warm (call once per page load — one IBKR round-trip for all)
# ---------------------------------------------------------------------------

async def warm_quote_cache(pool, symbols: list[str]) -> None:
    """
    Fetch live prices for all symbols in a single IBKR snapshot call.
    Skips symbols already in cache. No-ops when IBKR is not connected.
    """
    if not symbols:
        return
    uncached = [s.upper() for s in symbols if not _cached_quote(s.upper())]
    if not uncached:
        return

    try:
        from services.ibkr_client import ibkr_client
    except Exception as exc:
        logger.error("IBKR client unavailable: %s", exc)
        return

    if not ibkr_client.is_connected():
        logger.warning("IBKR not connected — skipping quote fetch for %d symbols", len(uncached))
        return

    conid_map = await _resolve_conids(pool, uncached)
    if not conid_map:
        logger.warning("Could not resolve conids for %s", uncached)
        return

    snapshots = ibkr_client.get_market_snapshot_batch(list(conid_map.values()))

    by_conid = {cid: sym for sym, cid in conid_map.items()}
    cached_count = 0
    for cid, snap in snapshots.items():
        sym = by_conid.get(cid)
        if not sym:
            continue
        quote = _snapshot_to_quote(sym, snap)
        if quote:
            _store_quote(sym, quote)
            cached_count += 1

    logger.info("warm_quote_cache: %d/%d symbols priced via IBKR", cached_count, len(uncached))


# ---------------------------------------------------------------------------
# Public: single quote
# ---------------------------------------------------------------------------

async def get_quote(pool, symbol: str) -> Optional[PriceQuote]:
    sym = symbol.upper()
    cached = _cached_quote(sym)
    if cached:
        return cached
    await warm_quote_cache(pool, [sym])
    return _cached_quote(sym)


# ---------------------------------------------------------------------------
# Historical bars
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


async def get_historical_bars(pool, symbol: str, start: date, end: date) -> list[HistoricalBar]:
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
    result: dict[str, dict[date, Decimal]] = {s.upper(): {} for s in symbols}
    if not symbols:
        return result
    try:
        from services.ibkr_client import ibkr_client
        if not ibkr_client.is_connected():
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
    return await get_historical_bars(pool, config.BENCHMARK_SYMBOL, start, end)

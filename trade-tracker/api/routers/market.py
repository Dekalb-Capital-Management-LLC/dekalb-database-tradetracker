"""
Market data router.

Endpoints:
  GET /market/quote/{symbol}          - current price quote
  GET /market/quotes                  - batch price quotes
  GET /market/history/{symbol}        - historical OHLCV bars
  GET /market/spy                     - SPY benchmark data
  GET /market/provider/status         - active market-data provider status
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

import db
from models.schemas import HistoricalBar, PriceQuote
from services import market_data

router = APIRouter(prefix="/market", tags=["market"])
logger = logging.getLogger(__name__)


def get_pool():
    return db.get_pool()


@router.get("/provider/status")
async def get_provider_status():
    return market_data.provider_status()


@router.get("/quote/{symbol}", response_model=PriceQuote)
async def get_quote(symbol: str, pool=Depends(get_pool)):
    quote = await market_data.get_quote(pool, symbol.upper())
    if not quote:
        raise HTTPException(
            status_code=503,
            detail=f"Could not fetch price for {symbol.upper()} from any configured provider.",
        )
    return quote


@router.get("/quotes", response_model=list[PriceQuote])
async def get_quotes(
    symbols: str = Query(..., description="Comma-separated list of symbols, e.g. AAPL,MSFT,SPY"),
    pool=Depends(get_pool),
):
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="No symbols provided")
    if len(symbol_list) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 symbols per request")

    # Single batched snapshot call
    await market_data.warm_quote_cache(pool, symbol_list)

    results = []
    for sym in symbol_list:
        quote = await market_data.get_quote(pool, sym)
        if quote:
            results.append(quote)
        else:
            logger.warning("No quote available for %s", sym)
    return results


@router.get("/history/{symbol}", response_model=list[HistoricalBar])
async def get_history(
    symbol: str,
    start: date = Query(default=None, description="Start date (YYYY-MM-DD), defaults to 1 year ago"),
    end: date = Query(default=None, description="End date (YYYY-MM-DD), defaults to today"),
    pool=Depends(get_pool),
):
    today = date.today()
    start = start or (today - timedelta(days=365))
    end = end or today

    if start > end:
        raise HTTPException(status_code=400, detail="start must be before end")

    bars = await asyncio.to_thread(market_data.get_historical_bars, symbol.upper(), start, end)

    if not bars:
        raise HTTPException(
            status_code=503,
            detail=f"No historical data available for {symbol.upper()}",
        )
    return bars


@router.get("/spy", response_model=list[HistoricalBar])
async def get_spy(
    start: date = Query(default=None, description="Start date, defaults to 1 year ago"),
    end: date = Query(default=None, description="End date, defaults to today"),
    pool=Depends(get_pool),
):
    today = date.today()
    start = start or (today - timedelta(days=365))
    end = end or today
    bars = await asyncio.to_thread(market_data.get_spy_history, start, end)

    if not bars:
        raise HTTPException(status_code=503, detail="No SPY data available")
    return bars

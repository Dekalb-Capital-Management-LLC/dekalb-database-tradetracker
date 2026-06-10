"""
Market data router.

Endpoints:
  GET /market/quote/{symbol}          - current price quote
  GET /market/quotes                  - batch price quotes
  GET /market/history/{symbol}        - historical OHLCV bars
  GET /market/spy                     - SPY benchmark data (shortcut)
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from models.schemas import HistoricalBar, PriceQuote
from services import market_data

router = APIRouter(prefix="/market", tags=["market"])
logger = logging.getLogger(__name__)


@router.get("/quote/{symbol}", response_model=PriceQuote)
async def get_quote(symbol: str):
    """
    Current price for a single symbol.
    Source: yfinance (or IBKR gateway if IBKR_ENABLED=true).
    Results cached for PRICE_CACHE_TTL_SECONDS (default 60s).
    """
    quote = market_data.get_quote(symbol.upper())
    if not quote:
        raise HTTPException(
            status_code=503,
            detail=f"Could not fetch price for {symbol.upper()}. Market may be closed or symbol invalid.",
        )
    return quote


@router.get("/quotes", response_model=list[PriceQuote])
async def get_quotes(
    symbols: str = Query(..., description="Comma-separated list of symbols, e.g. AAPL,MSFT,SPY"),
):
    """
    Batch price quotes for multiple symbols.
    """
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="No symbols provided")
    if len(symbol_list) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 symbols per request")

    results = []
    for sym in symbol_list:
        quote = market_data.get_quote(sym)
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
    interval: str = Query(default="1d", description="Bar interval: 1d | 1wk | 1mo"),
):
    """
    Historical OHLCV bars for a symbol via yfinance.
    Useful for the frontend to draw candlestick charts or calculate custom metrics.
    """
    if interval not in ("1d", "1wk", "1mo"):
        raise HTTPException(status_code=400, detail="interval must be one of: 1d, 1wk, 1mo")

    today = date.today()
    start = start or (today - timedelta(days=365))
    end = end or today

    if start > end:
        raise HTTPException(status_code=400, detail="start must be before end")

    bars = market_data.get_historical_bars(symbol.upper(), start, end, interval)
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
):
    """
    SPY (S&P 500 ETF) historical data. Used for benchmark overlay on performance graphs.
    """
    today = date.today()
    start = start or (today - timedelta(days=365))
    end = end or today
    bars = market_data.get_spy_history(start, end)
    if not bars:
        raise HTTPException(status_code=503, detail="No SPY data available")
    return bars

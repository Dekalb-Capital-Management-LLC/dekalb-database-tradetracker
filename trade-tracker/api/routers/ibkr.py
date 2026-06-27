"""
IBKR router.

Endpoints:
  GET  /ibkr/status         - Pangolin connection + IBKR auth status
  GET  /ibkr/account        - Live NAV, cash, equity from IBKR
  GET  /ibkr/positions      - Live open positions from IBKR
  POST /ibkr/sync/trades    - Pull IBKR fills (PA history + recent) into DB
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

import config
import db
from services.ibkr_client import ibkr_client

router = APIRouter(prefix="/ibkr", tags=["ibkr"])
logger = logging.getLogger(__name__)


def get_pool():
    return db.get_pool()


def _require_ibkr():
    if not config.IBKR_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="IBKR not enabled. Set IBKR_ENABLED=true and IBKR_ACCOUNT_ID in your environment.",
        )
    if not config.IBKR_ACCOUNT_ID:
        raise HTTPException(
            status_code=503,
            detail="IBKR_ACCOUNT_ID not set. Ask your manager for your account ID (format: U1234567).",
        )


def _parse_pa_trade(t: dict, sym_by_conid: dict[int, str]) -> Optional[dict]:
    """Map IBKR Portfolio Analyst transaction to trades-table fields."""
    tx_type = t.get("type")
    if tx_type not in ("Buy", "Sell"):
        return None

    raw_date = t.get("rawDate")
    if not raw_date:
        return None

    try:
        qty = Decimal(str(abs(float(t.get("qty", 0)))))
        price = Decimal(str(t.get("pr", 0)))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if qty <= 0 or price <= 0:
        return None

    conid = int(t.get("conid", 0))
    symbol = sym_by_conid.get(conid) or (t.get("desc") or "UNKNOWN").split()[0]
    symbol = symbol.upper().strip()
    side = "BUY" if tx_type == "Buy" else "SELL"

    gross = (qty * price).quantize(Decimal("0.01"))
    try:
        amt = abs(Decimal(str(float(t.get("amt", 0)))))
    except (InvalidOperation, TypeError, ValueError):
        amt = gross
    commission = max(Decimal("0"), (amt - gross).quantize(Decimal("0.01")))

    if side == "BUY":
        net = -(gross + commission)
    else:
        net = gross - commission

    trade_date = datetime.strptime(str(raw_date), "%Y%m%d")
    order_id = f"pa-{conid}-{raw_date}-{tx_type}-{qty}-{price}"

    return {
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "commission": commission,
        "gross": gross,
        "net": net,
        "trade_date": trade_date,
        "raw": t,
    }


def _parse_iserver_trade(t: dict) -> Optional[dict]:
    order_id = str(t.get("execution_id") or t.get("orderId") or "").strip()
    if not order_id:
        return None

    raw_side = str(t.get("side", "")).upper()
    side = "BUY" if raw_side in ("BOT", "BUY", "B") else "SELL"

    try:
        qty = Decimal(str(t.get("size") or t.get("quantity") or 0))
        price = Decimal(str(t.get("price") or 0))
        commission = Decimal(str(t.get("commission") or 0))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if qty <= 0 or price <= 0:
        return None

    gross = (qty * price).quantize(Decimal("0.01"))
    if side == "BUY":
        net = -(gross + commission)
    else:
        net = gross - commission

    raw_time = t.get("trade_time") or t.get("tradeTime") or t.get("time")
    if raw_time:
        try:
            trade_date = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
        except ValueError:
            trade_date = datetime.utcnow()
    else:
        trade_date = datetime.utcnow()

    symbol = (t.get("symbol") or t.get("ticker") or "").upper().strip()
    if not symbol:
        return None

    return {
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "commission": commission,
        "gross": gross,
        "net": net,
        "trade_date": trade_date,
        "raw": t,
    }


async def _insert_trade(pool, parsed: dict) -> bool:
    """Insert one trade; return True if inserted, False if skipped."""
    order_id = parsed["order_id"]
    existing = await pool.fetchval(
        "SELECT id FROM trades WHERE ibkr_order_id = $1", order_id
    )
    if existing:
        return False

    await pool.execute(
        """
        INSERT INTO trades
          (source, account_id, trade_date, symbol, side, quantity, price,
           commission, gross_amount, net_amount, ibkr_order_id, raw_data)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        """,
        "ibkr",
        config.IBKR_ACCOUNT_ID,
        parsed["trade_date"],
        parsed["symbol"],
        parsed["side"],
        float(parsed["qty"]),
        float(parsed["price"]),
        float(parsed["commission"]),
        float(parsed["gross"]),
        float(parsed["net"]),
        order_id,
        json.dumps(parsed["raw"]) if isinstance(parsed["raw"], dict) else str(parsed["raw"]),
    )
    return True


# Startup, the snapshot-cron container (every 5 min), and any number of
# concurrent "Update Portfolio" clicks can all call sync_ibkr_trades around the
# same time. Each call fans out into ~1 sequential, throttled IBKR HTTP request
# per position (get_all_pa_transactions can't batch conids — IBKR truncates
# history when it does) — that's the single biggest blocking sequence in this
# API. Multiple overlapping callers used to mean multiple overlapping 18+-call
# sequences hitting IBKR at once, which is exactly what trips IBKR's own
# 503/429 rate limiting. The lock + short debounce window collapse all of that
# into a single in-flight sync that every caller shares the result of.
_sync_lock = asyncio.Lock()
_last_sync_result: Optional[dict] = None
_last_sync_at: float = 0.0
SYNC_MIN_INTERVAL_SECONDS = 30


async def sync_ibkr_trades(pool) -> dict:
    """Pull IBKR PA + iserver fills into trades table. Used by endpoint, startup, and cron."""
    global _last_sync_result, _last_sync_at
    async with _sync_lock:
        if _last_sync_result is not None and (time.monotonic() - _last_sync_at) < SYNC_MIN_INTERVAL_SECONDS:
            return {**_last_sync_result, "deduplicated": True}
        result = await _sync_ibkr_trades_once(pool)
        _last_sync_result = result
        _last_sync_at = time.monotonic()
        return result


async def _sync_ibkr_trades_once(pool) -> dict:
    # The actual IBKR fetches are synchronous and throttled with time.sleep
    # between calls — run them on a worker thread so they don't block the
    # single asyncio event loop (and therefore every other concurrent
    # request) for the several seconds to a minute-plus this can take.
    def _fetch_from_ibkr():
        sym_by_conid = ibkr_client.position_symbol_map(config.IBKR_ACCOUNT_ID)
        conids = list(sym_by_conid.keys()) or ibkr_client.position_conids(config.IBKR_ACCOUNT_ID)
        pa_trades = ibkr_client.get_all_pa_transactions(config.IBKR_ACCOUNT_ID, conids)
        iserver_trades = ibkr_client.get_recent_trades(config.IBKR_ACCOUNT_ID)
        return sym_by_conid, conids, pa_trades, iserver_trades

    sym_by_conid, conids, pa_trades, iserver_trades = await asyncio.to_thread(_fetch_from_ibkr)
    symbols_synced = len(conids)
    total_from_ibkr = len(pa_trades) + len(iserver_trades)

    if not pa_trades and not iserver_trades:
        return {
            "inserted": 0,
            "skipped": 0,
            "total_from_ibkr": 0,
            "symbols_synced": symbols_synced,
            "transactions_parsed": 0,
            "transactions_inserted": 0,
            "message": "No trades returned by IBKR",
        }

    inserted = 0
    skipped = 0
    parsed_count = 0
    errors: list[str] = []

    for t in pa_trades:
        try:
            parsed = _parse_pa_trade(t, sym_by_conid)
            if not parsed:
                skipped += 1
                continue
            parsed_count += 1
            if await _insert_trade(pool, parsed):
                inserted += 1
            else:
                skipped += 1
        except Exception as exc:
            msg = f"PA trade parse/insert error — {exc}"
            logger.warning(msg)
            errors.append(msg)
            skipped += 1

    for t in iserver_trades:
        try:
            parsed = _parse_iserver_trade(t)
            if not parsed:
                skipped += 1
                continue
            parsed_count += 1
            if await _insert_trade(pool, parsed):
                inserted += 1
            else:
                skipped += 1
        except Exception as exc:
            order_id = t.get("execution_id") or t.get("orderId") or "?"
            msg = f"Trade {order_id}: {exc}"
            logger.warning(msg)
            errors.append(msg)
            skipped += 1

    logger.info(
        "IBKR trade sync complete: inserted=%d skipped=%d errors=%d pa=%d iserver=%d",
        inserted, skipped, len(errors), len(pa_trades), len(iserver_trades),
    )
    return {
        "inserted": inserted,
        "skipped": skipped,
        "total_from_ibkr": total_from_ibkr,
        "symbols_synced": symbols_synced,
        "transactions_parsed": parsed_count,
        "transactions_inserted": inserted,
        "pa_transactions": len(pa_trades),
        "iserver_trades": len(iserver_trades),
        "errors": errors or None,
    }


@router.get("/status", summary="IBKR connection and auth status")
def get_status():
    """
    Check IBKR connectivity. Safe when disabled — returns enabled=false.
    OAuth mode: IBKR_CLIENT_ID set in env. Gateway mode: local Client Portal Gateway.
    """
    if not config.IBKR_ENABLED:
        return {
            "enabled": False,
            "mode": "disabled",
            "message": "Set IBKR_ENABLED=true and IBKR_ACCOUNT_ID to activate",
        }

    mode = "oauth" if config.IBKR_USE_OAUTH else "gateway"
    api_url = config.IBKR_API_BASE_URL if config.IBKR_USE_OAUTH else config.IBKR_GATEWAY_URL

    if config.IBKR_USE_OAUTH and not ibkr_client.is_connected:
        return {
            "enabled": True,
            "mode": mode,
            "connected": False,
            "api_url": api_url,
            "account_id": config.IBKR_ACCOUNT_ID or "not set",
            "message": "OAuth session not established — check API startup logs",
        }

    auth = ibkr_client.auth_status()
    if auth is None:
        return {
            "enabled": True,
            "mode": mode,
            "connected": False,
            "api_url": api_url,
            "account_id": config.IBKR_ACCOUNT_ID or "not set",
            "message": (
                "Could not reach IBKR OAuth API"
                if config.IBKR_USE_OAUTH
                else "Could not reach IBKR gateway — is it running and authenticated?"
            ),
        }

    oauth_connected = ibkr_client.is_connected if config.IBKR_USE_OAUTH else True
    iserver_authenticated = auth.get("authenticated", False)
    positions_count = 0
    if config.IBKR_ACCOUNT_ID and oauth_connected:
        positions_count = len(ibkr_client.get_positions(config.IBKR_ACCOUNT_ID))

    return {
        "enabled": True,
        "mode": mode,
        "connected": True,
        "oauth_connected": oauth_connected,
        "iserver_authenticated": iserver_authenticated,
        "authenticated": iserver_authenticated,
        "competing": auth.get("competing", False),
        "api_url": api_url,
        "account_id": config.IBKR_ACCOUNT_ID or "not set",
        "positions_count": positions_count,
        "account_nav": ibkr_client.last_account_nav,
    }


# ---------------------------------------------------------------------------
# Account summary
# ---------------------------------------------------------------------------

@router.get("/account", summary="Live account NAV and balances from IBKR")
def get_account_summary():
    """
    Fetches live NAV, cash balance, and equity value directly from IBKR via the gateway.
    Much more accurate than the derived values from trade history alone.
    """
    _require_ibkr()

    summary = ibkr_client.get_account_summary(config.IBKR_ACCOUNT_ID)
    if summary is None:
        raise HTTPException(
            status_code=502,
            detail="Could not fetch account summary from IBKR. Check /ibkr/status.",
        )

    def extract_amount(field: str) -> Optional[float]:
        entry = summary.get(field, {})
        if isinstance(entry, dict):
            return entry.get("amount")
        return None

    return {
        "account_id": config.IBKR_ACCOUNT_ID,
        "total_nav": extract_amount("netliquidation"),
        "cash_balance": extract_amount("totalcashvalue"),
        "equity_value": extract_amount("equitywithloanvalue"),
        "gross_position_value": extract_amount("grosspositionvalue"),
        "buying_power": extract_amount("buyingpower"),
        "as_of": datetime.utcnow().isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# Live positions
# ---------------------------------------------------------------------------

@router.get("/positions", summary="Live open positions from IBKR")
def get_live_positions():
    """
    Returns current open positions pulled directly from IBKR (not derived from trade history).
    Use this to reconcile against /portfolio/positions which is computed from the trades table.
    """
    _require_ibkr()

    raw = ibkr_client.get_positions(config.IBKR_ACCOUNT_ID)
    if not raw:
        return []

    positions = []
    for p in raw:
        qty = p.get("position", 0)
        if qty == 0:
            continue  # skip flat positions
        positions.append({
            "symbol": p.get("ticker") or p.get("contractDesc", "UNKNOWN"),
            "conid": p.get("conid"),
            "quantity": qty,
            "market_price": p.get("mktPrice"),
            "market_value": p.get("mktValue"),
            "avg_cost": p.get("avgCost"),
            "unrealized_pnl": p.get("unrealizedPnl"),
            "realized_pnl": p.get("realizedPnl"),
            "currency": p.get("currency", "USD"),
        })

    return positions


# ---------------------------------------------------------------------------
# Trade sync
# ---------------------------------------------------------------------------

@router.post("/sync/trades", summary="Pull IBKR trade history into the trades table")
async def sync_recent_trades(pool=Depends(get_pool)):
    """
    Fetches buy/sell history from IBKR Portfolio Analyst (up to ~2y per holding)
    plus recent iserver fills, then inserts new rows into trades.
    """
    _require_ibkr()
    return await sync_ibkr_trades(pool)

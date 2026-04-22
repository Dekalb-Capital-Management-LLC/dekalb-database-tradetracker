"""
IBKR router.

Authentication is fully automatic (RSA key-based, server-side).
No login page, no redirect, no user action needed.

Endpoints:
  GET  /ibkr/status           - is IBKR connected?
  POST /ibkr/connect          - manually trigger a reconnect (useful after config change)
  GET  /ibkr/account          - live NAV, cash, equity
  GET  /ibkr/positions        - live open positions
  POST /ibkr/sync/trades      - pull recent fills into trades table
                                (also runs automatically every hour via cron)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
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
            detail="IBKR not enabled. Set IBKR_ENABLED=true and configure credentials in .env.",
        )
    if not ibkr_client.is_connected():
        raise HTTPException(
            status_code=503,
            detail="IBKR not connected. Check credentials and server logs. Try POST /ibkr/connect.",
        )
    if not config.IBKR_ACCOUNT_ID:
        raise HTTPException(status_code=503, detail="IBKR_ACCOUNT_ID not set.")


# ---------------------------------------------------------------------------
# Status + manual reconnect
# ---------------------------------------------------------------------------

@router.get("/status", summary="IBKR connection status")
def get_status():
    """
    Returns whether IBKR is connected and ready.
    The frontend uses this to show the connection indicator in the sidebar.
    """
    if not config.IBKR_ENABLED:
        return {
            "enabled": False,
            "connected": False,
            "message": "Set IBKR_ENABLED=true and configure RSA credentials in .env.",
        }

    connected = ibkr_client.is_connected()
    return {
        "enabled": True,
        "connected": connected,
        "account_id": config.IBKR_ACCOUNT_ID or "not set",
        "message": "Connected" if connected else "Not connected — check logs or POST /ibkr/connect",
    }


@router.post("/connect", summary="Manually trigger IBKR reconnection")
def reconnect():
    """
    Re-run the RSA auth flow and reconnect to IBKR.
    Useful after updating credentials or if the session dropped unexpectedly.
    """
    if not config.IBKR_ENABLED:
        raise HTTPException(status_code=503, detail="IBKR_ENABLED is false.")

    ibkr_client.disconnect()
    success = ibkr_client.connect()
    if success:
        return {"connected": True, "message": "Reconnected successfully."}
    raise HTTPException(
        status_code=502,
        detail="Reconnection failed. Check IBKR credentials and IBKR_SERVER_IP in logs.",
    )


# ---------------------------------------------------------------------------
# Account summary
# ---------------------------------------------------------------------------

@router.get("/account", summary="Live account NAV and balances")
def get_account_summary():
    _require_ibkr()

    summary = ibkr_client.get_account_summary(config.IBKR_ACCOUNT_ID)
    if summary is None:
        raise HTTPException(status_code=502, detail="Could not fetch account summary from IBKR.")

    def extract(field: str) -> Optional[float]:
        entry = summary.get(field, {})
        if isinstance(entry, dict):
            return entry.get("amount")
        return None

    return {
        "account_id": config.IBKR_ACCOUNT_ID,
        "total_nav": extract("netliquidation"),
        "cash_balance": extract("totalcashvalue"),
        "equity_value": extract("equitywithloanvalue"),
        "gross_position_value": extract("grosspositionvalue"),
        "buying_power": extract("buyingpower"),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Live positions
# ---------------------------------------------------------------------------

@router.get("/positions", summary="Live open positions")
def get_live_positions():
    _require_ibkr()

    raw = ibkr_client.get_positions(config.IBKR_ACCOUNT_ID)
    positions = []
    for p in raw:
        qty = p.get("position", 0)
        if qty == 0:
            continue
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

async def _sync_ibkr_trades(pool) -> dict:
    """
    Fetches the last ~24 hours of fills from IBKR and inserts any new ones
    into the trades table. Existing trades are matched by ibkr_order_id and skipped.
    """
    if not config.IBKR_ENABLED or not config.IBKR_ACCOUNT_ID:
        return {"inserted": 0, "skipped": 0, "total_from_ibkr": 0,
                "message": "IBKR not configured. Set IBKR_ENABLED=true and IBKR_ACCOUNT_ID."}
    if not ibkr_client.is_connected():
        return {"inserted": 0, "skipped": 0, "total_from_ibkr": 0,
                "message": "IBKR gateway not connected. Check /ibkr/status for details."}

    raw_trades = ibkr_client.get_recent_trades(config.IBKR_ACCOUNT_ID)
    if not raw_trades:
        return {"inserted": 0, "skipped": 0, "total_from_ibkr": 0, "message": "No recent trades returned by IBKR"}

    inserted = 0
    skipped = 0
    errors = []

    for t in raw_trades:
        order_id = str(t.get("execution_id") or t.get("orderId") or "").strip()
        if not order_id:
            skipped += 1
            continue

        existing = await pool.fetchval("SELECT id FROM trades WHERE ibkr_order_id = $1", order_id)
        if existing:
            skipped += 1
            continue

        try:
            raw_side = str(t.get("side", "")).upper()
            side = "BUY" if raw_side in ("BOT", "BUY", "B") else "SELL"

            qty = Decimal(str(t.get("size") or t.get("quantity") or 0))
            price = Decimal(str(t.get("price") or 0))
            commission = Decimal(str(t.get("commission") or 0))

            gross = (qty * price).quantize(Decimal("0.01"))
            net = -(gross + commission) if side == "BUY" else gross - commission

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
                errors.append(f"Trade {order_id}: missing symbol, skipped")
                skipped += 1
                continue

            await pool.execute(
                """
                INSERT INTO trades
                  (source, account_id, trade_date, symbol, side, quantity, price,
                   commission, gross_amount, net_amount, ibkr_order_id, raw_data)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                "ibkr", config.IBKR_ACCOUNT_ID, trade_date, symbol, side,
                float(qty), float(price), float(commission),
                float(gross), float(net), order_id, json.dumps(t),
            )
            inserted += 1

        except (InvalidOperation, ValueError) as exc:
            msg = f"Trade {order_id}: parse error — {exc}"
            logger.warning(msg)
            errors.append(msg)
            skipped += 1
        except Exception as exc:
            msg = f"Trade {order_id}: DB error — {exc}"
            logger.error(msg)
            errors.append(msg)
            skipped += 1

    logger.info("IBKR sync: inserted=%d skipped=%d errors=%d", inserted, skipped, len(errors))
    return {
        "inserted": inserted,
        "skipped": skipped,
        "total_from_ibkr": len(raw_trades),
        "errors": errors or None,
    }


@router.post("/sync/trades", summary="Pull recent IBKR fills into trades table (also runs automatically every hour)")
async def sync_recent_trades(background_tasks, pool=Depends(get_pool)):
    _require_ibkr()
    result = await _sync_ibkr_trades(pool)
    # Refresh today's snapshot after sync so dashboard numbers update
    if result.get("inserted", 0) > 0:
        from services.portfolio_metrics import backfill_snapshots
        background_tasks.add_task(backfill_snapshots, pool)
    return result

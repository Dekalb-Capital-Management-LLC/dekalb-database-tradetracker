"""
IBKR router.

Endpoints:
  GET  /ibkr/status         - Pangolin connection + IBKR auth status
  GET  /ibkr/account        - Live NAV, cash, equity from IBKR
  GET  /ibkr/positions      - Live open positions from IBKR
  POST /ibkr/sync/trades    - Pull last ~24h of IBKR fills and insert new ones into DB
"""
from __future__ import annotations

import logging
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


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get("/status", summary="IBKR gateway connection and auth status")
def get_status():
    """
    Check whether the IBKR Client Portal Gateway is reachable and the session is authenticated.
    Safe to call even when IBKR is disabled — returns enabled=false instead of erroring.
    """
    if not config.IBKR_ENABLED:
        return {
            "enabled": False,
            "gateway_url": config.IBKR_GATEWAY_URL,
            "message": "Set IBKR_ENABLED=true and IBKR_ACCOUNT_ID to activate",
        }

    auth = ibkr_client.auth_status()
    if auth is None:
        return {
            "enabled": True,
            "connected": False,
            "gateway_url": config.IBKR_GATEWAY_URL,
            "message": "Could not reach the IBKR gateway — is it running and authenticated?",
        }

    return {
        "enabled": True,
        "connected": True,
        "authenticated": auth.get("authenticated", False),
        "competing": auth.get("competing", False),
        "gateway_url": config.IBKR_GATEWAY_URL,
        "account_id": config.IBKR_ACCOUNT_ID or "not set",
        "raw": auth,
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

@router.post("/sync/trades", summary="Pull recent IBKR fills into the trades table")
async def sync_recent_trades(pool=Depends(get_pool)):
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
        # IBKR uses 'execution_id' or 'orderId' depending on endpoint version
        order_id = str(t.get("execution_id") or t.get("orderId") or "").strip()
        if not order_id:
            skipped += 1
            continue

        # Skip if already imported
        existing = await pool.fetchval(
            "SELECT id FROM trades WHERE ibkr_order_id = $1", order_id
        )
        if existing:
            skipped += 1
            continue

        try:
            # IBKR side: "BOT" = buy, "SLD" = sell
            raw_side = str(t.get("side", "")).upper()
            side = "BUY" if raw_side in ("BOT", "BUY", "B") else "SELL"

            qty = Decimal(str(t.get("size") or t.get("quantity") or 0))
            price = Decimal(str(t.get("price") or 0))
            commission = Decimal(str(t.get("commission") or 0))

            gross = (qty * price).quantize(Decimal("0.01"))
            # net_amount: positive = money out (buy), negative = money in (sell)
            if side == "BUY":
                net = -(gross + commission)
            else:
                net = gross - commission

            # Trade timestamp — try multiple field names IBKR uses
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
                "ibkr",
                config.IBKR_ACCOUNT_ID,
                trade_date,
                symbol,
                side,
                float(qty),
                float(price),
                float(commission),
                float(gross),
                float(net),
                order_id,
                str(t),  # store raw dict as string for audit trail
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

    logger.info(
        "IBKR trade sync complete: inserted=%d skipped=%d errors=%d",
        inserted, skipped, len(errors),
    )
    return {
        "inserted": inserted,
        "skipped": skipped,
        "total_from_ibkr": len(raw_trades),
        "errors": errors or None,
    }

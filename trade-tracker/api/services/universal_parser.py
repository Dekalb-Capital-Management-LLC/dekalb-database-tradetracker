"""
Universal trade CSV parser.

Auto-detects format and routes to the correct parser:
  1. IBKR Activity Statement   — contains "Trades,Header," rows
  2. Simple portfolio format   — header has "Date Acquired" + "Price Acquired"
  3. Fidelity Positions        — header has "Average Cost Basis"
  4. Fidelity Activity/Orders  — anything else with "Action" / "Run Date"

Returns (trades, errors, account_id, source_label).
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from models.schemas import TradeCreate
from services.ibkr_parser import parse_ibkr_csv, extract_account_id as _ibkr_account
from services.fidelity_parser import parse_fidelity_csv, extract_account_id as _fidelity_account

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Simple portfolio format parser
# Columns: Ticker | Date Acquired | Amount | Price Acquired
# ---------------------------------------------------------------------------

def _parse_simple_portfolio(
    text: str,
    import_id: int,
) -> tuple[list[TradeCreate], list[str], str]:
    """
    Parse files with columns: Ticker, Date Acquired, Amount, Price Acquired.
    All rows are treated as BUY orders (purchases).
    Account ID is returned as 'PORTFOLIO' (not embedded in file).
    """
    trades: list[TradeCreate] = []
    errors: list[str] = []

    # Handle both tab and comma separated
    sample = text[:500]
    delimiter = "\t" if "\t" in sample else ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        return [], ["Empty file or no header row."], "PORTFOLIO"

    # Normalise header names
    norm = {re.sub(r"\s+", " ", f.strip().lower()): f for f in reader.fieldnames if f}

    def col(row: dict, *candidates: str) -> str:
        for c in candidates:
            orig = norm.get(c)
            if orig and orig in row:
                return (row[orig] or "").strip()
        return ""

    for row_num, row in enumerate(reader, start=2):
        symbol = col(row, "ticker", "symbol").upper().strip()
        if not symbol or not re.match(r"^[A-Z0-9.\-]+$", symbol):
            continue

        raw_date = col(row, "date acquired", "date", "trade date")
        trade_date = _parse_simple_date(raw_date)
        if trade_date is None:
            errors.append(f"Row {row_num}: unrecognised date '{raw_date}' for {symbol} — skipped")
            continue

        qty_raw = col(row, "amount", "quantity", "shares", "qty")
        qty = _parse_dec(qty_raw)
        if qty is None or qty <= 0:
            errors.append(f"Row {row_num}: invalid quantity '{qty_raw}' for {symbol} — skipped")
            continue

        price_raw = col(row, "price acquired", "price", "cost", "cost basis", "unit cost")
        price = _parse_dec(price_raw)
        if price is None or price <= 0:
            errors.append(f"Row {row_num}: invalid price '{price_raw}' for {symbol} — skipped")
            continue

        gross = (qty * price).quantize(Decimal("0.01"))

        trades.append(TradeCreate(
            source="portfolio",
            account_id="PORTFOLIO",
            trade_date=trade_date,
            symbol=symbol,
            side="BUY",
            quantity=qty,
            price=price,
            commission=Decimal("0"),
            gross_amount=gross,
            net_amount=-gross,
            label=None,
            is_hedge=False,
            fidelity_import_id=import_id,
            raw_data={"_row": row_num},
        ))

    return trades, errors, "PORTFOLIO"


def _parse_simple_date(raw: str) -> Optional[datetime]:
    raw = raw.strip().strip('"')
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_dec(raw: str) -> Optional[Decimal]:
    raw = raw.strip().replace(",", "").replace("$", "").replace("+", "")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def _detect_format(text: str) -> str:
    """Return one of: 'ibkr_activity', 'simple_portfolio', 'fidelity'."""
    # IBKR Activity Statement has a very distinct section marker
    if any(line.startswith("Trades,Header,") for line in text.splitlines()[:100]):
        return "ibkr_activity"

    # Find the header row (first non-empty line that looks like a real header)
    sample = text[:2000]
    delimiter = "\t" if "\t" in sample else ","
    for line in sample.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        # Simple portfolio format
        if "date acquired" in lower or "price acquired" in lower:
            return "simple_portfolio"
        # Both Fidelity formats have these keywords
        if any(k in lower for k in ("average cost basis", "action", "run date", "settlement date")):
            return "fidelity"
        # If it has "ticker" or "symbol" in first col + "price"
        cols = [c.strip().lower() for c in stripped.split(delimiter)]
        if cols and cols[0] in ("ticker", "symbol") and any("price" in c for c in cols):
            return "simple_portfolio"

    return "fidelity"  # safest default


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def auto_parse(
    text: str,
    import_id: int,
    account_id: Optional[str] = None,
) -> tuple[list[TradeCreate], list[str], str, str]:
    """
    Parse any trade CSV/TSV.
    Returns (trades, errors, resolved_account_id, source_label).
    """
    fmt = _detect_format(text)
    logger.info("auto_parse detected format: %s (import_id=%d)", fmt, import_id)

    if fmt == "ibkr_activity":
        detected = account_id or _ibkr_account(text)
        trades, errors, resolved_id = parse_ibkr_csv(text, detected, import_id)
        return trades, errors, resolved_id, "ibkr"

    if fmt == "simple_portfolio":
        trades, errors, resolved_id = _parse_simple_portfolio(text, import_id)
        if account_id:
            trades = [t.model_copy(update={"account_id": account_id}) for t in trades]
            resolved_id = account_id
        return trades, errors, resolved_id, "portfolio"

    # Fidelity (activity or positions — fidelity_parser handles both)
    detected = account_id or _fidelity_account(text)
    trades, errors, resolved_id = parse_fidelity_csv(text, detected, import_id)
    return trades, errors, resolved_id, "fidelity"

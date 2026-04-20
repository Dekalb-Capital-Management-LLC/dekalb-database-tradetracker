"""
IBKR Activity Statement CSV parser.

How to export from IBKR Client Portal:
  Performance & Reports → Activity Statements
    → select date range → Format: CSV → Download

The Activity Statement CSV has multiple sections. This parser finds the
"Trades" section and extracts executed equity orders.

Expected format (the default IBKR Activity Statement CSV):

  Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,...,Comm/Fee,...
  Trades,Data,Order,Stocks,USD,AAPL,"2024-01-15, 10:30:05",100,185.4,185.40,-18540.00,-1.00,...,O
  Trades,Data,Order,Stocks,USD,MSFT,"2024-01-16, 14:25:30",-50,380.00,380.00,19000.00,-1.00,...,C

Key fields:
  - Symbol          ticker
  - Date/Time       "2024-01-15, 10:30:05"
  - Quantity        positive = BUY, negative = SELL
  - T. Price        trade/execution price
  - Comm/Fee        commission, always negative (charge)
  - Proceeds        net cash: negative for buys, positive for sells
  - DataDiscriminator  "Order" = an actual trade (skip SubTotal, Total, ClosedLot)
  - Asset Category  "Stocks" — skip Options, Forex, Futures, etc.
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

logger = logging.getLogger(__name__)


def _parse_decimal(raw: str) -> Optional[Decimal]:
    raw = raw.strip().replace(",", "")
    if not raw or raw in ("--", "n/a", "N/A", ""):
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _parse_ibkr_date(raw: str) -> Optional[datetime]:
    """
    IBKR date formats:
      "2024-01-15, 10:30:05"   (Activity Statement)
      "2024-01-15"             (date only)
      "01/15/2024"
    """
    raw = raw.strip().strip('"')
    # Remove the comma+time if present: "2024-01-15, 10:30:05" -> "2024-01-15 10:30:05"
    raw = raw.replace(", ", " ")
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _normalise(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


# ---------------------------------------------------------------------------
# Activity Statement parser  (Trades,Header / Trades,Data rows)
# ---------------------------------------------------------------------------

def _parse_activity_statement(lines: list[str], account_id: str, import_id: int) -> tuple[list[TradeCreate], list[str]]:
    """Parse the multi-section Activity Statement CSV format."""
    trades: list[TradeCreate] = []
    errors: list[str] = []

    # Find the Trades header row
    header_cols: Optional[list[str]] = None
    for line in lines:
        if line.startswith("Trades,Header,"):
            # e.g. Trades,Header,DataDiscriminator,Asset Category,...
            parts = next(csv.reader([line]))
            # Strip the first two meta-columns: "Trades" and "Header"
            header_cols = [_normalise(c) for c in parts[2:]]
            break

    if header_cols is None:
        return [], ["Could not find 'Trades,Header,' row in the CSV. Make sure you downloaded an IBKR Activity Statement."]

    def col_idx(name: str) -> Optional[int]:
        for i, c in enumerate(header_cols):
            if name in c:
                return i
        return None

    # Map required columns
    col_disc    = col_idx("datadiscriminator")
    col_cat     = col_idx("asset category")
    col_symbol  = col_idx("symbol")
    col_date    = col_idx("date/time")
    col_qty     = col_idx("quantity")
    col_price   = col_idx("t. price")
    col_comm    = col_idx("comm")     # "comm/fee"
    col_proc    = col_idx("proceeds")

    missing = [n for n, i in [
        ("DataDiscriminator", col_disc), ("Asset Category", col_cat),
        ("Symbol", col_symbol), ("Date/Time", col_date),
        ("Quantity", col_qty), ("T. Price", col_price),
    ] if i is None]
    if missing:
        return [], [f"Missing expected columns: {', '.join(missing)}. Check IBKR export settings."]

    row_num = 0
    for line in lines:
        if not line.startswith("Trades,Data,"):
            continue
        row_num += 1
        parts = next(csv.reader([line]))
        data = parts[2:]  # strip "Trades","Data" prefix

        def get(idx: Optional[int]) -> str:
            if idx is None or idx >= len(data):
                return ""
            return data[idx].strip()

        discriminator = get(col_disc)
        if discriminator.lower() not in ("order", "trade", "execdetail"):
            continue  # skip SubTotal, Total, ClosedLot rows

        asset_cat = get(col_cat).lower()
        if asset_cat not in ("stocks", "equity", "equities"):
            continue  # skip Options, Forex, Futures, etc.

        symbol = get(col_symbol).upper().strip()
        if not symbol or not re.match(r"^[A-Z.\-]+$", symbol):
            errors.append(f"Row {row_num}: invalid symbol '{symbol}' — skipped")
            continue

        raw_date = get(col_date)
        trade_date = _parse_ibkr_date(raw_date)
        if trade_date is None:
            errors.append(f"Row {row_num}: invalid date '{raw_date}' for {symbol} — skipped")
            continue

        qty = _parse_decimal(get(col_qty))
        if qty is None or qty == 0:
            errors.append(f"Row {row_num}: invalid quantity for {symbol} — skipped")
            continue

        side = "BUY" if qty > 0 else "SELL"
        quantity = abs(qty)

        price = _parse_decimal(get(col_price))
        if price is None or price <= 0:
            errors.append(f"Row {row_num}: invalid price for {symbol} — skipped")
            continue

        commission = abs(_parse_decimal(get(col_comm)) or Decimal("0"))

        gross_amount = (quantity * price).quantize(Decimal("0.01"))

        proceeds = _parse_decimal(get(col_proc))
        if proceeds is not None:
            # IBKR Proceeds is already net of commissions
            net_amount = proceeds
        else:
            net_amount = (-gross_amount - commission) if side == "BUY" else (gross_amount - commission)

        raw_data = {header_cols[i]: data[i] for i in range(min(len(header_cols), len(data)))}

        trades.append(TradeCreate(
            source="ibkr",
            account_id=account_id,
            trade_date=trade_date,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            commission=commission,
            gross_amount=gross_amount,
            net_amount=net_amount,
            label=None,
            is_hedge=False,
            fidelity_import_id=import_id,
            raw_data=raw_data,
        ))

    if not trades and not errors:
        errors.append("No equity trade rows found. Confirm the date range includes trades and 'Stocks' is selected.")

    return trades, errors


# ---------------------------------------------------------------------------
# Simple flat CSV parser  (fallback / Flex Query style)
# ---------------------------------------------------------------------------

def _parse_simple_csv(csv_text: str, account_id: str, import_id: int) -> tuple[list[TradeCreate], list[str]]:
    """
    Fallback for simpler flat CSVs (e.g. IBKR Flex Query, Trade Activity report).
    Expects columns: Symbol, Date/Time (or Date), Quantity, T. Price (or Price), Comm/Fee (or Commission), ...
    """
    trades: list[TradeCreate] = []
    errors: list[str] = []

    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        return [], ["CSV has no header row."]

    norm = {_normalise(f): f for f in reader.fieldnames if f}

    def col(row: dict, *candidates: str) -> str:
        for c in candidates:
            orig = norm.get(c)
            if orig and orig in row:
                return (row[orig] or "").strip()
        return ""

    for row_num, row in enumerate(reader, start=2):
        symbol = col(row, "symbol").upper().strip()
        if not symbol or len(symbol) > 10 or not re.match(r"^[A-Z.\-]+$", symbol):
            continue

        raw_date = col(row, "date/time", "datetime", "date", "trade date", "trade_date")
        trade_date = _parse_ibkr_date(raw_date)
        if trade_date is None:
            errors.append(f"Row {row_num}: invalid date '{raw_date}' for {symbol} — skipped")
            continue

        qty_str = col(row, "quantity", "qty")
        qty = _parse_decimal(qty_str)
        if qty is None or qty == 0:
            errors.append(f"Row {row_num}: invalid quantity for {symbol} — skipped")
            continue

        side_raw = col(row, "side", "buy/sell", "action")
        if side_raw:
            side_upper = side_raw.upper()
            if any(k in side_upper for k in ("BUY", "BOT", "B")):
                side = "BUY"
                quantity = abs(qty)
            elif any(k in side_upper for k in ("SELL", "SLD", "S")):
                side = "SELL"
                quantity = abs(qty)
            else:
                errors.append(f"Row {row_num}: unrecognised side '{side_raw}' for {symbol} — skipped")
                continue
        else:
            side = "BUY" if qty > 0 else "SELL"
            quantity = abs(qty)

        price = _parse_decimal(col(row, "t. price", "t.price", "price", "trade price"))
        if price is None or price <= 0:
            errors.append(f"Row {row_num}: invalid price for {symbol} — skipped")
            continue

        commission = abs(_parse_decimal(col(row, "comm/fee", "comm", "commission", "fees")) or Decimal("0"))
        gross_amount = (quantity * price).quantize(Decimal("0.01"))

        proceeds_raw = col(row, "proceeds", "net amount", "net_amount")
        proceeds = _parse_decimal(proceeds_raw)
        if proceeds is not None:
            net_amount = proceeds
        else:
            net_amount = (-gross_amount - commission) if side == "BUY" else (gross_amount - commission)

        raw_data = {k.strip(): v.strip() for k, v in row.items() if k}

        trades.append(TradeCreate(
            source="ibkr",
            account_id=account_id,
            trade_date=trade_date,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            commission=commission,
            gross_amount=gross_amount,
            net_amount=net_amount,
            label=None,
            is_hedge=False,
            fidelity_import_id=import_id,
            raw_data=raw_data,
        ))

    return trades, errors


# ---------------------------------------------------------------------------
# Account ID extraction
# ---------------------------------------------------------------------------

def extract_conids(csv_text: str) -> list[dict]:
    """
    Parse the "Financial Instrument Information" section of an IBKR Activity
    Statement to extract symbol → conid mappings.

    Example rows:
      Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,...
      Financial Instrument Information,Data,Stocks,AAPL,APPLE INC,265598,...
      Financial Instrument Information,Data,Stocks,DTM,DT MIDSTREAM INC,495876055,...

    Returns list of {symbol, conid, description, asset_class}.
    """
    results: list[dict] = []
    header_cols: Optional[list[str]] = None
    for line in csv_text.splitlines():
        if line.startswith("Financial Instrument Information,Header,"):
            parts = next(csv.reader([line]))
            header_cols = [_normalise(c) for c in parts[2:]]
            continue
        if header_cols and line.startswith("Financial Instrument Information,Data,"):
            try:
                parts = next(csv.reader([line]))
            except Exception:
                continue
            data = parts[2:]

            def get(name: str) -> str:
                for i, c in enumerate(header_cols):
                    if name in c and i < len(data):
                        return data[i].strip()
                return ""

            symbol = get("symbol").upper().split(",")[0].strip()
            # Conid may be named "conid" or include it
            conid_raw = get("conid")
            if not symbol or not conid_raw:
                continue
            try:
                conid = int(conid_raw)
            except ValueError:
                continue
            if not re.match(r"^[A-Z0-9.\-]+$", symbol):
                continue
            results.append({
                "symbol": symbol,
                "conid": conid,
                "description": get("description") or None,
                "asset_class": get("asset category") or None,
            })
    return results


def extract_account_id(csv_text: str) -> Optional[str]:
    """
    Extract the account ID from an IBKR Activity Statement CSV.
    Looks for lines like: Account Information,Data,Account ID,F16173704
    """
    for line in csv_text.splitlines():
        if not line.startswith("Account Information,Data,"):
            continue
        try:
            parts = next(csv.reader([line]))
            # parts[2] is the field name, parts[3] is the value
            if len(parts) >= 4 and "account id" in parts[2].strip().lower():
                val = parts[3].strip()
                if val:
                    return val
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_ibkr_csv(
    csv_text: str,
    account_id: Optional[str],
    import_id: int,
) -> tuple[list[TradeCreate], list[str], str]:
    """
    Parse an IBKR CSV export into TradeCreate objects.
    Auto-detects Activity Statement format vs simple flat CSV.
    Auto-detects account_id from the file if not provided.
    Returns (trades, errors, resolved_account_id).
    """
    lines = csv_text.splitlines()

    # Auto-detect account ID from the Activity Statement header if not provided
    resolved_account_id = account_id or extract_account_id(csv_text) or "IBKR"

    # Detect Activity Statement format
    has_section_rows = any(line.startswith("Trades,Header,") for line in lines)

    if has_section_rows:
        logger.info("Detected IBKR Activity Statement format (import_id=%d, account=%s)", import_id, resolved_account_id)
        trades, errors = _parse_activity_statement(lines, resolved_account_id, import_id)
    else:
        logger.info("Detected simple IBKR CSV format (import_id=%d, account=%s)", import_id, resolved_account_id)
        trades, errors = _parse_simple_csv(csv_text, resolved_account_id, import_id)

    logger.info(
        "IBKR parse complete: %d trades, %d errors (import_id=%d)",
        len(trades), len(errors), import_id,
    )
    return trades, errors, resolved_account_id

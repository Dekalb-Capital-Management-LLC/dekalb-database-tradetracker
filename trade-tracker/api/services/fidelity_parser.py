"""
Fidelity CSV parser — handles two export formats:

FORMAT A: Activity & Orders (trade history)
  Columns: Run Date, Account, Action, Symbol, Security Description,
           Security Type, Quantity, Price ($), Commission ($), Fees ($), Amount ($), Settlement Date
  Detected by: has "Action" column

FORMAT B: Portfolio Positions (current holdings snapshot)
  Columns: Account Number, Account Name, Symbol, Description, Quantity,
           Last Price, Last Price Change, Current Value, Today's Gain/Loss Dollar,
           Today's Gain/Loss Percent, Total Gain/Loss Dollar, Total Gain/Loss Percent,
           Percent Of Account, Cost Basis Total, Average Cost Basis, Type
  Detected by: has "Average Cost Basis" column (no "Action" column)
  Positions are converted to synthetic BUY/SELL trades using avg cost basis as price.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import Optional

from models.schemas import TradeCreate

logger = logging.getLogger(__name__)

_BUY_KEYWORDS = {"YOU BOUGHT", "BOUGHT", "BUY", "PURCHASE", "REINVESTMENT"}
_SELL_KEYWORDS = {"YOU SOLD", "SOLD", "SELL"}

# Symbols to always skip
_SKIP_SYMBOLS = {"SPAXX**", "SPAXX", "FDRXX", "FDRXX**", "FCASH**"}


def _parse_fidelity_decimal(raw: str) -> Optional[Decimal]:
    """Handle Fidelity number formats: (1,234.56) = -1234.56, +$1.23 = 1.23"""
    raw = raw.strip().replace(",", "").replace("$", "").replace("+", "")
    if not raw or raw in ("--", "n/a", "N/A", ""):
        return None
    negative = False
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1]
        negative = True
    elif raw.startswith("-"):
        raw = raw[1:]
        negative = True
    try:
        value = Decimal(raw)
        return -value if negative else value
    except InvalidOperation:
        return None


def _parse_fidelity_date(raw: str) -> Optional[datetime]:
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%b-%d-%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _detect_side(action: str) -> Optional[str]:
    action_upper = action.upper().strip()
    for kw in _BUY_KEYWORDS:
        if kw in action_upper:
            return "BUY"
    for kw in _SELL_KEYWORDS:
        if kw in action_upper:
            return "SELL"
    return None


def _normalise_header(h: str) -> str:
    return re.sub(r"\s+", " ", h.strip().lower())


def _find_header_row(lines: list[str]) -> Optional[int]:
    """Scan for the data header line (contains 'symbol' and at least one other key column)."""
    for i, line in enumerate(lines):
        lower = line.lower()
        if "symbol" in lower and ("action" in lower or "average cost basis" in lower or "quantity" in lower):
            return i
    return None


def _clean_symbol(raw: str) -> str:
    """Normalize a Fidelity symbol: strip spaces, leading dashes, etc."""
    s = raw.strip()
    # Option symbols like " -ONDS260402P8" or "-PR260417P20"
    s = s.lstrip("- ")
    return s.upper()


def _is_skip_row(symbol: str, description: str) -> bool:
    """Return True for rows we should silently skip."""
    sym = symbol.upper().strip()
    desc = description.upper().strip()
    if not sym:
        return True
    if sym in _SKIP_SYMBOLS or sym.startswith("SPAXX") or sym.startswith("FDRXX"):
        return True
    if "PENDING ACTIVITY" in sym or "PENDING ACTIVITY" in desc:
        return True
    if "HELD IN MONEY MARKET" in desc:
        return True
    return False


# ---------------------------------------------------------------------------
# Format A: Activity / Trade History
# ---------------------------------------------------------------------------

def _parse_activity(
    lines: list[str],
    header_idx: int,
    account_id: str,
    import_id: int,
) -> tuple[list[TradeCreate], list[str]]:
    data_section = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(data_section))
    if reader.fieldnames is None:
        return [], ["CSV has no columns after header."]

    norm = {_normalise_header(f): f for f in reader.fieldnames if f}

    def col(row: dict, *candidates: str) -> str:
        for c in candidates:
            orig = norm.get(c)
            if orig and orig in row:
                return (row[orig] or "").strip()
        return ""

    trades: list[TradeCreate] = []
    errors: list[str] = []

    for row_num, row in enumerate(reader, start=header_idx + 2):
        raw_symbol = _clean_symbol(col(row, "symbol"))
        if not raw_symbol or len(raw_symbol) > 20:
            continue
        if not re.match(r"^[A-Z0-9.\-]+$", raw_symbol):
            continue

        description = col(row, "security description", "description")
        if _is_skip_row(raw_symbol, description):
            continue

        raw_action = col(row, "action")
        side = _detect_side(raw_action)
        if side is None:
            errors.append(f"Row {row_num}: unrecognised action '{raw_action}' for {raw_symbol} — skipped")
            continue

        raw_date = col(row, "run date", "date", "settlement date")
        trade_date = _parse_fidelity_date(raw_date)
        if trade_date is None:
            errors.append(f"Row {row_num}: invalid date '{raw_date}' for {raw_symbol} — skipped")
            continue

        quantity = _parse_fidelity_decimal(col(row, "quantity"))
        if quantity is None or quantity == 0:
            errors.append(f"Row {row_num}: invalid quantity for {raw_symbol} — skipped")
            continue
        quantity = abs(quantity)

        price = _parse_fidelity_decimal(col(row, "price ($)", "price"))
        if price is None or price <= 0:
            errors.append(f"Row {row_num}: invalid price for {raw_symbol} — skipped")
            continue

        commission = abs(_parse_fidelity_decimal(col(row, "commission ($)", "commission")) or Decimal(0))
        fees = abs(_parse_fidelity_decimal(col(row, "fees ($)", "fees")) or Decimal(0))
        total_commission = commission + fees

        gross = (quantity * price).quantize(Decimal("0.01"))
        net_raw = _parse_fidelity_decimal(col(row, "amount ($)", "amount"))
        net = net_raw if net_raw is not None else (
            (-gross - total_commission) if side == "BUY" else (gross - total_commission)
        )

        trades.append(TradeCreate(
            source="fidelity",
            account_id=account_id,
            trade_date=trade_date,
            symbol=raw_symbol,
            side=side,
            quantity=quantity,
            price=price,
            commission=total_commission,
            gross_amount=gross,
            net_amount=net,
            label=None,
            is_hedge=False,
            fidelity_import_id=import_id,
            raw_data={k.strip(): v.strip() for k, v in row.items() if k},
        ))

    return trades, errors


# ---------------------------------------------------------------------------
# Format B: Portfolio Positions Snapshot
# ---------------------------------------------------------------------------

def _parse_positions(
    lines: list[str],
    header_idx: int,
    account_id: str,
    import_id: int,
) -> tuple[list[TradeCreate], list[str]]:
    """
    Convert a Fidelity positions snapshot into synthetic trades.
    Each position becomes a BUY (long) or SELL (short) using avg cost basis as price.
    Trade date = today (the export date is not embedded per-row).
    """
    data_section = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(data_section))
    if reader.fieldnames is None:
        return [], ["CSV has no columns after header."]

    norm = {_normalise_header(f): f for f in reader.fieldnames if f}

    def col(row: dict, *candidates: str) -> str:
        for c in candidates:
            orig = norm.get(c)
            if orig and orig in row:
                return (row[orig] or "").strip()
        return ""

    trades: list[TradeCreate] = []
    errors: list[str] = []
    today = datetime.combine(date.today(), datetime.min.time())

    for row_num, row in enumerate(reader, start=header_idx + 2):
        raw_symbol = _clean_symbol(col(row, "symbol"))
        description = col(row, "description", "security description")

        if not raw_symbol:
            continue
        if _is_skip_row(raw_symbol, description):
            continue
        # Skip footer/disclaimer text rows
        if len(raw_symbol) > 25 or raw_symbol.startswith("BROKERAGE") or raw_symbol.startswith("THE DATA"):
            continue

        raw_qty = col(row, "quantity")
        quantity_raw = _parse_fidelity_decimal(raw_qty)
        if quantity_raw is None or quantity_raw == 0:
            continue

        # Determine side: negative qty = short (SELL), positive = long (BUY)
        side = "SELL" if quantity_raw < 0 else "BUY"
        quantity = abs(quantity_raw)

        # Price: use Average Cost Basis; fall back to Cost Basis Total / quantity
        avg_cost = _parse_fidelity_decimal(col(row, "average cost basis"))
        if avg_cost is None or avg_cost <= 0:
            cost_basis_total = _parse_fidelity_decimal(col(row, "cost basis total"))
            if cost_basis_total and quantity > 0:
                avg_cost = abs(cost_basis_total) / quantity
        if avg_cost is None or avg_cost <= 0:
            # Last resort: use Last Price
            avg_cost = _parse_fidelity_decimal(col(row, "last price"))
        if avg_cost is None or avg_cost <= 0:
            errors.append(f"Row {row_num}: no valid price/cost basis for {raw_symbol} — skipped")
            continue

        gross = (quantity * avg_cost).quantize(Decimal("0.01"))
        net = (-gross) if side == "BUY" else gross  # no commission info in positions export

        trades.append(TradeCreate(
            source="fidelity",
            account_id=account_id,
            trade_date=today,
            symbol=raw_symbol,
            side=side,
            quantity=quantity,
            price=avg_cost,
            commission=Decimal(0),
            gross_amount=gross,
            net_amount=net,
            label=None,
            is_hedge=False,
            fidelity_import_id=import_id,
            raw_data={k.strip(): v.strip() for k, v in row.items() if k},
        ))

    return trades, errors


# ---------------------------------------------------------------------------
# Public entry point — auto-detects format
# ---------------------------------------------------------------------------

def extract_account_id(csv_text: str) -> Optional[str]:
    """
    Auto-detect the Fidelity account ID from the CSV data.
    The Activity CSV has an "Account" column with values like "Z12345678".
    The Positions CSV has an "Account Number" column.
    """
    lines = csv_text.splitlines()
    header_idx = _find_header_row(lines)
    if header_idx is None:
        return None
    try:
        header_lower = lines[header_idx].lower()
        data_section = "\n".join(lines[header_idx:])
        reader = csv.DictReader(io.StringIO(data_section))
        norm = {_normalise_header(f): f for f in (reader.fieldnames or []) if f}

        def col(row: dict, *candidates: str) -> str:
            for c in candidates:
                orig = norm.get(c)
                if orig and orig in row:
                    return (row[orig] or "").strip()
            return ""

        for row in reader:
            # Activity format: "Account" column
            acct = col(row, "account", "account number")
            if acct and len(acct) >= 4:
                return acct
    except Exception:
        pass
    return None


def parse_fidelity_csv(
    csv_text: str,
    account_id: Optional[str],
    import_id: int,
) -> tuple[list[TradeCreate], list[str], str]:
    """
    Parse a Fidelity CSV export into TradeCreate objects.
    Automatically detects whether it's an Activity/Orders export or a
    Portfolio Positions snapshot and uses the appropriate parser.
    Auto-detects account_id from the file if not provided.
    Returns (trades, errors, resolved_account_id).
    """
    resolved_account_id = account_id or extract_account_id(csv_text) or "FIDELITY"

    lines = csv_text.splitlines()
    header_idx = _find_header_row(lines)
    if header_idx is None:
        return [], ["Could not find header row. Expected columns: Symbol, Action or Average Cost Basis."], resolved_account_id

    header_lower = lines[header_idx].lower()
    if "average cost basis" in header_lower:
        logger.info("Fidelity CSV detected as POSITIONS format (import_id=%d, account=%s)", import_id, resolved_account_id)
        trades, errors = _parse_positions(lines, header_idx, resolved_account_id, import_id)
    else:
        logger.info("Fidelity CSV detected as ACTIVITY format (import_id=%d, account=%s)", import_id, resolved_account_id)
        trades, errors = _parse_activity(lines, header_idx, resolved_account_id, import_id)

    return trades, errors, resolved_account_id

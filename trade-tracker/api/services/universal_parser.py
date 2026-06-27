"""
Universal trade file parser.
Reads .xlsx files with multiple sheets (each sheet: Ticker | Date Acquired | Amount | Price Acquired).
Aggregates by symbol, returns trades list.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

import pandas as pd

from models.schemas import TradeCreate

logger = logging.getLogger(__name__)

_DATE_FORMATS = ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%d-%b-%Y", "%b %d, %Y")


def _parse_date(raw: str) -> Optional[datetime]:
    raw = str(raw).strip().strip('"')
    if not raw:
        return None
    try:
        ts = pd.to_datetime(raw, errors="raise")
        if pd.notna(ts):
            return ts.to_pydatetime()
    except Exception:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_number(raw) -> Optional[Decimal]:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace("$", "").replace("+", "")
    if not s or s.lower() in ("nan", "--", "n/a", ""):
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        v = Decimal(s)
        return -v if neg else v
    except InvalidOperation:
        return None


def _norm(col: str) -> str:
    return re.sub(r"\s+", " ", str(col).strip().lower())


def _pick(row, *names: str) -> str:
    for n in names:
        if n in row.index:
            v = row[n]
            if v is not None:
                s = str(v).strip()
                if s and s.lower() not in ("nan", ""):
                    return s
    return ""


def parse_portfolio_xlsx(raw_bytes: bytes, import_id: int, account_id: str = "PORTFOLIO") -> tuple[list[TradeCreate], list[str]]:
    """
    Parse a multi-sheet .xlsx portfolio file.
    Each sheet should have: Ticker | Date Acquired | Amount | Price Acquired
    Returns (trades, errors).
    """
    try:
        sheets: dict = pd.read_excel(
            io.BytesIO(raw_bytes),
            engine="openpyxl",
            header=None,
            dtype=str,
            sheet_name=None,
        )
    except Exception as exc:
        return [], [f"Could not open file: {exc}"]

    trades: list[TradeCreate] = []
    errors: list[str] = []

    for sheet_name, df in sheets.items():
        df = df.fillna("").map(lambda x: str(x).replace("\x00", "") if isinstance(x, str) else x)

        # Find header row (contains "ticker" or "symbol")
        header_idx = None
        for i in range(min(30, len(df))):
            cells = [_norm(c) for c in df.iloc[i].tolist()]
            if any(c in ("ticker", "symbol") for c in cells):
                header_idx = i
                break
        if header_idx is None:
            logger.info("Sheet '%s': no Ticker/Symbol header found, skipping", sheet_name)
            continue

        headers = [_norm(c) for c in df.iloc[header_idx].tolist()]
        data_df = df.iloc[header_idx + 1:].copy()
        data_df.columns = list(headers) + [f"_x{i}" for i in range(len(data_df.columns) - len(headers))]

        for row_num, (_, row) in enumerate(data_df.iterrows(), start=header_idx + 2):
            symbol = _pick(row, "ticker", "symbol").upper().strip()
            if not symbol:
                continue
            symbol = symbol.split()[0].split("(")[0].strip()
            if not re.match(r"^[A-Z0-9.\-]+$", symbol) or len(symbol) > 10:
                continue

            raw_date = _pick(row, "date acquired", "date", "trade date", "purchase date")
            trade_date = _parse_date(raw_date)
            if trade_date is None:
                errors.append(f"[{sheet_name}] Row {row_num}: bad date '{raw_date}' for {symbol}")
                continue

            qty = _parse_number(_pick(row, "amount", "quantity", "shares", "qty", "# shares"))
            if qty is None or qty <= 0:
                errors.append(f"[{sheet_name}] Row {row_num}: bad quantity for {symbol}")
                continue

            price = _parse_number(_pick(row, "price acquired", "price", "cost", "unit cost", "avg price", "cost basis"))
            if price is None or price <= 0:
                errors.append(f"[{sheet_name}] Row {row_num}: bad price for {symbol}")
                continue

            gross = (qty * price).quantize(Decimal("0.01"))
            trades.append(TradeCreate(
                # 'portfolio' was never a real source value (schema only ever
                # documented 'ibkr' | 'fidelity') — using it here meant every
                # custom-sheet upload was invisible to anything that filtered
                # by source='fidelity' (the Fidelity tab, trade log filter, etc).
                source="fidelity",
                account_id=account_id,
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
                raw_data={"sheet": sheet_name, "row": row_num},
            ))

    logger.info("parse_portfolio_xlsx: %d trades, %d errors from %d sheets", len(trades), len(errors), len(sheets))
    return trades, errors

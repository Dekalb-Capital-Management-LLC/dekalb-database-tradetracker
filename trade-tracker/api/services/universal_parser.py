"""
Universal trade file parser.

Uses pandas to read .csv, .tsv, .xlsx, .xlsm, .xls. Auto-detects one of three
supported formats and dispatches to the appropriate parser:

  1. IBKR Activity Statement — multi-section CSV, starts with "Statement,Header,..."
                               and contains "Trades,Header,..." rows.
  2. Simple portfolio        — columns: Ticker, Date Acquired, Amount, Price Acquired
  3. Fidelity                — has "Action" / "Run Date" / "Average Cost Basis" columns

Returns (trades, errors, account_id, source_label).
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
from services.ibkr_parser import (
    parse_ibkr_csv,
    extract_account_id as _ibkr_account,
    extract_conids as _ibkr_conids,
)
from services.fidelity_parser import parse_fidelity_csv, extract_account_id as _fidelity_account

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# File reading — produces both bytes and a best-effort text string for parsers
# that need the raw multi-section text (IBKR Activity Statement).
# ---------------------------------------------------------------------------

def read_spreadsheet(raw_bytes: bytes, filename: str) -> tuple[pd.DataFrame, str]:
    """
    Read any spreadsheet into a DataFrame of strings, plus a text representation
    used by section-aware parsers. Returns (df, text).
    Excel files: ALL sheets are stacked vertically (separated by blank rows) so
    multi-tab portfolio workbooks parse correctly.
    """
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        engine = "openpyxl" if name.endswith((".xlsx", ".xlsm")) else "xlrd"
        # Read every sheet — a dict of {sheet_name: DataFrame}
        sheets = pd.read_excel(
            io.BytesIO(raw_bytes), engine=engine, header=None,
            dtype=str, sheet_name=None,
        )

        cleaned: list[pd.DataFrame] = []
        for sheet_df in sheets.values():
            sd = sheet_df.fillna("").replace("\x00", "", regex=True)
            sd = sd.applymap(lambda x: str(x).replace("\x00", "") if isinstance(x, str) else x)
            cleaned.append(sd)

        if not cleaned:
            df = pd.DataFrame()
        else:
            # Pad to widest column count so concat doesn't drop data
            max_cols = max(c.shape[1] for c in cleaned)
            padded = []
            for c in cleaned:
                if c.shape[1] < max_cols:
                    for i in range(max_cols - c.shape[1]):
                        c[f"_pad_{i}"] = ""
                padded.append(c)
                # Insert one blank row between sheets so header detection
                # doesn't carry across tab boundaries
                padded.append(pd.DataFrame([[""] * max_cols], columns=padded[-1].columns))
            df = pd.concat(padded, ignore_index=True).fillna("")

        text = df.to_csv(index=False, header=False).replace("\x00", "")
        return df, text

    # CSV / TSV / plain text — decode bytes with fallback encodings
    text: Optional[str] = None
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw_bytes.decode("latin-1", errors="replace")

    # Load as dataframe with sniffed delimiter, header=None so no column loss
    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep=None,
            engine="python",
            header=None,
            dtype=str,
            skip_blank_lines=False,
            on_bad_lines="skip",
        ).fillna("")
    except Exception as exc:
        logger.warning("pandas read_csv failed (%s), trying comma delim", exc)
        df = pd.read_csv(
            io.StringIO(text),
            sep=",",
            header=None,
            dtype=str,
            skip_blank_lines=False,
            on_bad_lines="skip",
        ).fillna("")
    return df, text


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def _detect_format(df: pd.DataFrame, text: str) -> str:
    """Return 'ibkr_activity', 'simple_portfolio', or 'fidelity'."""
    # IBKR Activity Statement marker is unambiguous
    for line in text.splitlines()[:200]:
        if line.startswith("Trades,Header,") or line.startswith("Statement,Header,"):
            return "ibkr_activity"

    # Look at column headers — check first 20 rows for one that looks like a header
    lower_blob = text[:5000].lower()

    if "date acquired" in lower_blob or "price acquired" in lower_blob:
        return "simple_portfolio"

    if any(kw in lower_blob for kw in ("average cost basis", "run date", "settlement date")):
        return "fidelity"

    # Heuristic: if first column says "ticker"/"symbol" and any column has "price"
    for r in range(min(20, len(df))):
        row = [str(c).strip().lower() for c in df.iloc[r].tolist()]
        if row and row[0] in ("ticker", "symbol") and any("price" in c or "amount" in c for c in row):
            return "simple_portfolio"
        if "action" in row:
            return "fidelity"

    return "fidelity"  # safest default


# ---------------------------------------------------------------------------
# Simple portfolio parser (pandas-based)
# Expected columns (case/space insensitive):
#     Ticker | Date Acquired | Amount | Price Acquired
# ---------------------------------------------------------------------------

_DATE_FORMATS = ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%d-%b-%Y", "%b %d, %Y")


def _parse_date(raw: str) -> Optional[datetime]:
    raw = str(raw).strip().strip('"')
    if not raw:
        return None
    # Try pandas' flexible parser first
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
    if not s or s.lower() in ("nan", "--", "n/a"):
        return None
    # Parenthesis negative: (123.45)
    neg = False
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
        neg = True
    try:
        v = Decimal(s)
        return -v if neg else v
    except InvalidOperation:
        return None


def _norm(col: str) -> str:
    return re.sub(r"\s+", " ", str(col).strip().lower())


def _find_all_simple_header_rows(df: pd.DataFrame) -> list[int]:
    """
    Find every row that looks like a 'Ticker | Date | Amount | Price'-style
    header. Used for multi-sheet workbooks where each tab has its own header.
    """
    hits: list[int] = []
    for i in range(len(df)):
        cells = [_norm(c) for c in df.iloc[i].tolist()]
        if any(c in ("ticker", "symbol") for c in cells):
            hits.append(i)
    return hits


def _parse_simple_portfolio_pd(df: pd.DataFrame, import_id: int) -> tuple[list[TradeCreate], list[str], str]:
    trades: list[TradeCreate] = []
    errors: list[str] = []

    header_idxs = _find_all_simple_header_rows(df)
    if not header_idxs:
        return [], ["Could not find a header row containing 'Ticker' or 'Symbol'."], "PORTFOLIO"

    def pick(row, *names: str) -> str:
        for n in names:
            if n in row.index:
                v = row[n]
                if v is None:
                    continue
                s = str(v).strip()
                if s and s.lower() != "nan":
                    return s
        return ""

    # Iterate each header section: rows from header+1 up to the next header (or EOF)
    for sec_idx, header_idx in enumerate(header_idxs):
        end_idx = header_idxs[sec_idx + 1] if sec_idx + 1 < len(header_idxs) else len(df)
        headers = [_norm(c) for c in df.iloc[header_idx].tolist()]
        data_df = df.iloc[header_idx + 1:end_idx].copy()
        data_df.columns = headers + [f"_extra_{i}" for i in range(len(data_df.columns) - len(headers))]

        for row_num, (_, row) in enumerate(data_df.iterrows(), start=header_idx + 2):
            symbol = pick(row, "ticker", "symbol").upper().strip()
            if not symbol:
                continue
            symbol = symbol.split()[0].split("(")[0].strip()
            if not re.match(r"^[A-Z0-9.\-]+$", symbol) or len(symbol) > 10:
                continue

            raw_date = pick(row, "date acquired", "date", "trade date", "purchase date")
            trade_date = _parse_date(raw_date)
            if trade_date is None:
                errors.append(f"Row {row_num}: unrecognised date '{raw_date}' for {symbol} — skipped")
                continue

            qty_raw = pick(row, "amount", "quantity", "shares", "qty", "# shares")
            qty = _parse_number(qty_raw)
            if qty is None or qty <= 0:
                errors.append(f"Row {row_num}: invalid quantity '{qty_raw}' for {symbol} — skipped")
                continue

            price_raw = pick(row, "price acquired", "price", "cost", "cost basis", "unit cost", "avg price")
            price = _parse_number(price_raw)
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
                raw_data={"_row": row_num, "_section": sec_idx},
            ))

    logger.info("Simple portfolio: %d trades, %d errors (from %d sheet section(s))",
                len(trades), len(errors), len(header_idxs))
    return trades, errors, "PORTFOLIO"


# ---------------------------------------------------------------------------
# Conid persistence — called after successful IBKR Activity Statement import
# ---------------------------------------------------------------------------

async def persist_ibkr_conids(pool, text: str) -> int:
    """
    Extract symbol→conid rows from IBKR Activity Statement "Financial Instrument
    Information" section and upsert them into instrument_conids.
    Returns the number of rows written.
    """
    rows = _ibkr_conids(text)
    if not rows:
        return 0
    for r in rows:
        await pool.execute(
            """
            INSERT INTO instrument_conids (symbol, conid, description, asset_class, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (symbol) DO UPDATE
            SET conid = EXCLUDED.conid,
                description = COALESCE(EXCLUDED.description, instrument_conids.description),
                asset_class = COALESCE(EXCLUDED.asset_class, instrument_conids.asset_class),
                updated_at = NOW()
            """,
            r["symbol"], r["conid"], r.get("description"), r.get("asset_class"),
        )
    logger.info("Persisted %d IBKR conids", len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def auto_parse(
    raw_bytes: bytes,
    filename: str,
    import_id: int,
    account_id: Optional[str] = None,
) -> tuple[list[TradeCreate], list[str], str, str, str]:
    """
    Parse any supported spreadsheet (.csv/.tsv/.xlsx/.xlsm/.xls).
    Returns (trades, errors, resolved_account_id, source_label, text_for_conid_extraction).
    `text` is returned so the caller can persist IBKR conids after insert.
    """
    df, text = read_spreadsheet(raw_bytes, filename)
    fmt = _detect_format(df, text)
    logger.info("auto_parse detected format: %s (import_id=%d, file=%s)", fmt, import_id, filename)

    if fmt == "ibkr_activity":
        resolved_id = account_id or _ibkr_account(text) or "IBKR"
        trades, errors, resolved_id = parse_ibkr_csv(text, resolved_id, import_id)
        return trades, errors, resolved_id, "ibkr", text

    if fmt == "simple_portfolio":
        trades, errors, resolved_id = _parse_simple_portfolio_pd(df, import_id)
        if account_id:
            trades = [t.model_copy(update={"account_id": account_id}) for t in trades]
            resolved_id = account_id
        return trades, errors, resolved_id, "portfolio", text

    # Fidelity (activity or positions) — still uses the existing CSV parser.
    resolved_id = account_id or _fidelity_account(text) or "FIDELITY"
    trades, errors, resolved_id = parse_fidelity_csv(text, resolved_id, import_id)
    return trades, errors, resolved_id, "fidelity", text

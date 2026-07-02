"""
FirstRateData bundle reader.

FirstRateData distributes market data as CSV files inside ZIP archives or
extracted directories. This module reads those bundle files locally so tests can
use the public sample ZIP and production can point at a cached full bundle.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Iterator, Optional

import requests

import config
from models.schemas import HistoricalBar, PriceQuote

logger = logging.getLogger(__name__)

_TIMEFRAME_ALIASES = {
    "1d": "1day",
    "1day": "1day",
    "daily": "1day",
    "day": "1day",
    "1m": "1min",
    "1min": "1min",
    "minute": "1min",
    "5m": "5min",
    "5min": "5min",
    "30m": "30min",
    "30min": "30min",
    "1h": "1hour",
    "1hour": "1hour",
    "hour": "1hour",
}


def _normal_timeframe(timeframe: str) -> str:
    return _TIMEFRAME_ALIASES.get(timeframe.lower().strip(), timeframe.lower().strip())


def _decimal(value: object) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _parse_date(value: str) -> Optional[date]:
    text = value.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _bar_from_row(row: dict[str, str]) -> Optional[HistoricalBar]:
    normalized = {k.strip().lower(): v for k, v in row.items() if k is not None}
    raw_date = normalized.get("timestamp") or normalized.get("datetime") or normalized.get("date")
    if not raw_date:
        return None

    bar_date = _parse_date(raw_date)
    open_px = _decimal(normalized.get("open"))
    high_px = _decimal(normalized.get("high"))
    low_px = _decimal(normalized.get("low"))
    close_px = _decimal(normalized.get("close"))
    if bar_date is None or None in (open_px, high_px, low_px, close_px):
        return None

    raw_volume = normalized.get("volume") or "0"
    try:
        volume = int(Decimal(str(raw_volume).strip() or "0"))
    except (InvalidOperation, ValueError):
        volume = 0

    return HistoricalBar(
        date=bar_date,
        open=open_px,
        high=high_px,
        low=low_px,
        close=close_px,
        volume=volume,
    )


class FirstRateDataProvider:
    """Reads historical bars from a FirstRateData ZIP file or extracted folder."""

    def __init__(self, data_path: str | Path | None = None):
        raw_path = str(data_path or config.FIRST_RATE_DATA_PATH).strip()
        self.data_path = Path(raw_path).expanduser() if raw_path else None
        self._catalog: list[str] | None = None

    @property
    def is_configured(self) -> bool:
        return self.data_path is not None and self.data_path.exists()

    def _iter_csv_names(self) -> list[str]:
        if self._catalog is not None:
            return self._catalog
        if not self.is_configured:
            self._catalog = []
        elif self.data_path.is_dir():
            self._catalog = [str(path) for path in self.data_path.rglob("*.csv")]
        elif self.data_path.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(self.data_path) as archive:
                    self._catalog = [
                        name for name in archive.namelist()
                        if name.lower().endswith(".csv")
                    ]
            except zipfile.BadZipFile:
                logger.warning("FirstRateData path is not a valid ZIP: %s", self.data_path)
                self._catalog = []
        else:
            self._catalog = [str(self.data_path)] if self.data_path.suffix.lower() == ".csv" else []
        return self._catalog

    def _match_csv_name(self, symbol: str, timeframe: str) -> Optional[str]:
        names = self._iter_csv_names()
        if not names:
            return None

        sym = symbol.upper().strip()
        tf = _normal_timeframe(timeframe)
        exact_names = {
            f"{sym}_{tf}.csv".lower(),
            f"{sym}_{tf}_sample.csv".lower(),
        }
        for name in names:
            if Path(name).name.lower() in exact_names:
                return name

        pattern = re.compile(rf"^{re.escape(sym)}_{re.escape(tf)}(?:_.+)?\.csv$", re.IGNORECASE)
        for name in names:
            if pattern.match(Path(name).name):
                return name
        return None

    @contextmanager
    def _open_csv_text(self, name: str) -> Iterator[io.TextIOBase]:
        if self.data_path is None:
            raise FileNotFoundError("FirstRateData path is not configured")
        if self.data_path.is_dir():
            with open(name, encoding="utf-8-sig", newline="") as handle:
                yield handle
            return
        if self.data_path.suffix.lower() == ".zip":
            with zipfile.ZipFile(self.data_path) as archive:
                with archive.open(name) as raw:
                    with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
                        yield text
            return
        with open(self.data_path, encoding="utf-8-sig", newline="") as handle:
            yield handle

    def iter_historical_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        timeframe: str = "1day",
    ) -> Iterable[HistoricalBar]:
        csv_name = self._match_csv_name(symbol, timeframe)
        if not csv_name:
            return

        with self._open_csv_text(csv_name) as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                bar = _bar_from_row(row)
                if bar and start <= bar.date <= end:
                    yield bar

    def get_historical_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        timeframe: str = "1day",
    ) -> list[HistoricalBar]:
        return sorted(self.iter_historical_bars(symbol, start, end, timeframe), key=lambda bar: bar.date)

    def get_latest_quote(self, symbol: str) -> Optional[PriceQuote]:
        csv_name = self._match_csv_name(symbol, "1day")
        if not csv_name:
            return None

        previous: Optional[HistoricalBar] = None
        latest: Optional[HistoricalBar] = None
        with self._open_csv_text(csv_name) as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                bar = _bar_from_row(row)
                if not bar:
                    continue
                previous = latest
                latest = bar

        if latest is None:
            return None

        change = None
        change_pct = None
        previous_close = previous.close if previous else None
        if previous_close and previous_close > 0:
            change = latest.close - previous_close
            change_pct = (change / previous_close * Decimal("100")).quantize(Decimal("0.000001"))

        return PriceQuote(
            symbol=symbol.upper(),
            price=latest.close,
            change=change,
            change_pct=change_pct,
            previous_close=previous_close,
            source="firstrate",
            as_of=datetime.combine(latest.date, datetime.min.time()),
        )

    def get_historical_close_map(
        self,
        symbols: list[str],
        start: date,
        end: date,
    ) -> dict[str, dict[date, Decimal]]:
        result: dict[str, dict[date, Decimal]] = {symbol.upper(): {} for symbol in symbols}
        for symbol in result:
            result[symbol] = {
                bar.date: bar.close
                for bar in self.get_historical_bars(symbol, start, end)
            }
        return result


def download_bundle(
    destination_path: str | Path | None = None,
    download_url: str | None = None,
) -> Path:
    """
    Download a FirstRateData bundle ZIP to the configured cache path.

    Production can set FIRST_RATE_DATA_DOWNLOAD_URL to the vendor-provided
    authenticated URL and FIRST_RATE_DATA_API_KEY when bearer auth is required.
    """
    url = (download_url or config.FIRST_RATE_DATA_DOWNLOAD_URL).strip()
    if not url:
        raise ValueError("FIRST_RATE_DATA_DOWNLOAD_URL is not configured")

    destination = Path(destination_path or config.FIRST_RATE_DATA_PATH).expanduser()
    if destination.is_dir():
        destination = destination / "firstratedata_bundle.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)

    headers = {}
    if config.FIRST_RATE_DATA_API_KEY:
        headers["Authorization"] = f"Bearer {config.FIRST_RATE_DATA_API_KEY}"

    with requests.get(
        url,
        headers=headers,
        stream=True,
        timeout=config.FIRST_RATE_DATA_TIMEOUT_SECONDS,
    ) as response:
        response.raise_for_status()
        with open(destination, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)

    return destination


_provider: FirstRateDataProvider | None = None


def get_provider() -> FirstRateDataProvider:
    global _provider
    if _provider is None:
        _provider = FirstRateDataProvider()
    return _provider


def reset_provider() -> None:
    global _provider
    _provider = None

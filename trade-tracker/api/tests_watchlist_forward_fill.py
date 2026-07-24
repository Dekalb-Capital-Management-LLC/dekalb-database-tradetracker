"""ponytail: forward-fill watchlist cum % across empty days (no chart gaps)."""
from datetime import date
from decimal import Decimal


def forward_fill(dates, by_date):
    last = None
    out = []
    for d in dates:
        if d in by_date and by_date[d] is not None:
            last = by_date[d]
        out.append(last)
    return out


dates = [date(2026, 2, 1), date(2026, 2, 2), date(2026, 2, 3), date(2026, 2, 4)]
by = {date(2026, 2, 1): Decimal("1"), date(2026, 2, 3): Decimal("2")}
assert forward_fill(dates, by) == [Decimal("1"), Decimal("1"), Decimal("2"), Decimal("2")]
assert forward_fill(dates, {}) == [None, None, None, None]
print("watchlist_forward_fill ok")

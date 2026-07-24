"""ponytail: watchlist overlay merges period TWR + purchase markers."""
from datetime import date
from decimal import Decimal

from models.schemas import PerformancePoint


def merge(main, by_date, markers):
    return [
        p.model_copy(update={
            "watchlist_cumulative_pct": by_date.get(p.date),
            "purchase_markers": markers.get(p.date) or None,
        })
        for p in main
    ]


base = PerformancePoint(
    date=date(2026, 1, 2),
    portfolio_nav=Decimal("100"),
    portfolio_pct_change=Decimal("1"),
    spy_pct_change=Decimal("0.5"),
    spy_cumulative_pct=Decimal("0.5"),
    portfolio_cumulative_pct=Decimal("1"),
)
assert merge([base], {}, {})[0].watchlist_cumulative_pct is None
out = merge(
    [base],
    {date(2026, 1, 2): Decimal("-2.5")},
    {date(2026, 1, 2): ["AAPL"]},
)
assert out[0].watchlist_cumulative_pct == Decimal("-2.5")
assert out[0].purchase_markers == ["AAPL"]
print("watchlist_twr_overlay ok")

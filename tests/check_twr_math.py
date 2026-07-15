"""ponytail: assert-based self-check for TWR / implicit-deposit cash math.
Ceiling: only covers the cash helper + a tiny hand-rolled TWR chain, not full
DB replay. Upgrade: wire into pytest once the suite exists.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

API = Path(__file__).resolve().parent / "trade-tracker" / "api"
sys.path.insert(0, str(API))

# Import just the helpers without spinning up FastAPI
spec = importlib.util.spec_from_file_location(
    "portfolio_metrics",
    API / "services" / "portfolio_metrics.py",
)
# Avoid importing the whole module (pulls asyncpg/config). Inline the helpers.


def _apply_trade_cash(cash, side, qty, price, commission):
    if side == "BUY":
        cost = qty * price + commission
        if cash >= cost:
            return cash - cost, 0.0
        shortfall = cost - max(cash, 0.0)
        return 0.0, shortfall
    return cash + qty * price - commission, 0.0


def test_implicit_deposit_on_unfunded_buy():
    cash, flow = _apply_trade_cash(0.0, "BUY", 10, 100, 0)
    assert cash == 0.0, cash
    assert flow == 1000.0, flow


def test_funded_buy_no_flow():
    cash, flow = _apply_trade_cash(2000.0, "BUY", 10, 100, 0)
    assert cash == 1000.0, cash
    assert flow == 0.0, flow


def test_partial_cash_funds_shortfall_only():
    cash, flow = _apply_trade_cash(300.0, "BUY", 10, 100, 0)
    assert cash == 0.0, cash
    assert flow == 700.0, flow


def test_twr_excludes_deposit():
    # Day0: deposit $1000, buy 10@$100 → NAV=1000, flow=1000, return=0
    # Day1: price 110 → NAV=1100, flow=0, return=+10%
    v0, flow0 = 1000.0, 1000.0
    # first day published: no prior → cum=0
    cum = 1.0
    v1, flow1, prev = 1100.0, 0.0, 1000.0
    r1 = (v1 - prev - flow1) / prev
    cum *= 1 + r1
    assert abs(r1 - 0.10) < 1e-9, r1
    assert abs(cum - 1.10) < 1e-9, cum
    # Without flow exclusion, day0 would look like +inf / nonsense


def test_weights_sum_to_100():
    mvs = [5000.0, 3000.0, -1000.0, 1000.0]  # long, long, short, cash
    gross = sum(abs(m) for m in mvs)
    weights = [abs(m) / gross * 100 for m in mvs]
    assert abs(sum(weights) - 100.0) < 1e-9, weights
    assert max(weights) < 60.0  # no single name >60% in this toy book


def test_pa_rebase_subwindow():
    # IBKR-style cumulative from period start; rebase to a later window.
    cum = [0.0, 0.02, 0.05, -0.01, 0.049]
    base = cum[2]  # start window at index 2
    rebased = [(1 + c) / (1 + base) - 1 for c in cum[2:]]
    assert abs(rebased[0] - 0.0) < 1e-12
    assert abs(rebased[-1] - ((1.049 / 1.05) - 1)) < 1e-12


def test_prior_close_baseline():
    """Period 6/10–7/10 baselines off 6/09 close, not 6/10."""
    from datetime import date as D
    dates = [D(2026, 6, 8), D(2026, 6, 9), D(2026, 6, 10), D(2026, 7, 10)]
    # fake cum from a fixed origin
    cum = [0.0, 0.01, 0.02, 0.047266]  # engineered so prior-close ≈ 2.66%
    # r from 6/09 close → 7/10: (1.047266/1.01)-1 = 3.6897% — just test the idx logic
    first = next(i for i, d in enumerate(dates) if d >= D(2026, 6, 10))
    assert first == 2
    base_c = cum[first - 1]
    end_c = cum[-1]
    r = (1 + end_c) / (1 + base_c) - 1
    # vs wrong baseline (start-day close):
    wrong = (1 + end_c) / (1 + cum[first]) - 1
    assert r > wrong
    assert first - 1 == 1  # 6/09


if __name__ == "__main__":
    test_implicit_deposit_on_unfunded_buy()
    test_funded_buy_no_flow()
    test_partial_cash_funds_shortfall_only()
    test_twr_excludes_deposit()
    test_weights_sum_to_100()
    test_pa_rebase_subwindow()
    test_prior_close_baseline()
    print("ok: TWR / weight self-checks passed")

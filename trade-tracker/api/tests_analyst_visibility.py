"""ponytail: ticker visibility rules for analyst dashboard filter."""

def matches(symbol, label, view_mode, categories, tickers):
    # tickers: list of (symbol, visible)
    if view_mode == "categories":
        if not categories:
            return True
        return bool(label and label.lower() in categories)
    known = {s: v for s, v in tickers}
    row = known.get(symbol.upper())
    return row is True

assert matches("AAPL", None, "tickers", [], [("AAPL", True)]) is True
assert matches("AAPL", None, "tickers", [], [("AAPL", False)]) is False
assert matches("AAPL", None, "tickers", [], []) is False  # unknown → hide until answered
assert matches("MSFT", "tech", "categories", ["tech"], []) is True
assert matches("MSFT", None, "categories", ["tech"], []) is False
assert matches("MSFT", None, "categories", [], []) is True
print("analyst_visibility ok")

"""ponytail: lot-only basket TWR must treat sells as withdrawals."""

def lot_only_flow(side: str, qty: float, price: float, commission: float = 0.0) -> float:
    if side == "BUY":
        return qty * price + commission
    # SELL → external withdrawal of net proceeds
    return -(qty * price - commission)


# Buy $100 of stock: NAV +100, flow +100 → return 0
assert abs((200 - 100 - lot_only_flow("BUY", 1, 100)) / 100) < 1e-9

# Sell all $100: NAV 0→ skip; with flow, (0 - 100 - (-100)) / 100 = 0
assert abs((0 - 100 - lot_only_flow("SELL", 1, 100)) / 100) < 1e-9

# Partial sell $50 of $100 book → NAV 50, flow -50 → return 0
assert abs((50 - 100 - lot_only_flow("SELL", 1, 50)) / 100) < 1e-9

print("lot_only_sell_flow ok")

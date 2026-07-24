"""ponytail: symbol label coalesce prefers explicit ticker label."""

def coalesce_label(symbol_label, trade_max_label):
    return symbol_label if symbol_label else trade_max_label

assert coalesce_label("tech", "hedge") == "tech"
assert coalesce_label(None, "hedge") == "hedge"
assert coalesce_label(None, None) is None
print("symbol_label_coalesce ok")

"""ponytail: non-positive NAV must not be snapshotted."""
from decimal import Decimal

def should_skip(total_nav) -> bool:
    return total_nav is None or Decimal(str(total_nav)) <= 0

assert should_skip(0) and should_skip("0") and should_skip(None)
assert not should_skip(19662.32)
print("snapshot_nav_guard ok")

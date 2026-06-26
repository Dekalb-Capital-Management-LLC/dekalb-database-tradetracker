import asyncio
import json
import tempfile
from pathlib import Path

import requests
from ibauth.auth import IBAuth


def load_env():
    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    if not env_path.exists():
        env_path = root / "env"
    env = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def format_row(p):
    qty = p.get("position", 0)
    if qty == 0:
        return None
    return {
        "account": p.get("acctId"),
        "symbol": p.get("ticker") or p.get("contractDesc"),
        "quantity": qty,
        "market_price": p.get("mktPrice"),
        "market_value": p.get("mktValue"),
        "avg_cost": p.get("avgCost"),
        "unrealized_pnl": p.get("unrealizedPnl"),
        "asset_class": p.get("assetClass"),
    }


async def main():
    env = load_env()
    pk = env["IBKR_PRIVATE_KEY"].replace("\\n", "\n")
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
        f.write(pk)
        kp = f.name

    auth = IBAuth(
        env["IBKR_CLIENT_ID"],
        env["IBKR_CLIENT_KEY_ID"],
        env["IBKR_CREDENTIAL"],
        kp,
    )
    auth.IP = env.get("IBKR_SERVER_IP", "")
    await auth.get_access_token()
    await auth.get_bearer_token()
    await auth.validate_sso()
    await auth.ssodh_init()

    h = {**auth.header, "User-Agent": "test"}
    subs = requests.get("https://api.ibkr.com/v1/api/portfolio/subaccounts", headers=h, timeout=30)
    account_ids = [a["accountId"] for a in subs.json()]

    all_rows = []
    summaries = {}
    for aid in account_ids:
        summary = requests.get(f"https://api.ibkr.com/v1/api/portfolio/{aid}/summary", headers=h, timeout=30)
        if summary.ok:
            s = summary.json()
            nav = s.get("netliquidation", {}).get("amount")
            cash = s.get("totalcashvalue", {}).get("amount")
            summaries[aid] = {"nav": nav, "cash": cash, "title": aid}

        pos = requests.get(f"https://api.ibkr.com/v1/api/portfolio/{aid}/positions/0", headers=h, timeout=30)
        if pos.ok:
            for p in pos.json():
                row = format_row(p)
                if row:
                    all_rows.append(row)

    print(json.dumps({"accounts": summaries, "positions": all_rows}, indent=2))
    Path(kp).unlink(missing_ok=True)


asyncio.run(main())

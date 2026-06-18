import asyncio
import json
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from ibauth.auth import IBAuth

ACCT = "U16303670"


def load_env():
    env = {}
    for line in Path(".env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


async def connect():
    env = load_env()
    pk = env["IBKR_PRIVATE_KEY"].replace("\\n", "\n")
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
        f.write(pk)
        kp = f.name
    auth = IBAuth(
        env["IBKR_CLIENT_ID"], env["IBKR_CLIENT_KEY_ID"], env["IBKR_CREDENTIAL"], kp
    )
    auth.IP = env.get("IBKR_SERVER_IP", "")
    await auth.get_access_token()
    await auth.get_bearer_token()
    await auth.validate_sso()
    await auth.ssodh_init()
    return auth, kp


def parse_trade_time(raw):
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=timezone.utc).date()
    s = str(raw)
    if s.isdigit():
        return datetime.fromtimestamp(int(s), tz=timezone.utc).date()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return s[:10]


async def main():
    auth, kp = await connect()
    h = {**auth.header, "User-Agent": "t", "Content-Type": "application/json"}
    base = "https://api.ibkr.com/v1/api"

    # trades: max 7 days
    trades = requests.get(f"{base}/iserver/account/trades?days=7", headers=h, timeout=30)
    print("trades_7d", trades.status_code, trades.text[:500])

    pos = requests.get(f"{base}/portfolio2/{ACCT}/positions", headers=h, timeout=30).json()
    open_pos = [p for p in pos if float(p.get("position", 0)) != 0]

    first_buy = {}
    for p in open_pos:
        sym = p.get("description") or p.get("ticker")
        conid = int(p["conid"])
        for days in (90, 365, 730):
            r = requests.post(
                f"{base}/pa/transactions",
                headers=h,
                json={"acctIds": [ACCT], "currency": "USD", "conids": [conid], "days": days},
                timeout=60,
            )
            if r.ok:
                data = r.json()
                print(sym, "days", days, "keys", list(data.keys())[:8])
                # dump structure once
                if sym == (open_pos[0].get("description")):
                    print(json.dumps(data, indent=2)[:2500])
                break
            else:
                print(sym, "days", days, "err", r.status_code, r.text[:120])

    Path(kp).unlink(missing_ok=True)


asyncio.run(main())

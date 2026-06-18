import asyncio
import json
import tempfile
from pathlib import Path

import requests
from ibauth.auth import IBAuth


def load_env():
    env = {}
    for line in Path("env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


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
    acct = env["IBKR_ACCOUNT_ID"]
    # Also pull all FA subaccounts — holdings are often under client accounts
    subaccounts = requests.get(f"https://api.ibkr.com/v1/api/portfolio/subaccounts", headers=h, timeout=30)
    sub_ids = [a.get("accountId") or a.get("id") for a in subaccounts.json()] if subaccounts.ok else [acct]
    paths = ["/portfolio/accounts"] + [f"/portfolio/{aid}/summary" for aid in sub_ids] + [f"/portfolio/{aid}/positions/0" for aid in sub_ids]
    for path in paths:
        r = requests.get(f"https://api.ibkr.com/v1/api{path}", headers=h, timeout=30)
        print("===", path, r.status_code)
        try:
            print(json.dumps(r.json(), indent=2)[:4000])
        except Exception:
            print(r.text[:500])
        print()

    Path(kp).unlink(missing_ok=True)


asyncio.run(main())

"""Fetch live IBKR positions via OAuth 2.0 Web API (uses ibauth)."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import requests
from ibauth.auth import IBAuth

API_BASE = "https://api.ibkr.com/v1/api"


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


async def connect(auth: IBAuth) -> None:
    await auth.get_access_token()
    await auth.get_bearer_token()
    await auth.validate_sso()
    await auth.ssodh_init()


def fetch_positions(auth: IBAuth, account_id: str) -> list[dict]:
    headers = {**auth.header, "User-Agent": "dekalb-trade-tracker/1.0"}

    accounts = requests.get(f"{API_BASE}/portfolio/accounts", headers=headers, timeout=30)
    accounts.raise_for_status()

    resp = requests.get(
        f"{API_BASE}/portfolio/{account_id}/positions/0",
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def format_positions(positions: list[dict]) -> list[dict]:
    rows = []
    for p in positions:
        qty = p.get("position", 0)
        if qty == 0:
            continue
        rows.append({
            "symbol": p.get("ticker") or p.get("contractDesc", "UNKNOWN"),
            "quantity": qty,
            "market_price": p.get("mktPrice"),
            "market_value": p.get("mktValue"),
            "avg_cost": p.get("avgCost"),
            "unrealized_pnl": p.get("unrealizedPnl"),
            "realized_pnl": p.get("realizedPnl"),
            "asset_class": p.get("assetClass"),
            "currency": p.get("currency", "USD"),
        })
    return rows


async def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    env_path = repo_root / "env"
    if not env_path.exists():
        env_path = repo_root / ".env"
    if not env_path.exists():
        print("No env or .env file found", file=sys.stderr)
        return 1

    env = load_env_file(env_path)
    client_id = env.get("IBKR_CLIENT_ID", "")
    key_id = env.get("IBKR_CLIENT_KEY_ID", "main")
    credential = env.get("IBKR_CREDENTIAL", "")
    account_id = env.get("IBKR_ACCOUNT_ID", "")
    private_key = env.get("IBKR_PRIVATE_KEY", "").replace("\\n", "\n")
    server_ip = env.get("IBKR_SERVER_IP", "")

    if not all([client_id, credential, account_id, private_key]):
        print("Missing required IBKR env vars", file=sys.stderr)
        return 1

    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
        f.write(private_key)
        key_path = f.name

    try:
        auth = IBAuth(
            client_id=client_id,
            client_key_id=key_id,
            credential=credential,
            private_key_file=key_path,
        )
        if server_ip:
            auth.IP = server_ip

        print(f"Connecting to IBKR (account={account_id})...")
        await connect(auth)

        positions = fetch_positions(auth, account_id)
        formatted = format_positions(positions)
        print(json.dumps(formatted, indent=2))
        print(f"\nTotal open positions: {len(formatted)}")
        return 0
    finally:
        Path(key_path).unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)

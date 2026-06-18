"""Build open-positions table with first-buy (date opened) from IBKR pa/transactions."""
from __future__ import annotations

import asyncio
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from ibauth.auth import IBAuth

ACCT = "U16303670"
TX_DAYS = 730  # ~2y lookback for first buy date


def load_env():
    env = {}
    for line in Path(".env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def parse_raw_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y%m%d").date()


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


def first_buy_date(h, conid: int) -> date | None:
    r = requests.post(
        "https://api.ibkr.com/v1/api/pa/transactions",
        headers=h,
        json={"acctIds": [ACCT], "currency": "USD", "conids": [conid], "days": TX_DAYS},
        timeout=60,
    )
    if not r.ok:
        return None
    buys = [
        parse_raw_date(t["rawDate"])
        for t in r.json().get("transactions", [])
        if t.get("type") == "Buy" and t.get("rawDate")
    ]
    return min(buys) if buys else None


async def main():
    auth, kp = await connect()
    h = {**auth.header, "User-Agent": "dekalb-positions/1.0", "Content-Type": "application/json"}
    base = "https://api.ibkr.com/v1/api"

    positions = requests.get(f"{base}/portfolio2/{ACCT}/positions", headers=h, timeout=30).json()
    rows = []
    today = date.today()

    for p in sorted(positions, key=lambda x: x.get("description", "")):
        qty = float(p.get("position", 0))
        if qty == 0:
            continue
        sym = p["description"]
        conid = int(p["conid"])
        price = float(p["marketPrice"])
        mkt_val = float(p["marketValue"])
        avg_cost = float(p["avgCost"])
        upnl = float(p["unrealizedPnl"])
        cost_basis = mkt_val - upnl

        opened = first_buy_date(h, conid)
        time.sleep(0.3)  # gentle pacing

        hold_days = (today - opened).days if opened else None
        ret_pct = (upnl / cost_basis * 100) if cost_basis else 0
        ann_pct = (ret_pct * 365 / hold_days) if hold_days and hold_days > 0 else None

        rows.append({
            "symbol": sym,
            "shares": qty,
            "price": price,
            "market_value": mkt_val,
            "avg_cost": avg_cost,
            "unrealized_pnl": upnl,
            "return_pct": ret_pct,
            "date_opened": opened.isoformat() if opened else "—",
            "hold_days": hold_days if hold_days is not None else "—",
            "ann_return_pct": round(ann_pct, 1) if ann_pct is not None else "—",
        })

    # markdown table
    print(f"Account: {ACCT} | As of: {today.isoformat()}\n")
    hdr = "| Symbol | Shares | Price | Market Value | Avg Cost | Unrealized P&L | Return % | Date Opened | Hold Days | Ann. Return % |"
    sep = "|--------|--------|-------|--------------|----------|----------------|----------|-------------|-----------|---------------|"
    print(hdr)
    print(sep)
    for r in sorted(rows, key=lambda x: -x["market_value"]):
        pnl_s = f"+${r['unrealized_pnl']:,.2f}" if r["unrealized_pnl"] >= 0 else f"-${abs(r['unrealized_pnl']):,.2f}"
        print(
            f"| {r['symbol']} | {r['shares']:g} | ${r['price']:,.2f} | ${r['market_value']:,.2f} | "
            f"${r['avg_cost']:,.2f} | {pnl_s} | {r['return_pct']:+.2f}% | {r['date_opened']} | {r['hold_days']} | {r['ann_return_pct']} |"
        )

    tot_mv = sum(r["market_value"] for r in rows)
    tot_pnl = sum(r["unrealized_pnl"] for r in rows)
    tot_cost = tot_mv - tot_pnl
    print(f"\n**Totals:** Market value ${tot_mv:,.2f} | Unrealized P&L {'+' if tot_pnl>=0 else ''}${tot_pnl:,.2f} | Return on cost {tot_pnl/tot_cost*100:+.2f}%")

    Path(kp).unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())

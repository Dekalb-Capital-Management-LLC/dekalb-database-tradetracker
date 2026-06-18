"""

IBKR client — OAuth 2.0 (api.ibkr.com) or Client Portal Gateway (localhost).



OAuth: set IBKR_CLIENT_ID + IBKR_PRIVATE_KEY + IBKR_CREDENTIAL in .env.

Gateway: run Client Portal Gateway locally and set IBKR_GATEWAY_URL.

"""

from __future__ import annotations



import json

import logging

import tempfile

import time

from datetime import date, datetime

from typing import Any, Optional



import requests

import urllib3



urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)



import config



logger = logging.getLogger(__name__)



_session: Optional[requests.Session] = None

_last_ibkr_request_at: float = 0.0





def _throttle_ibkr() -> None:

    global _last_ibkr_request_at

    delay = config.IBKR_REQUEST_DELAY_SECONDS

    elapsed = time.time() - _last_ibkr_request_at

    if elapsed < delay:

        time.sleep(delay - elapsed)

    _last_ibkr_request_at = time.time()





def _get_session() -> requests.Session:

    global _session

    if _session is None:

        _session = requests.Session()

        _session.headers.update({"User-Agent": "dekalb-trade-tracker/1.0"})

        _session.verify = False

    return _session





def _normalize_position(p: dict) -> dict:

    """Unify portfolio/0 and portfolio2 field names."""

    desc = p.get("contractDesc") or p.get("description") or ""

    ticker = p.get("ticker") or (desc.split()[0] if desc else "")

    qty = p.get("position")

    if qty is None:

        qty = p.get("quantity", 0)

    return {

        "conid": p.get("conid"),

        "ticker": ticker,

        "contractDesc": desc or ticker,

        "description": desc,

        "position": qty,

        "mktPrice": p.get("mktPrice") if p.get("mktPrice") is not None else p.get("marketPrice"),

        "mktValue": p.get("mktValue") if p.get("mktValue") is not None else p.get("marketValue"),

        "avgCost": p.get("avgCost") if p.get("avgCost") is not None else p.get("averageCost"),

        "unrealizedPnl": (

            p.get("unrealizedPnl")

            if p.get("unrealizedPnl") is not None

            else p.get("unrealizedPnl")

        ),

        "realizedPnl": p.get("realizedPnl"),

        "currency": p.get("currency", "USD"),

    }





class IBKRClient:

    """Thin IBKR Web API wrapper. Returns None/[] when disabled."""



    def __init__(self) -> None:

        self.enabled = config.IBKR_ENABLED

        self.use_oauth = config.IBKR_USE_OAUTH

        self.base_url = (

            config.IBKR_API_BASE_URL.rstrip("/")

            if self.use_oauth

            else config.IBKR_GATEWAY_URL.rstrip("/")

        )

        self._oauth_headers: dict[str, str] = {}

        self._key_path: Optional[str] = None

        self._last_account_nav: Optional[float] = None



    @property

    def is_connected(self) -> bool:

        if not self.enabled:

            return False

        if self.use_oauth:

            return bool(self._oauth_headers)

        return self.auth_status() is not None



    @property

    def last_account_nav(self) -> Optional[float]:

        return self._last_account_nav



    async def connect_oauth(self) -> bool:

        """OAuth handshake via ibauth — call once on startup."""

        if not self.enabled or not self.use_oauth:

            return False

        from ibauth.auth import IBAuth



        pk = config.IBKR_PRIVATE_KEY.replace("\\n", "\n")

        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:

            f.write(pk)

            self._key_path = f.name



        auth = IBAuth(

            config.IBKR_CLIENT_ID,

            config.IBKR_CLIENT_KEY_ID,

            config.IBKR_CREDENTIAL,

            self._key_path,

        )

        auth.IP = config.IBKR_SERVER_IP

        await auth.get_access_token()

        await auth.get_bearer_token()

        await auth.validate_sso()

        await auth.ssodh_init()



        self._oauth_headers = {

            **auth.header,

            "User-Agent": "dekalb-trade-tracker/1.0",

            "Content-Type": "application/json",

        }

        logger.info("IBKR OAuth session ready (account %s)", config.IBKR_ACCOUNT_ID)

        self._validate_account_ids()

        self._bootstrap_iserver()

        return True



    def _validate_account_ids(self) -> None:

        accounts = self.get_accounts()

        ids: list[str] = []

        for a in accounts:

            aid = a.get("accountId") or a.get("id") or a.get("account")

            if aid:

                ids.append(str(aid))

        if ids:

            logger.info("IBKR portfolio accounts available: %s", ", ".join(ids))

        if config.IBKR_ACCOUNT_ID and ids and config.IBKR_ACCOUNT_ID not in ids:

            logger.warning(

                "IBKR_ACCOUNT_ID=%s not in portfolio accounts %s — "

                "check FA master vs client account (U*)",

                config.IBKR_ACCOUNT_ID,

                ids,

            )



    def _bootstrap_iserver(self) -> None:
        """Activate iserver layer for market data and recent fills."""
        try:
            auth = self.auth_status()
            logger.info("IBKR iserver auth status: %s", auth)

            accounts = self._get("/v1/api/iserver/accounts")
            if accounts:
                if isinstance(accounts, dict):
                    acct_list = accounts.get("accounts", [])
                elif isinstance(accounts, list):
                    acct_list = accounts
                else:
                    acct_list = []
                target = config.IBKR_ACCOUNT_ID
                for a in acct_list:
                    if isinstance(a, str):
                        aid = a
                    elif isinstance(a, dict):
                        aid = a.get("accountId") or a.get("id")
                    else:
                        continue
                    if aid and (not target or str(aid) == target):
                        self._post(f"/v1/api/iserver/account/{aid}/summary")
                        logger.info("IBKR iserver account summary requested for %s", aid)
                        break

            self.tickle()
        except Exception as exc:
            logger.warning("IBKR iserver bootstrap failed (non-fatal): %s", exc)

    def tickle(self) -> None:

        data = self._get("/v1/api/tickle")

        if data:

            logger.debug("IBKR tickle OK")



    def _get(self, path: str, *, _retry: bool = False, **kwargs) -> Optional[Any]:

        if not self.enabled:

            return None

        url = f"{self.base_url}{path}"

        try:

            _throttle_ibkr()

            if self.use_oauth:

                if not self._oauth_headers:

                    logger.error("IBKR OAuth not connected")

                    return None

                resp = requests.get(

                    url, headers=self._oauth_headers, timeout=30, **kwargs

                )

            else:

                resp = _get_session().get(url, timeout=10, **kwargs)

            resp.raise_for_status()

            return resp.json()

        except requests.exceptions.ConnectionError:

            logger.error("Cannot reach IBKR at %s", self.base_url)

            return None

        except requests.exceptions.Timeout:

            logger.error("IBKR request timed out [%s]", path)

            return None

        except requests.exceptions.HTTPError as exc:

            if (

                not _retry

                and exc.response is not None

                and exc.response.status_code == 429

            ):

                time.sleep(config.IBKR_REQUEST_DELAY_SECONDS * 5)

                return self._get(path, _retry=True, **kwargs)

            logger.error("IBKR HTTP error [%s]: %s", path, exc)

            return None

        except Exception as exc:

            logger.error("IBKR request failed [%s]: %s", path, exc)

            return None



    def _post(self, path: str, json: Optional[dict] = None) -> Optional[Any]:

        if not self.enabled:

            return None

        url = f"{self.base_url}{path}"

        try:

            _throttle_ibkr()

            if self.use_oauth:

                if not self._oauth_headers:

                    return None

                resp = requests.post(

                    url, headers=self._oauth_headers, json=json or {}, timeout=30

                )

            else:

                resp = _get_session().post(url, json=json or {}, timeout=10)

            resp.raise_for_status()

            return resp.json()

        except Exception as exc:

            logger.error("IBKR POST failed [%s]: %s", path, exc)

            return None



    def auth_status(self) -> Optional[dict]:

        return self._get("/v1/api/iserver/auth/status")



    def reauthenticate(self) -> Optional[dict]:

        return self._post("/v1/api/iserver/reauthenticate")



    def get_accounts(self) -> list[dict]:

        data = self._get("/v1/api/portfolio/accounts")

        if data is None:

            return []

        return data if isinstance(data, list) else [data]



    def get_account_summary(self, account_id: str) -> Optional[dict]:

        self.get_accounts()

        summary = self._get(f"/v1/api/portfolio/{account_id}/summary")

        if summary:

            entry = summary.get("netliquidation", {})

            if isinstance(entry, dict) and entry.get("amount") is not None:

                self._last_account_nav = float(entry["amount"])

        return summary



    def _fetch_positions_primary(self, account_id: str) -> Optional[Any]:

        return self._get(f"/v1/api/portfolio/{account_id}/positions/0")



    def _fetch_positions_portfolio2(self, account_id: str) -> Optional[Any]:

        return self._get(f"/v1/api/portfolio2/{account_id}/positions")



    def get_positions(self, account_id: str) -> list[dict]:

        self.get_accounts()

        retries = config.IBKR_POSITIONS_RETRY_COUNT

        delay = config.IBKR_POSITIONS_RETRY_DELAY

        data: Any = None



        for attempt in range(retries):

            data = self._fetch_positions_primary(account_id)

            if data and isinstance(data, list) and len(data) > 0:

                break

            if config.DEBUG and data is not None:

                logger.debug(

                    "IBKR positions attempt %d empty: %s",

                    attempt + 1,

                    json.dumps(data)[:500],

                )

            if attempt < retries - 1:

                time.sleep(delay)



        if not data or (isinstance(data, list) and len(data) == 0):

            logger.warning(

                "IBKR primary positions empty after %d retries, trying portfolio2",

                retries,

            )

            data = self._fetch_positions_portfolio2(account_id)



        if data is None:

            return []

        if not isinstance(data, list):

            logger.warning("Unexpected positions response type: %s", type(data))

            return []



        if len(data) == 0:

            logger.warning("IBKR returned no positions for account %s", account_id)

        return [_normalize_position(p) for p in data]



    def get_conid(self, symbol: str) -> Optional[int]:

        data = self._get("/v1/api/trsrv/stocks", params={"symbols": symbol})

        if not data:

            return None

        try:

            contracts = data.get(symbol.upper(), [])

            if contracts:

                return contracts[0]["contracts"][0]["conid"]

        except (KeyError, IndexError, TypeError):

            logger.warning("Unexpected conid response for %s: %s", symbol, data)

        return None



    def get_market_history_bars(

        self,

        conid: int,

        start: date,

        end: date,

        *,

        bar: str = "1d",

    ) -> list[dict]:

        """Daily OHLCV from /iserver/marketdata/history."""

        days = min(max((end - start).days + 10, 5), 1000)

        data = self._get(

            "/v1/api/iserver/marketdata/history",

            params={

                "conid": str(conid),

                "period": f"{days}d",

                "bar": bar,

                "outsideRth": "false",

            },

        )

        if not data or not isinstance(data, dict):

            return []

        rows = data.get("data") or []

        out: list[dict] = []

        for row in rows:

            ts = row.get("t")

            close = row.get("c")

            if ts is None or close is None:

                continue

            d = datetime.utcfromtimestamp(ts / 1000).date()

            if d < start or d > end:

                continue

            out.append({

                "date": d,

                "open": row.get("o"),

                "high": row.get("h"),

                "low": row.get("l"),

                "close": close,

                "volume": int(row.get("v") or 0),

            })

        out.sort(key=lambda r: r["date"])

        logger.debug("IBKR history conid=%s bars=%d (%s..%s)", conid, len(out), start, end)

        return out



    def get_market_snapshot(

        self,

        conid: int,

        *,

        max_attempts: Optional[int] = None,

        delay: Optional[float] = None,

    ) -> Optional[dict]:

        prices = self.get_market_snapshot_batch(

            [conid], max_attempts=max_attempts, delay=delay

        )

        if conid in prices:

            return {"conid": str(conid), "31": str(prices[conid])}

        return None



    def get_market_snapshot_batch(

        self,

        conids: list[int],

        *,

        max_attempts: Optional[int] = None,

        delay: Optional[float] = None,

    ) -> dict[int, float]:

        """Poll snapshot until field 31 (last price) is populated."""

        if not conids:

            return {}



        max_attempts = max_attempts or config.IBKR_SNAPSHOT_MAX_ATTEMPTS

        delay = delay if delay is not None else config.IBKR_SNAPSHOT_POLL_DELAY

        params = {

            "conids": ",".join(str(c) for c in conids),

            "fields": "31,84,86",

        }

        result: dict[int, float] = {}



        for attempt in range(max_attempts):

            data = self._get("/v1/api/iserver/marketdata/snapshot", params=params)

            if data and isinstance(data, list):

                for row in data:

                    cid = row.get("conid")

                    raw = row.get("31")

                    if cid is not None and raw not in (None, ""):

                        try:

                            result[int(cid)] = float(raw)

                        except (TypeError, ValueError):

                            pass

            if len(result) >= len(conids):

                break

            if attempt < max_attempts - 1:

                time.sleep(delay)



        return result



    def get_price_from_positions(self, symbol: str, account_id: Optional[str] = None) -> Optional[float]:

        """Live price from portfolio holdings when iserver snapshot fails."""

        acct = account_id or config.IBKR_ACCOUNT_ID

        if not acct:

            return None

        sym = symbol.upper()

        for p in self.live_positions(acct):

            pos_sym = str(p.get("symbol", "")).upper().split()[0]

            if pos_sym == sym:

                price = p.get("market_price")

                if price is not None and float(price) > 0:

                    return float(price)

        return None



    def get_pa_transactions(

        self, account_id: str, conids: list[int], days: int | None = None

    ) -> list[dict]:

        """Portfolio Analyst trade history (up to ~2y per conid batch)."""

        if not conids:

            return []

        days = days or config.IBKR_TX_DAYS

        data = self._post(

            "/v1/api/pa/transactions",

            json={

                "acctIds": [account_id],

                "currency": "USD",

                "conids": conids,

                "days": days,

            },

        )

        if not data:

            return []

        return data.get("transactions", []) if isinstance(data, dict) else []



    def get_all_pa_transactions(

        self, account_id: str, conids: list[int], days: int | None = None

    ) -> list[dict]:

        """Fetch PA transactions one conid at a time (batched calls truncate history)."""

        out: list[dict] = []

        for conid in conids:

            out.extend(self.get_pa_transactions(account_id, [conid], days))

        return out



    def position_conids(self, account_id: str) -> list[int]:

        return [

            int(p["conid"])

            for p in self.get_positions(account_id)

            if p.get("conid") is not None

        ]



    def position_symbol_map(self, account_id: str) -> dict[int, str]:

        out: dict[int, str] = {}

        for p in self.get_positions(account_id):

            conid = p.get("conid")

            if conid is None:

                continue

            sym = p.get("ticker") or p.get("contractDesc") or p.get("description") or ""

            if sym:

                out[int(conid)] = str(sym).upper().split()[0]

        return out



    def get_recent_trades(self, account_id: str) -> list[dict]:

        self.get_accounts()

        data = self._get("/v1/api/iserver/account/trades")

        if data is None:

            return []

        return data if isinstance(data, list) else []



    def live_positions(self, account_id: str) -> list[dict]:

        """Normalized open positions with IBKR live prices."""

        out = []

        for p in self.get_positions(account_id):

            qty = float(p.get("position", 0))

            if qty == 0:

                continue

            mkt_val = float(p.get("mktValue") or 0)

            upnl = float(p.get("unrealizedPnl") or 0)

            mkt_price = float(p.get("mktPrice") or 0)

            out.append({

                "symbol": p.get("ticker") or p.get("contractDesc") or p.get("description", "UNKNOWN"),

                "quantity": qty,

                "avg_cost": float(p.get("avgCost") or 0),

                "market_price": mkt_price,

                "market_value": mkt_val,

                "unrealized_pnl": upnl,

                "cost_basis": mkt_val - upnl,

            })

        return out





ibkr_client = IBKRClient()



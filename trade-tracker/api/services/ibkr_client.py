"""
IBKR Web API client — RSA-based OAuth 2.0 (JWT Bearer Token flow, RFC 7523).

This is NOT standard browser OAuth. There is no redirect URL or login page.
Authentication is entirely server-side using RSA keys. The full flow:

  1. Build a JWT signed with your RSA private key
  2. POST to token endpoint → get OAuth2 bearer token
  3. POST to sso-sessions with bearer token + IBKR username + server IP → session cookie
  4. POST to iserver/auth/ssodh/init → activate trading/data session
  5. Sleep 3-5s, then GET iserver/accounts (required warm-up call)
  6. Make API calls using the session cookie
  7. POST /tickle every 60s to keep session alive

This module handles all of that automatically. Call ibkr_client.connect() once
at startup (done by main.py). After that, just call the data methods.
Re-authentication is automatic when the session expires.

Required env vars (set in .env):
  IBKR_CLIENT_ID      - e.g. DekalbCapital-Paper
  IBKR_CLIENT_KEY_ID  - e.g. main
  IBKR_CREDENTIAL     - IBKR username (e.g. dekalbcapitalpaper)
  IBKR_PRIVATE_KEY    - full RSA private key PEM content
  IBKR_ACCOUNT_ID     - account ID (e.g. DFP321877)
  IBKR_SERVER_IP      - outbound IP of this server
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import jwt
import requests

import config

logger = logging.getLogger(__name__)


def _is_configured() -> bool:
    return bool(
        config.IBKR_ENABLED
        and config.IBKR_CLIENT_ID
        and config.IBKR_CLIENT_KEY_ID
        and config.IBKR_CREDENTIAL
        and config.IBKR_PRIVATE_KEY
        and config.IBKR_ACCOUNT_ID
    )


def _load_private_key():
    """Load RSA private key from config."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    key_bytes = config.IBKR_PRIVATE_KEY.encode()
    return load_pem_private_key(key_bytes, password=None)


def _build_jwt() -> str:
    """
    Build a signed JWT assertion for the OAuth2 token request.
    Header: alg=RS256, kid=<clientKeyId>
    Claims: iss/sub=clientId, aud=token URL, standard timing fields
    """
    now = int(time.time())
    payload = {
        "iss": config.IBKR_CLIENT_ID,
        "sub": config.IBKR_CLIENT_ID,
        "aud": config.IBKR_TOKEN_URL,
        "iat": now,
        "exp": now + 300,
        "jti": str(uuid.uuid4()),
    }
    private_key = _load_private_key()
    return jwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers={"kid": config.IBKR_CLIENT_KEY_ID},
    )


class IBKRClient:
    """
    Manages the IBKR Web API session lifecycle.
    Once connected, all data methods use the active session automatically.
    """

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "dekalb-trade-tracker/1.0"})
        self._connected = False
        self._tickle_thread: Optional[threading.Thread] = None
        self._stop_tickle = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """
        Establish a full IBKR session. Safe to call on startup or after expiry.
        Returns True if successful, False otherwise.
        """
        if not _is_configured():
            logger.info("IBKR not configured — skipping connection attempt")
            return False

        with self._lock:
            try:
                return self._do_connect()
            except Exception as exc:
                logger.error("IBKR connection failed: %s", exc)
                self._connected = False
                return False

    def _do_connect(self) -> bool:
        logger.info("IBKR: starting connection (client_id=%s)", config.IBKR_CLIENT_ID)

        # Step 1: Get OAuth2 bearer token using RSA-signed JWT
        bearer_token = self._get_bearer_token()
        if not bearer_token:
            return False

        # Step 2: Set bearer token on session for all subsequent requests
        self._session.headers.update({"Authorization": f"Bearer {bearer_token}"})
        logger.info("IBKR: bearer token set on session")

        # Step 3: Create SSO session (establishes portal session for the account)
        sso_token = self._create_sso_session()
        if not sso_token:
            return False

        # Step 4: Swap to SSO session token for iserver calls
        self._session.headers.update({"Authorization": f"Bearer {sso_token}"})

        # Step 5: Init iserver trading/data session (opens brokerage session)
        self._init_iserver()

        # Step 6: Wait for session to activate (IBKR requirement)
        time.sleep(4)

        # Step 7: Warm-up call — required before portfolio/iserver endpoints work
        self._warmup()

        self._connected = True
        logger.info("IBKR: connected (account=%s)", config.IBKR_ACCOUNT_ID)

        # Start background tickle to keep session alive
        self._start_tickle()
        return True

    def _get_bearer_token(self) -> Optional[str]:
        """Step 1: exchange RSA-signed JWT for OAuth2 bearer token."""
        try:
            assertion = _build_jwt()
            resp = self._session.post(
                config.IBKR_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                    "client_assertion": assertion,
                    "scope": "sso-sessions.write",
                },
                timeout=15,
            )
            resp.raise_for_status()
            token = resp.json().get("access_token")
            if token:
                logger.info("IBKR: OAuth2 bearer token obtained")
            else:
                logger.error("IBKR: token response missing access_token: %s", resp.text[:200])
            return token
        except requests.exceptions.HTTPError as exc:
            body = ""
            try:
                body = exc.response.text[:500]
            except Exception:
                pass
            logger.error("IBKR: failed to get bearer token: %s | body: %s", exc, body)
            return None
        except Exception as exc:
            logger.error("IBKR: failed to get bearer token: %s", exc)
            return None

    def _create_sso_session(self) -> Optional[str]:
        """
        Step 3: create SSO session.
        Endpoint: POST /gw/api/v1/sso-sessions
        Body: signed JWT (Content-Type: application/jwt) containing credential + ip.
        Returns the SSO access_token on success, None on failure.
        """
        try:
            ip = config.IBKR_SERVER_IP or self._detect_ip()
            now = int(time.time())
            payload = {
                "iss": config.IBKR_CLIENT_ID,
                "sub": config.IBKR_CLIENT_ID,
                "iat": now,
                "exp": now + 300,
                "jti": str(uuid.uuid4()),
                "credential": config.IBKR_CREDENTIAL,
                "ip": ip,
            }
            private_key = _load_private_key()
            sso_jwt = jwt.encode(
                payload,
                private_key,
                algorithm="RS256",
                headers={"kid": config.IBKR_CLIENT_KEY_ID},
            )
            resp = self._session.post(
                config.IBKR_SSO_URL,
                data=sso_jwt,
                headers={"Content-Type": "application/jwt"},
                timeout=15,
            )
            resp.raise_for_status()
            token = resp.json().get("access_token")
            logger.info("IBKR: SSO session created (credential=%s)", config.IBKR_CREDENTIAL)
            return token
        except Exception as exc:
            logger.error("IBKR: SSO session creation failed: %s", exc)
            return None

    def _init_iserver(self) -> None:
        """Step 3: activate trading/data session."""
        try:
            resp = self._session.post(
                f"{config.IBKR_BASE_URL}/iserver/auth/ssodh/init",
                json={"publish": True, "compete": True},
                timeout=15,
            )
            logger.debug("IBKR: iserver init response: %s", resp.status_code)
        except Exception as exc:
            logger.warning("IBKR: iserver init warning (may still work): %s", exc)

    def _warmup(self) -> None:
        """Step 5: GET /iserver/accounts — required before other iserver calls."""
        try:
            self._session.get(f"{config.IBKR_BASE_URL}/iserver/accounts", timeout=10)
        except Exception:
            pass

    def _detect_ip(self) -> str:
        """Detect this server's outbound IP as a fallback when IBKR_SERVER_IP is not set."""
        try:
            resp = requests.get("https://api.ipify.org", timeout=5)
            ip = resp.text.strip()
            logger.warning("IBKR_SERVER_IP not set — auto-detected %s. Set it explicitly for stability.", ip)
            return ip
        except Exception:
            logger.warning("Could not detect outbound IP — using 0.0.0.0 (likely to fail)")
            return "0.0.0.0"

    def disconnect(self) -> None:
        """Close the session and stop the tickle thread."""
        self._stop_tickle.set()
        try:
            self._session.post(f"{config.IBKR_BASE_URL}/logout", timeout=10)
        except Exception:
            pass
        self._connected = False
        logger.info("IBKR: disconnected")

    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Tickle — keeps the session alive
    # ------------------------------------------------------------------

    def _start_tickle(self) -> None:
        self._stop_tickle.clear()
        self._tickle_thread = threading.Thread(target=self._tickle_loop, daemon=True)
        self._tickle_thread.start()

    def _tickle_loop(self) -> None:
        """POST /tickle every 60s to prevent session timeout."""
        while not self._stop_tickle.wait(60):
            try:
                resp = self._session.post(f"{config.IBKR_BASE_URL}/tickle", timeout=10)
                if resp.status_code == 401:
                    logger.warning("IBKR: tickle 401 — session expired, reconnecting...")
                    self._connected = False
                    self._do_connect()
            except Exception as exc:
                logger.warning("IBKR: tickle error: %s", exc)

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------

    def _get(self, path: str, **kwargs) -> Optional[Any]:
        if not self._connected:
            logger.warning("IBKR not connected — call connect() first")
            return None
        url = f"{config.IBKR_BASE_URL}{path}"
        try:
            resp = self._session.get(url, timeout=10, **kwargs)
            if resp.status_code == 401:
                logger.warning("IBKR: 401 on %s — reconnecting...", path)
                self._connected = False
                if self.connect():
                    resp = self._session.get(url, timeout=10, **kwargs)
                else:
                    return None
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ConnectionError:
            logger.error("IBKR: cannot reach API at %s", url)
            return None
        except requests.exceptions.Timeout:
            logger.error("IBKR: request timed out [%s]", path)
            return None
        except Exception as exc:
            logger.error("IBKR: request failed [%s]: %s", path, exc)
            return None

    # ------------------------------------------------------------------
    # Data methods
    # ------------------------------------------------------------------

    def get_account_summary(self, account_id: str) -> Optional[dict]:
        # Must call /portfolio/accounts before other /portfolio endpoints
        self._get("/portfolio/accounts")
        return self._get(f"/portfolio/{account_id}/summary")

    def get_ledger(self, account_id: str) -> Optional[dict]:
        """
        Returns per-currency balances including cashbalance, netliquidationvalue,
        unrealizedpnl, realizedpnl. Use 'BASE' key for base-currency totals.
        """
        self._get("/portfolio/accounts")
        return self._get(f"/portfolio/{account_id}/ledger")

    def get_historical_bars(
        self,
        conid: int,
        period: str = "2y",
        bar: str = "1d",
    ) -> list[dict]:
        """
        Fetch daily OHLCV history for an instrument.
        period: '2y' | '1y' | '6m' | '3m' | '1m'
        bar:    '1d' | '1w' | '1m'
        Returns list of {'date': date, 'close': Decimal}
        """
        from datetime import timezone
        data = self._get(
            "/iserver/marketdata/history",
            params={"conid": conid, "period": period, "bar": bar, "outsideRth": True},
        )
        if not data or "data" not in data:
            return []
        bars = []
        for b in data["data"]:
            ts_ms = b.get("t")
            close = b.get("c")
            if ts_ms is None or close is None:
                continue
            try:
                d = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).date()
                bars.append({"date": d, "close": Decimal(str(round(float(close), 4)))})
            except Exception:
                continue
        return bars

    def get_positions(self, account_id: str) -> list[dict]:
        self._get("/portfolio/accounts")
        data = self._get(f"/portfolio/{account_id}/positions/0")
        if data is None:
            return []
        return data if isinstance(data, list) else []

    def get_recent_trades(self, account_id: str) -> list[dict]:
        # days=7 fetches up to 7 days of fills (IBKR max for this endpoint)
        data = self._get("/iserver/account/trades", params={"days": 7})
        if data is None:
            return []
        return data if isinstance(data, list) else []

    def get_conid(self, symbol: str) -> Optional[int]:
        data = self._get("/trsrv/stocks", params={"symbols": symbol})
        if not data:
            return None
        try:
            contracts = data.get(symbol.upper(), [])
            if contracts:
                return contracts[0]["contracts"][0]["conid"]
        except (KeyError, IndexError, TypeError):
            pass
        return None

    def get_market_snapshot(self, conid: int) -> Optional[dict]:
        # Fields: 31=last, 84=bid, 86=ask, 82=change$, 83=change%, 7296=prev_close
        params = {"conids": conid, "fields": "31,84,86,82,83,7296"}
        data = self._get("/iserver/marketdata/snapshot", params=params)
        if not data or not isinstance(data, list):
            return None
        # First call subscribes to the feed — wait 1s then fetch actual data
        if not data[0].get("31"):
            time.sleep(1)
            data = self._get("/iserver/marketdata/snapshot", params=params)
            if not data or not isinstance(data, list):
                return None
        return data[0]


# Singleton — imported by routers and services
ibkr_client = IBKRClient()

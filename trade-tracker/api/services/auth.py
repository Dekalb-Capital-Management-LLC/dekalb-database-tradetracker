import base64, logging, time
from typing import Any
import jwt, requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
import config

logger = logging.getLogger(__name__)
_GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
_JWKS_CACHE_TTL = 3600
_jwks_cache: dict[str, Any] = {}
_jwks_fetched_at: float = 0.0


class AuthError(Exception):
    pass


def _b64_to_int(b64: str) -> int:
    padded = b64 + "=" * (-len(b64) % 4)
    return int.from_bytes(base64.urlsafe_b64decode(padded), "big")


def _jwk_to_public_key(jwk: dict) -> Any:
    return RSAPublicNumbers(_b64_to_int(jwk["e"]), _b64_to_int(jwk["n"])).public_key(default_backend())


def _refresh_jwks() -> None:
    global _jwks_cache, _jwks_fetched_at
    try:
        resp = requests.get(_GOOGLE_JWKS_URL, timeout=5)
        resp.raise_for_status()
        keys = resp.json().get("keys", [])
        _jwks_cache = {k["kid"]: _jwk_to_public_key(k) for k in keys if k.get("kty") == "RSA"}
        _jwks_fetched_at = time.monotonic()
    except Exception as exc:
        logger.error("Failed to refresh Google JWKS: %s", exc)
        if not _jwks_cache:
            raise AuthError("Unable to fetch Google public keys") from exc


def _get_public_key(kid: str) -> Any:
    if time.monotonic() - _jwks_fetched_at > _JWKS_CACHE_TTL or not _jwks_cache:
        _refresh_jwks()
    key = _jwks_cache.get(kid)
    if key is None:
        _refresh_jwks()
        key = _jwks_cache.get(kid)
    if key is None:
        raise AuthError(f"Unknown key id: {kid}")
    return key


def verify_google_id_token(token: str) -> dict:
    if not config.GOOGLE_CLIENT_ID:
        raise AuthError("GOOGLE_CLIENT_ID is not configured")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.DecodeError as exc:
        raise AuthError("Malformed token header") from exc
    kid = header.get("kid")
    if not kid:
        raise AuthError("Token header missing 'kid'")
    if header.get("alg") != "RS256":
        raise AuthError(f"Unexpected algorithm: {header.get('alg')}")
    public_key = _get_public_key(kid)
    try:
        claims = jwt.decode(token, public_key, algorithms=["RS256"], audience=config.GOOGLE_CLIENT_ID)
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token has expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise AuthError("Token audience mismatch") from exc
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc
    if claims.get("iss") not in _VALID_ISSUERS:
        raise AuthError(f"Invalid issuer: {claims.get('iss')}")
    if not claims.get("email_verified"):
        raise AuthError("Email is not verified")
    email: str = claims.get("email", "")
    hd: str = claims.get("hd", "")
    domain = config.ALLOWED_EMAIL_DOMAIN
    if hd != domain and not email.endswith(f"@{domain}"):
        raise AuthError(f"Email domain not allowed: {email}")
    return claims

"""Optional OIDC authorization-code + PKCE flow for existing workspace users."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

from .config import Settings


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _state_cookie(settings: Settings) -> tuple[str, dict[str, str]]:
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    payload = {
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "exp": str(int(time.time()) + 600),
    }
    encoded = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(
        settings.session_pepper.get_secret_value().encode(), encoded.encode(), hashlib.sha256
    ).hexdigest()
    return f"{encoded}.{signature}", payload


def read_state_cookie(settings: Settings, value: str, returned_state: str) -> dict[str, str] | None:
    encoded, separator, signature = value.partition(".")
    if not separator:
        return None
    expected = hmac.new(
        settings.session_pepper.get_secret_value().encode(), encoded.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or not hmac.compare_digest(str(payload.get("state", "")), returned_state)
        or int(payload.get("exp", 0)) <= int(time.time())
    ):
        return None
    return payload


async def discovery(settings: Settings) -> dict:
    issuer = settings.oidc_issuer_url.rstrip("/")
    parsed = urlsplit(issuer)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("oidc_issuer_must_be_https")
    async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as client:
        response = await client.get(f"{issuer}/.well-known/openid-configuration")
        response.raise_for_status()
        config = response.json()
    if config.get("issuer", "").rstrip("/") != issuer:
        raise ValueError("oidc_issuer_mismatch")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        endpoint = urlsplit(config.get(key, ""))
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
        ):
            raise ValueError("oidc_invalid_discovery_endpoint")
    return config


async def authorization_url(settings: Settings) -> tuple[str, str]:
    config = await discovery(settings)
    cookie, payload = _state_cookie(settings)
    challenge = _b64(hashlib.sha256(payload["verifier"].encode()).digest())
    query = urlencode(
        {
            "client_id": settings.oidc_client_id,
            "response_type": "code",
            "redirect_uri": settings.oidc_redirect_url,
            "scope": "openid email profile",
            "state": payload["state"],
            "nonce": payload["nonce"],
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    separator = "&" if "?" in config["authorization_endpoint"] else "?"
    return f"{config['authorization_endpoint']}{separator}{query}", cookie


async def verified_email(settings: Settings, code: str, payload: dict[str, str]) -> str | None:
    config = await discovery(settings)
    token_form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oidc_redirect_url,
        "client_id": settings.oidc_client_id,
        "code_verifier": payload["verifier"],
    }
    if settings.oidc_client_secret:
        token_form["client_secret"] = settings.oidc_client_secret.get_secret_value()
    async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False) as client:
        response = await client.post(config["token_endpoint"], data=token_form)
        response.raise_for_status()
        id_token = response.json().get("id_token")
        if not isinstance(id_token, str):
            return None
        header = jwt.get_unverified_header(id_token)
        algorithm = header.get("alg")
        if algorithm not in {"RS256", "ES256"}:
            return None
        jwks_response = await client.get(config["jwks_uri"])
        jwks_response.raise_for_status()
        jwks = jwks_response.json().get("keys", [])
    key = next((item for item in jwks if item.get("kid") == header.get("kid")), None)
    if key is None:
        return None
    try:
        signing_key = jwt.PyJWK.from_dict(key, algorithm=algorithm).key
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=[algorithm],
            audience=settings.oidc_client_id,
            issuer=settings.oidc_issuer_url.rstrip("/"),
            options={"require": ["exp", "iat", "iss", "sub", "aud"]},
        )
    except jwt.InvalidTokenError:
        return None
    audiences = claims.get("aud")
    authorized_party = claims.get("azp")
    if authorized_party is not None and authorized_party != settings.oidc_client_id:
        return None
    if (
        isinstance(audiences, list)
        and len(audiences) > 1
        and authorized_party != settings.oidc_client_id
    ):
        return None
    if (
        not claims.get("sub")
        or not isinstance(claims.get("nonce"), str)
        or not hmac.compare_digest(claims["nonce"], payload.get("nonce", ""))
        or claims.get("email_verified") is not True
    ):
        return None
    email = claims.get("email")
    return email.strip().lower() if isinstance(email, str) else None

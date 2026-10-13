"""Bounded WooCommerce REST adapter; never retries or follows redirects."""

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import quote

import httpx

from .config import Settings


class StoreError(Exception):
    """Sanitized failure safe to return to an MCP caller."""


def oauth_params(method: str, url: str, params: dict, key: str, secret: str) -> dict:
    """WooCommerce's one-legged OAuth 1.0a HMAC-SHA256 for internal HTTP.

    OAuth signs the method, URL and query parameters, not JSON request bodies.
    HTTPS remains required for confidentiality on untrusted networks.
    """
    values = {
        **{k: str(v) for k, v in params.items()},
        "oauth_consumer_key": key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_timestamp": str(int(time.time())),
        "oauth_signature_method": "HMAC-SHA256",
    }
    encode = lambda value: quote(str(value), safe="~")
    normalized = "&".join(f"{encode(k)}={encode(v)}" for k, v in sorted(values.items()))
    base = "&".join(encode(v) for v in (method.upper(), url, normalized))
    signature = hmac.new((secret + "&").encode(), base.encode(), hashlib.sha256).digest()
    return {**values, "oauth_signature": base64.b64encode(signature).decode()}


class StoreClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout_seconds, connect=5),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def request(
        self, method: str, resource: str, *, params: dict | None = None, body: dict | None = None
    ):
        url = f"{self.settings.store_url}/wp-json/wc/v3/{resource}"
        query = params or {}
        key = self.settings.consumer_key.get_secret_value()
        secret = self.settings.consumer_secret.get_secret_value()
        auth = None
        if url.startswith("https://"):
            auth = httpx.BasicAuth(key, secret)
        else:
            signing_url = (
                f"{self.settings.signing_url or self.settings.store_url}/wp-json/wc/v3/{resource}"
            )
            query = oauth_params(method, signing_url, query, key, secret)
        try:
            async with (
                asyncio.timeout(self.settings.timeout_seconds),
                self.http.stream(method, url, params=query, json=body, auth=auth) as response,
            ):
                if response.status_code in (401, 403):
                    raise StoreError(
                        "store_authentication_failed: check the store service credential"
                    )
                if response.status_code == 404:
                    raise StoreError("not_found: the requested store resource does not exist")
                if not 200 <= response.status_code < 300:
                    raise StoreError(
                        f"store_http_error: HTTP {response.status_code}; no automatic retry"
                    )
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > self.settings.max_response_bytes:
                        raise StoreError("store_response_too_large: narrow the query")
                try:
                    data = json.loads(chunks)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise StoreError("invalid_store_response: expected JSON") from exc
                return data, response.headers
        except (httpx.TimeoutException, TimeoutError) as exc:
            outcome = (
                "unknown_outcome: write may have committed; reconcile with a read"
                if method != "GET"
                else "store_timeout: read did not complete"
            )
            raise StoreError(outcome) from exc
        except httpx.RequestError as exc:
            outcome = (
                "unknown_outcome: write result is uncertain; reconcile with a read"
                if method != "GET"
                else "store_unreachable: read could not reach the store"
            )
            raise StoreError(outcome) from exc

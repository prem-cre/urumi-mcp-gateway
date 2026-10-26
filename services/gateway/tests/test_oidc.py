from __future__ import annotations

import time

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import Request, Response
from pydantic import SecretStr

from services.gateway import oidc
from services.gateway.config import Settings


def make_settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql+asyncpg://gateway:unused@localhost/gateway",
        "api_key_pepper": SecretStr("api-pepper"),
        "session_pepper": SecretStr("session-pepper"),
        "upstream_encryption_key": SecretStr(Fernet.generate_key().decode()),
        "bootstrap_admin_email": "admin@example.test",
        "bootstrap_admin_password": SecretStr("not-a-real-password"),
        "bootstrap_demo_token": SecretStr("demo-token"),
        "woo_mcp_token": SecretStr("woo-token"),
        "oidc_issuer_url": "https://id.example.test",
        "oidc_client_id": "urumi-dashboard",
        "oidc_redirect_url": "https://gateway.example.test/api/auth/oidc/callback",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_oidc_verified_email_checks_jwt_signature_issuer_audience_nonce_and_email(
    monkeypatch,
):
    settings = make_settings()
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    state = {"nonce": "expected-nonce", "verifier": "pkce-verifier"}

    async def fake_discovery(_settings):
        return {
            "issuer": settings.oidc_issuer_url,
            "authorization_endpoint": "https://id.example.test/authorize",
            "token_endpoint": "https://id.example.test/token",
            "jwks_uri": "https://id.example.test/keys",
        }

    monkeypatch.setattr(oidc, "discovery", fake_discovery)

    async def verify_claims(claims):
        token = jwt.encode(
            claims,
            private_key,
            algorithm="RS256",
            headers={"kid": "key-1"},
        )
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
        jwk["kid"] = "key-1"

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, _url, data):
                assert data["code_verifier"] == state["verifier"]
                return http_response({"id_token": token})

            async def get(self, _url):
                return http_response({"keys": [jwk]})

        monkeypatch.setattr(oidc.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
        return await oidc.verified_email(settings, "authorization-code", state)

    base_claims = {
        "iss": settings.oidc_issuer_url,
        "sub": "user-123",
        "aud": settings.oidc_client_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "nonce": state["nonce"],
        "email": "  Member@Example.Test ",
        "email_verified": True,
    }
    assert await verify_claims(base_claims) == "member@example.test"

    assert await verify_claims({**base_claims, "nonce": "wrong"}) is None
    assert await verify_claims({**base_claims, "email_verified": False}) is None
    assert await verify_claims({**base_claims, "iss": "https://attacker.example.test"}) is None


def http_response(body: dict):

    return Response(200, json=body, request=Request("GET", "https://id.example.test"))


def test_oidc_state_cookie_is_bound_to_returned_state_and_signed():
    settings = make_settings()
    cookie, payload = oidc._state_cookie(settings)

    assert oidc.read_state_cookie(settings, cookie, payload["state"]) == payload
    assert oidc.read_state_cookie(settings, cookie, "substituted-state") is None
    encoded, _, signature = cookie.partition(".")
    assert (
        oidc.read_state_cookie(settings, f"{encoded}.{signature[:-1]}0", payload["state"]) is None
    )


@pytest.mark.asyncio
async def test_oidc_discovery_refuses_non_https_issuer():
    with pytest.raises(ValueError, match="OIDC issuer URL must be an HTTPS URL"):
        await oidc.discovery(make_settings(oidc_issuer_url="http://id.example.test"))

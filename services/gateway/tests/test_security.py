from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from starlette.requests import Request
from starlette.responses import JSONResponse

from services.gateway.app import MCPKeyMiddleware
from services.gateway.config import Settings
from services.gateway.models import ApiKey, ToolAudit, User, WebSession
from services.gateway.security import (
    Principal,
    authenticate_chat_assertion,
    create_chat_assertion,
    digest_secret,
    validate_upstream_url,
)
from services.gateway.upstreams import public_tool_name


def settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql+asyncpg://gateway:unused@localhost/gateway",
        "api_key_pepper": SecretStr("api-pepper"),
        "session_pepper": SecretStr("session-pepper"),
        "upstream_encryption_key": SecretStr(Fernet.generate_key().decode()),
        "bootstrap_admin_email": "admin@example.test",
        "bootstrap_admin_password": SecretStr("not-a-real-password"),
        "bootstrap_demo_token": SecretStr("demo-token"),
        "woo_mcp_token": SecretStr("woo-token"),
        "api_rpm_limit": 100,
    }
    values.update(overrides)
    return Settings(**values)


class SessionStub:
    def __init__(self, key: ApiKey, user: User):
        self.key = key
        self.user = user
        self.audit_rows: list[ToolAudit] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, model, identity):
        if model is ApiKey and identity == self.key.id:
            return self.key
        if model is User and identity == self.user.id:
            return self.user
        return None

    def add(self, record):
        if isinstance(record, ToolAudit):
            self.audit_rows.append(record)

    async def commit(self):
        return None


async def mcp_client_for(key: ApiKey, user: User, key_secret: str, config: Settings, monkeypatch):
    sessions_created: list[SessionStub] = []

    async def allow_rate_limited_request(*_args, **_kwargs):
        return True, config.api_rpm_limit - 1, 60

    # These tests exercise API-key authentication. Rate limiting has its own
    # PostgreSQL-backed session dependency, so keep that concern out of this stub.
    monkeypatch.setattr("services.gateway.app.consume_request", allow_rate_limited_request)

    @asynccontextmanager
    async def sessions():
        session = SessionStub(key, user)
        sessions_created.append(session)
        yield session

    app = FastAPI()
    app.add_middleware(MCPKeyMiddleware, sessions=sessions, settings=config)

    @app.get("/mcp")
    async def mcp_endpoint(request: Request):
        principal: Principal = request.state.principal
        return JSONResponse({"user": principal.email})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/mcp", headers={"Authorization": f"Bearer {key_secret}"})
    return response, sessions_created


@pytest.mark.asyncio
async def test_revoked_key_is_rejected_and_audited(monkeypatch):
    config = settings()
    user_id = uuid.uuid4()
    secret = "client-key-secret"
    key = ApiKey(
        id=uuid.uuid4().hex,
        user_id=user_id,
        digest=digest_secret(secret, config.api_key_pepper.get_secret_value()),
        revoked_at=datetime.now(UTC),
    )
    user = User(
        id=user_id, email="member@example.test", display_name="Member", role="member", active=True
    )

    response, sessions = await mcp_client_for(
        key, user, f"urm_{key.id}.{secret}", config, monkeypatch
    )

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"
    assert any(
        row.error_category == "revoked_key" for session in sessions for row in session.audit_rows
    )


@pytest.mark.asyncio
async def test_active_key_authenticates_the_database_principal(monkeypatch):
    config = settings()
    user_id = uuid.uuid4()
    secret = "client-key-secret"
    key = ApiKey(
        id=uuid.uuid4().hex,
        user_id=user_id,
        digest=digest_secret(secret, config.api_key_pepper.get_secret_value()),
    )
    user = User(
        id=user_id, email="member@example.test", display_name="Member", role="member", active=True
    )

    response, _ = await mcp_client_for(key, user, f"urm_{key.id}.{secret}", config, monkeypatch)

    assert response.status_code == 200
    assert response.json() == {"user": "member@example.test"}


def test_gateway_names_are_deterministic_and_collision_safe():
    first_used: set[str] = set()
    first = public_tool_name("inventory", "get stock", first_used)
    collision = public_tool_name("inventory", "get/stock", first_used)

    assert first == "inventory__get_stock"
    assert collision != first
    assert collision.endswith("__" + sha256(b"get/stock").hexdigest()[:10])
    assert public_tool_name("inventory", "get/stock", {first}) == collision


def test_internal_upstream_host_is_allowed_without_opening_public_private_addresses():
    config = settings(internal_hosts=["urumi-woocommerce-mcp"])

    assert (
        validate_upstream_url("http://urumi-woocommerce-mcp:8000/mcp", config)
        == "http://urumi-woocommerce-mcp:8000/mcp"
    )


def test_external_upstream_rejects_a_private_dns_answer(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )

    with pytest.raises(HTTPException, match="non-public address"):
        validate_upstream_url("https://mcp.example.test/mcp", settings())


@pytest.mark.asyncio
async def test_chat_assertion_is_signed_and_resolves_the_live_dashboard_principal():
    config = settings()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user = User(
        id=user_id,
        email="member@example.test",
        display_name="Member",
        role="member",
        active=True,
    )
    web_session = WebSession(
        id=session_id,
        user_id=user_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    class AssertionSession:
        async def get(self, model, identity):
            if model is WebSession and identity == session_id:
                return web_session
            if model is User and identity == user_id:
                return user
            return None

    token = create_chat_assertion(
        Principal(user_id, user.email, user.display_name, user.role, session_id=session_id),
        config,
    )
    principal, reason = await authenticate_chat_assertion(AssertionSession(), token, config)

    assert reason == "ok"
    assert principal is not None
    assert principal.user_id == user_id
    assert principal.role == "member"
    assert principal.key_id is None
    assert principal.session_id == session_id


@pytest.mark.asyncio
async def test_chat_assertion_rejects_modified_signature_and_expired_session():
    config = settings()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user = User(
        id=user_id,
        email="member@example.test",
        display_name="Member",
        role="member",
        active=True,
    )
    web_session = WebSession(
        id=session_id,
        user_id=user_id,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    class AssertionSession:
        async def get(self, model, identity):
            if model is WebSession and identity == session_id:
                return web_session
            if model is User and identity == user_id:
                return user
            return None

    token = create_chat_assertion(
        Principal(user_id, user.email, user.display_name, user.role, session_id=session_id),
        config,
    )
    invalid, invalid_reason = await authenticate_chat_assertion(
        AssertionSession(), token[:-1] + ("0" if token[-1] != "0" else "1"), config
    )
    expired, expired_reason = await authenticate_chat_assertion(AssertionSession(), token, config)

    assert invalid is None
    assert invalid_reason == "invalid_chat_assertion"
    assert expired is not None
    assert expired.user_id == user_id
    assert expired_reason == "inactive_chat_session"


@pytest.mark.asyncio
async def test_mcp_middleware_accepts_chat_assertion_as_the_live_database_user(monkeypatch):
    config = settings()

    async def allow_rate_limited_request(*_args, **_kwargs):
        return True, config.api_rpm_limit - 1, 60

    monkeypatch.setattr("services.gateway.app.consume_request", allow_rate_limited_request)
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user = User(
        id=user_id,
        email="member@example.test",
        display_name="Member",
        role="member",
        active=True,
    )
    web_session = WebSession(
        id=session_id,
        user_id=user_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    class ChatSession:
        def __init__(self):
            self.audit_rows: list[ToolAudit] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, model, identity):
            if model is WebSession and identity == session_id:
                return web_session
            if model is User and identity == user_id:
                return user
            return None

        def add(self, record):
            if isinstance(record, ToolAudit):
                self.audit_rows.append(record)

        async def commit(self):
            return None

    db_session = ChatSession()

    @asynccontextmanager
    async def sessions():
        yield db_session

    app = FastAPI()
    app.add_middleware(MCPKeyMiddleware, sessions=sessions, settings=config)

    @app.get("/mcp")
    async def mcp_endpoint(request: Request):
        principal: Principal = request.state.principal
        return JSONResponse(
            {
                "user": principal.email,
                "role": principal.role,
                "key_id": principal.key_id,
                "session_id": str(principal.session_id),
            }
        )

    assertion = create_chat_assertion(
        Principal(user_id, user.email, user.display_name, user.role, session_id=session_id), config
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/mcp", headers={"Authorization": f"Bearer {assertion}"})

    assert response.status_code == 200
    assert response.json() == {
        "user": "member@example.test",
        "role": "member",
        "key_id": None,
        "session_id": str(session_id),
    }

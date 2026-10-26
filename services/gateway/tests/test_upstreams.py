import uuid

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from services.gateway.config import Settings
from services.gateway.upstreams import (
    ServerConfig,
    UpstreamBusyError,
    UpstreamCallError,
    UpstreamManager,
    _operation_error,
    safe_error,
)


def test_sdk_exception_group_with_connect_error_is_classified_as_unreachable():
    error = ExceptionGroup("MCP transport failed", [httpx.ConnectError("connection refused")])

    assert safe_error(error) == "upstream_unreachable"
    routed = _operation_error(error, dispatched=True)
    assert isinstance(routed, UpstreamCallError)
    assert routed.outcome_unknown is True


def test_circuit_breaker_blocks_calls_then_allows_one_recovery_probe(monkeypatch):
    settings = Settings(
        database_url="postgresql+asyncpg://gateway:unused@localhost/gateway",
        api_key_pepper=SecretStr("api-pepper"),
        session_pepper=SecretStr("session-pepper"),
        upstream_encryption_key=SecretStr(Fernet.generate_key().decode()),
        bootstrap_admin_email="admin@example.test",
        bootstrap_admin_password=SecretStr("not-a-real-password"),
        bootstrap_demo_token=SecretStr("demo-token"),
        woo_mcp_token=SecretStr("woo-token"),
        circuit_breaker_failure_threshold=2,
        circuit_breaker_cooldown_seconds=5,
    )
    manager = UpstreamManager(None, settings)
    server = ServerConfig(
        uuid.uuid4(), "inventory", "https://inventory.example.test/mcp", None, "Bearer ", None, 1
    )
    clock = [100.0]
    monkeypatch.setattr("services.gateway.upstreams.time.monotonic", lambda: clock[0])

    manager._operation_failed(server, httpx.ConnectError("refused"))
    manager._operation_failed(server, httpx.ConnectError("refused"))
    with pytest.raises(UpstreamCallError, match="upstream_circuit_open"):
        manager._before_request(server)

    clock[0] = 106.0
    manager._before_request(server)
    with pytest.raises(UpstreamCallError, match="upstream_circuit_half_open"):
        manager._before_request(server)
    manager._operation_succeeded(server)
    manager._before_request(server)


def test_busy_upstream_does_not_trip_circuit_breaker():
    settings = Settings(
        database_url="postgresql+asyncpg://gateway:unused@localhost/gateway",
        api_key_pepper=SecretStr("api-pepper"),
        session_pepper=SecretStr("session-pepper"),
        upstream_encryption_key=SecretStr(Fernet.generate_key().decode()),
        bootstrap_admin_email="admin@example.test",
        bootstrap_admin_password=SecretStr("not-a-real-password"),
        bootstrap_demo_token=SecretStr("demo-token"),
        woo_mcp_token=SecretStr("woo-token"),
        circuit_breaker_failure_threshold=1,
    )
    manager = UpstreamManager(None, settings)
    server = ServerConfig(
        uuid.uuid4(), "inventory", "https://inventory.example.test/mcp", None, "Bearer ", None, 1
    )

    manager._operation_failed(server, UpstreamBusyError("queue full"))
    manager._before_request(server)

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr

from services.woocommerce_mcp.app import create_app, order_view
from services.woocommerce_mcp.client import StoreClient, StoreError
from services.woocommerce_mcp.config import Settings


def settings():
    return Settings(
        store_url="http://store.test",
        consumer_key=SecretStr("ck_test"),
        consumer_secret=SecretStr("cs_private"),
        gateway_token=SecretStr("x" * 48),
    )


@pytest.mark.asyncio
async def test_write_timeout_is_not_retried_and_is_sanitized():
    attempts = []

    def handler(request):
        attempts.append(request)
        raise httpx.ReadTimeout("secret cs_private", request=request)

    client = StoreClient(settings(), httpx.MockTransport(handler))
    with pytest.raises(StoreError, match="unknown_outcome") as error:
        await client.request("POST", "products", body={"name": "A"})
    assert len(attempts) == 1
    assert "cs_private" not in str(error.value)
    assert "consumer_secret" not in str(attempts[0].url)
    await client.close()


@pytest.mark.asyncio
async def test_redirect_and_oversized_response_rejected():
    for handler, message in [
        (
            lambda r: httpx.Response(302, headers={"location": "https://evil.test"}),
            "store_http_error",
        ),
        (lambda r: httpx.Response(200, content=b"x" * 1_048_577), "too_large"),
    ]:
        client = StoreClient(settings(), httpx.MockTransport(handler))
        with pytest.raises(StoreError, match=message):
            await client.request("GET", "products")
        await client.close()


def test_order_projection_removes_pii_and_payment_data():
    result = order_view(
        {
            "id": 12,
            "billing": {"email": "private@example.test"},
            "order_key": "secret",
            "customer_ip_address": "1.2.3.4",
            "line_items": [{"name": "Notebook", "meta_data": [{"private": "x"}]}],
        }
    )
    assert "billing" not in result and "order_key" not in result
    assert "meta_data" not in result["items"][0]


@asynccontextmanager
async def running_app(app):
    # Real sockets and official MCP client exercise transport/auth/input validation.
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task
        sock.close()


@pytest.mark.asyncio
async def test_real_mcp_flow_auth_validation_and_timezone():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[], headers={"X-WP-Total": "0", "X-WP-TotalPages": "0"})

    config = settings()
    app = create_app(config, StoreClient(config, httpx.MockTransport(handler)))
    async with running_app(app) as base:
        async with httpx.AsyncClient() as http:
            for method in ("GET", "POST", "DELETE"):
                response = await http.request(method, base + "/mcp")
                assert response.status_code == 401
            assert (await http.get(base + "/healthz")).status_code == 200
        async with (
            httpx.AsyncClient(headers={"Authorization": "Bearer " + "x" * 48}) as auth_http,
            streamable_http_client(base + "/mcp", http_client=auth_http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert {t.name for t in tools} == {
                "list_products",
                "create_product",
                "list_orders",
                "get_order",
                "update_order_status",
            }
            before = len(requests)
            result = await session.call_tool("create_product", {"name": "A", "price": "-1"})
            assert result.isError and len(requests) == before
            result = await session.call_tool(
                "update_order_status", {"order_id": 12, "status": "refunded"}
            )
            assert result.isError and len(requests) == before
            result = await session.call_tool("list_products", {"per_page": 51})
            assert result.isError and len(requests) == before
            result = await session.call_tool("list_orders", {"today": True})
            assert not result.isError
            assert result.structuredContent is not None
            assert requests[-1].url.params["after"].endswith("T00:00:00")
            assert requests[-1].url.params["before"].endswith("T23:59:59")


@pytest.mark.asyncio
async def test_canonical_signing_url_and_https_auth():
    recorded = []

    def handler(request):
        recorded.append(request)
        return httpx.Response(200, json=[])

    config = settings().model_copy(update={"signing_url": "http://store.localhost:8080"})
    client = StoreClient(config, httpx.MockTransport(handler))
    await client.request("GET", "products", params={"search": "tea & coffee"})
    # Actual destination stays private; a canonical signing URL never redirects traffic.
    assert recorded[-1].url.host == "store.test"
    assert recorded[-1].url.params["search"] == "tea & coffee"
    assert recorded[-1].url.params["oauth_signature"]
    assert "cs_private" not in str(recorded[-1].url)
    await client.close()
    config = settings().model_copy(update={"store_url": "https://store.test"})
    client = StoreClient(config, httpx.MockTransport(handler))
    await client.request("GET", "products")
    assert recorded[-1].headers["authorization"].startswith("Basic ")
    assert not recorded[-1].url.query
    await client.close()

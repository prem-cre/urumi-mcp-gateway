"""Run inside an ephemeral gateway-labeled verification Job, never publicly expose MCP."""

import asyncio
import os
import uuid

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    endpoint = "http://urumi-woocommerce-mcp:8000/mcp"
    async with httpx.AsyncClient(timeout=30) as anonymous:
        assert (await anonymous.post(endpoint)).status_code == 401
    async with (
        httpx.AsyncClient(
            headers={"Authorization": "Bearer " + os.environ["GATEWAY_TOKEN"]}, timeout=30
        ) as http,
        streamable_http_client(endpoint, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        initialized = await session.initialize()
        print("MCP negotiated:", initialized.protocolVersion)
        names = {tool.name for tool in (await session.list_tools()).tools}
        assert names == {
            "list_products",
            "create_product",
            "list_orders",
            "get_order",
            "update_order_status",
        }
        print("PASS authentication and discovery: five required tools")

        async def call(name, arguments):
            result = await session.call_tool(name, arguments)
            if result.isError:
                raise RuntimeError(f"{name} failed: {result.content}")
            return result.structuredContent

        products = await call("list_products", {"per_page": 5})
        assert products["products"]
        product = await call(
            "create_product",
            {
                "name": "MCP Verification Notebook",
                "price": "349.00",
                "sku": "MCP-VERIFY-" + uuid.uuid4().hex[:8],
            },
        )
        assert product["status"] == "publish" and product["regular_price"] == "349.00"
        print("PASS create_product; published product ID:", product["id"])
        orders = await call("list_orders", {"today": True})
        assert orders["orders"], "Place a fresh dummy COD order today before running this check"
        order = orders["orders"][0]
        assert all(key not in order for key in ("billing", "shipping", "order_key"))
        detail = await call("get_order", {"order_id": order["id"]})
        assert detail["id"] == order["id"]
        print("PASS list_orders(today), get_order; order ID:", order["id"])
        # The caller must explicitly supply the dummy verification order ID.
        fixture_id = int(os.environ["FIXTURE_ORDER_ID"])
        original = await call("get_order", {"order_id": fixture_id})
        assert original["status"] == "processing", "Fixture must start processing"
        changed = await call("update_order_status", {"order_id": fixture_id, "status": "on-hold"})
        assert changed["status"] == "on-hold"
        restored = await call(
            "update_order_status", {"order_id": fixture_id, "status": original["status"]}
        )
        assert restored["status"] == original["status"]
        print("PASS update_order_status on fixture and restore:", fixture_id)
        invalid = await session.call_tool("create_product", {"name": "Bad", "price": "-3"})
        assert invalid.isError
        missing = await session.call_tool("get_order", {"order_id": 99999999})
        assert missing.isError
        print("PASS validation and not-found error")


asyncio.run(main())

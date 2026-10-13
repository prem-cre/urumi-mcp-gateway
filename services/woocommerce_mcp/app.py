"""Own tools-only MCP service. Run with uvicorn services.woocommerce_mcp.app:create_app --factory."""

import hmac
import logging
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.responses import JSONResponse

from .client import StoreClient, StoreError
from .config import Settings

PositiveID = Annotated[int, Field(gt=0, strict=True)]
Page = Annotated[int, Field(ge=1, le=10000, strict=True)]
Size = Annotated[int, Field(ge=1, le=50, strict=True)]
Status = Literal["pending", "processing", "on-hold", "completed", "cancelled", "failed"]
OrderFilter = Literal[
    "any", "pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"
]
Price = Annotated[str, Field(pattern=r"^(0|[1-9]\d{0,7})(\.\d{1,2})?$", max_length=11)]
Name = Annotated[str, Field(min_length=1, max_length=160)]


def product_view(item: dict) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "id",
            "name",
            "sku",
            "status",
            "regular_price",
            "price",
            "stock_status",
            "permalink",
        )
    }


def order_view(item: dict) -> dict[str, Any]:
    return {
        **{
            key: item.get(key)
            for key in (
                "id",
                "number",
                "status",
                "currency",
                "total",
                "date_created",
                "date_created_gmt",
            )
        },
        "items": [
            {key: line.get(key) for key in ("product_id", "name", "sku", "quantity", "total")}
            for line in item.get("line_items", [])
        ],
    }


class GatewayAuth:
    """Pure ASGI middleware: validate every MCP request without consuming its body."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (
            scope["path"] == "/mcp" or scope["path"].startswith("/mcp/")
        ):
            headers = dict(scope.get("headers", []))
            presented = headers.get(b"authorization", b"")
            expected = f"Bearer {self.token}".encode()
            if not hmac.compare_digest(presented, expected):
                await JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None, client: StoreClient | None = None) -> FastAPI:
    settings = settings or Settings()
    store = client or StoreClient(settings)
    mcp = FastMCP(
        "WooCommerce",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=[],
        ),
    )
    # FastMCP installs a root handler; HTTP client INFO logs contain signed URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    read = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
    write = ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
    )

    async def query(method, resource, **kwargs):
        try:
            return await store.request(method, resource, **kwargs)
        except StoreError as exc:
            # SDK converts this to an isError tool result; no HTTPX URLs/credentials leak.
            raise ValueError(str(exc)) from None

    @mcp.tool(annotations=read, structured_output=True)
    async def list_products(
        page: Page = 1, per_page: Size = 20, search: Annotated[str, Field(max_length=160)] = ""
    ) -> dict[str, Any]:
        """List product summaries with pagination; no customer data is returned."""
        data, headers = await query(
            "GET", "products", params={"page": page, "per_page": per_page, "search": search}
        )
        return {
            "products": [product_view(p) for p in data],
            "page": page,
            "total": int(headers.get("X-WP-Total", len(data))),
            "total_pages": int(headers.get("X-WP-TotalPages", 1)),
        }

    @mcp.tool(annotations=write, structured_output=True)
    async def create_product(
        name: Name,
        price: Price,
        sku: Annotated[str, Field(max_length=64)] = "",
        publish: bool = True,
    ) -> dict[str, Any]:
        """Create a simple product with a decimal price in store currency. This writes to the store; never automatically retry an uncertain result."""
        if not name.strip():
            raise ValueError("invalid_name: name must contain non-whitespace characters")
        body = {
            "name": name.strip(),
            "type": "simple",
            "regular_price": f"{Decimal(price):.2f}",
            "status": "publish" if publish else "draft",
        }
        if sku:
            body["sku"] = sku
        data, _ = await query("POST", "products", body=body)
        return product_view(data)

    @mcp.tool(annotations=read, structured_output=True)
    async def list_orders(
        page: Page = 1, per_page: Size = 20, status: OrderFilter = "any", today: bool = False
    ) -> dict[str, Any]:
        """List privacy-minimized order summaries. today uses the configured store timezone, not the model's guessed date."""
        params = {"page": page, "per_page": per_page, "status": status}
        if today:
            day = datetime.now(ZoneInfo(settings.timezone)).date()
            params.update(
                after=datetime.combine(day, time.min).isoformat(),
                before=(
                    datetime.combine(day + timedelta(days=1), time.min) - timedelta(seconds=1)
                ).isoformat(),
            )
        data, headers = await query("GET", "orders", params=params)
        return {
            "orders": [order_view(o) for o in data],
            "page": page,
            "timezone": settings.timezone,
            "total": int(headers.get("X-WP-Total", len(data))),
            "total_pages": int(headers.get("X-WP-TotalPages", 1)),
        }

    @mcp.tool(annotations=read, structured_output=True)
    async def get_order(order_id: PositiveID) -> dict[str, Any]:
        """Get one order's items, totals and status. Names, email, addresses and payment secrets are omitted."""
        data, _ = await query("GET", f"orders/{order_id}")
        return order_view(data)

    @mcp.tool(annotations=write, structured_output=True)
    async def update_order_status(order_id: PositiveID, status: Status) -> dict[str, Any]:
        """Change an order status. This can trigger stock/email effects. Refunds and deletion are deliberately unsupported; never automatically retry."""
        data, _ = await query("PUT", f"orders/{order_id}", body={"status": status})
        return order_view(data)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield
        await store.close()

    app = FastAPI(
        title="WooCommerce MCP", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.add_middleware(GatewayAuth, token=settings.gateway_token.get_secret_value())

    @app.get("/healthz")
    async def health():
        return {"status": "alive"}

    @app.get("/readyz")
    async def ready():
        try:
            await store.request("GET", "products", params={"per_page": 1, "_fields": "id"})
        except StoreError:
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return {"status": "ready"}

    app.mount("/", mcp.streamable_http_app())
    return app

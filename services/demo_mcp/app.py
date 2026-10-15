"""Two small, read-only MCP sources: sample inventory and fulfillment."""

import hmac
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.responses import JSONResponse


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEMO_MCP_", extra="ignore")
    kind: Literal["inventory", "fulfillment"]
    gateway_token: SecretStr
    allowed_hosts: list[str] = ["urumi-inventory-mcp:8000", "urumi-fulfillment-mcp:8000"]


INVENTORY = [
    {
        "sku": "URUMI-DEMO-001",
        "name": "Urumi Demo Notebook",
        "available": 42,
        "warehouse": "BLR-01",
    },
    {"sku": "URUMI-PEN-002", "name": "Urumi Gel Pen", "available": 9, "warehouse": "BLR-01"},
    {"sku": "URUMI-MUG-003", "name": "Urumi Ceramic Mug", "available": 0, "warehouse": "DEL-02"},
]
SAMPLE_SHIPMENTS = [
    {"shipment_id": "SHP-1001", "order_id": 1001, "status": "in_transit", "carrier": "Blue Dart"},
    {
        "shipment_id": "SHP-1002",
        "order_id": 1002,
        "status": "label_created",
        "carrier": "Delhivery",
    },
    {"shipment_id": "SHP-1003", "order_id": 1003, "status": "delivered", "carrier": "India Post"},
]


class GatewayAuth:
    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            headers = dict(scope.get("headers", []))
            presented = headers.get(b"authorization", b"")
            if not hmac.compare_digest(presented, f"Bearer {self.token}".encode()):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    mcp = FastMCP(
        f"Urumi {settings.kind.title()} Demo",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=[],
        ),
    )
    read = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )

    if settings.kind == "inventory":

        @mcp.tool(annotations=read, structured_output=True)
        async def check_stock(sku: Annotated[str, Field(min_length=1, max_length=64)]) -> dict:
            """Look up sample on-hand stock for a store product SKU."""
            item = next((row for row in INVENTORY if row["sku"].lower() == sku.lower()), None)
            return item or {"sku": sku, "available": 0, "warehouse": None, "found": False}

        @mcp.tool(annotations=read, structured_output=True)
        async def list_inventory(
            page: Annotated[int, Field(ge=1, le=10)] = 1,
            per_page: Annotated[int, Field(ge=1, le=20)] = 20,
            search: Annotated[str, Field(max_length=80)] = "",
        ) -> dict:
            """List bounded sample inventory records, optionally filtered by name or SKU."""
            rows = [
                row
                for row in INVENTORY
                if search.lower() in row["name"].lower() or search.lower() in row["sku"].lower()
            ]
            start = (page - 1) * per_page
            return {"items": rows[start : start + per_page], "page": page, "total": len(rows)}

        @mcp.tool(annotations=read, structured_output=True)
        async def get_low_stock(threshold: Annotated[int, Field(ge=0, le=100)] = 10) -> dict:
            """List sample inventory items at or below a requested stock threshold."""
            return {
                "threshold": threshold,
                "items": [row for row in INVENTORY if row["available"] <= threshold],
            }

    else:

        @mcp.tool(annotations=read, structured_output=True)
        async def get_shipment(
            shipment_id: Annotated[str, Field(min_length=4, max_length=32)],
        ) -> dict:
            """Get one explicitly labeled sample shipment record."""
            return next(
                (
                    row
                    for row in SAMPLE_SHIPMENTS
                    if row["shipment_id"].lower() == shipment_id.lower()
                ),
                {"shipment_id": shipment_id, "found": False, "source": "sample data"},
            )

        @mcp.tool(annotations=read, structured_output=True)
        async def list_shipments(
            page: Annotated[int, Field(ge=1, le=10)] = 1,
            per_page: Annotated[int, Field(ge=1, le=20)] = 20,
        ) -> dict:
            """List bounded sample shipments; records are demonstration data."""
            start = (page - 1) * per_page
            return {
                "shipments": SAMPLE_SHIPMENTS[start : start + per_page],
                "page": page,
                "total": len(SAMPLE_SHIPMENTS),
            }

        @mcp.tool(annotations=read, structured_output=True)
        async def get_delivery_estimate(
            postal_code: Annotated[str, Field(pattern=r"^[0-9]{6}$")],
        ) -> dict:
            """Return a sample delivery estimate for a six-digit Indian postal code."""
            return {"postal_code": postal_code, "business_days": 3, "source": "sample estimate"}

        @mcp.tool(annotations=read, structured_output=True)
        async def list_carriers() -> dict:
            """List carriers available in the sample fulfillment data."""
            return {"carriers": ["Blue Dart", "Delhivery", "India Post"], "source": "sample data"}

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title=f"Urumi {settings.kind.title()} MCP", lifespan=lifespan, docs_url=None)
    app.add_middleware(GatewayAuth, token=settings.gateway_token.get_secret_value())

    @app.get("/healthz")
    async def healthz():
        return {"status": "alive"}

    @app.get("/readyz")
    async def readyz():
        return {"status": "ready"}

    app.mount("/", mcp.streamable_http_app())
    return app

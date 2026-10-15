"""Idempotent workspace bootstrap: one admin and the three demo MCP servers."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .config import Settings
from .models import UpstreamServer, User
from .security import encrypt_credential, hash_password


async def bootstrap_workspace(sessions: async_sessionmaker, settings: Settings) -> None:
    async with sessions() as session:
        admin_count = await session.scalar(select(func.count(User.id)).where(User.role == "admin"))
        if not admin_count:
            session.add(
                User(
                    email=settings.bootstrap_admin_email,
                    display_name=settings.bootstrap_admin_name,
                    role="admin",
                    password_hash=hash_password(
                        settings.bootstrap_admin_password.get_secret_value()
                    ),
                )
            )
        server_count = await session.scalar(
            select(func.count(UpstreamServer.id)).where(UpstreamServer.deleted_at.is_(None))
        )
        if server_count:
            await session.commit()
            return
        demo_token = settings.bootstrap_demo_token.get_secret_value()
        woo_token = settings.woo_mcp_token.get_secret_value()
        servers = [
            {
                "alias": "woocommerce",
                "display_name": "WooCommerce",
                "description": "Products and privacy-minimized store orders.",
                "url": settings.woo_mcp_url,
                "credential": woo_token,
                "member_tools": ["list_products", "list_orders", "get_order"],
            },
            {
                "alias": "inventory",
                "display_name": "Inventory",
                "description": "Sample stock levels for the assessment store.",
                "url": settings.inventory_mcp_url,
                "credential": demo_token,
                "member_tools": ["check_stock", "list_inventory", "get_low_stock"],
            },
            {
                "alias": "fulfillment",
                "display_name": "Fulfillment",
                "description": "Sample shipment status and delivery estimates.",
                "url": settings.fulfillment_mcp_url,
                "credential": demo_token,
                "member_tools": [
                    "get_shipment",
                    "list_shipments",
                    "get_delivery_estimate",
                    "list_carriers",
                ],
            },
        ]
        for item in servers:
            session.add(
                UpstreamServer(
                    alias=item["alias"],
                    display_name=item["display_name"],
                    description=item["description"],
                    url=item["url"],
                    auth_header="Authorization",
                    auth_prefix="Bearer ",
                    credential_ciphertext=encrypt_credential(item["credential"], settings),
                    enabled=True,
                    status="unreachable",
                    member_tools=item["member_tools"],
                )
            )
        await session.commit()

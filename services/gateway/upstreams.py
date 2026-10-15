"""Upstream MCP client operations, safe catalog refresh and tool dispatch."""

import asyncio
import hashlib
import json
import re
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass

import httpx
import jsonschema
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Tool
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings
from .models import DiscoveredTool, UpstreamServer, now_utc
from .security import decrypt_credential, validate_upstream_url

_TOOL_NAME = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True)
class ServerConfig:
    id: uuid.UUID
    alias: str
    url: str
    auth_header: str | None
    auth_prefix: str
    credential_ciphertext: str | None
    revision: int


@dataclass
class UpstreamConnection:
    revision: int
    stack: AsyncExitStack
    session: ClientSession


class UpstreamTimeoutError(Exception):
    def __init__(self, outcome_unknown: bool):
        self.outcome_unknown = outcome_unknown
        super().__init__("upstream_timeout")


class UpstreamBusyError(Exception):
    """A health check skipped a server that is serving a real request."""


class UpstreamCallError(Exception):
    """A transport/protocol failure after dispatch may have left a write uncertain."""

    def __init__(
        self, outcome_unknown: bool = True, category: str = "upstream_transport_interrupted"
    ):
        self.outcome_unknown = outcome_unknown
        self.category = category
        super().__init__(category)


def server_config(server: UpstreamServer) -> ServerConfig:
    return ServerConfig(
        id=server.id,
        alias=server.alias,
        url=server.url,
        auth_header=server.auth_header,
        auth_prefix=server.auth_prefix,
        credential_ciphertext=server.credential_ciphertext,
        revision=server.revision,
    )


def public_tool_name(alias: str, original: str, used: set[str]) -> str:
    clean = _TOOL_NAME.sub("_", original).strip("_") or "tool"
    base = f"{alias}__{clean}"[:240]
    if base not in used:
        used.add(base)
        return base
    suffix = hashlib.sha256(original.encode()).hexdigest()[:10]
    name = f"{base[:228]}__{suffix}"
    used.add(name)
    return name


class UpstreamManager:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings):
        self.sessions = sessions
        self.settings = settings
        self._operation_locks: dict[uuid.UUID, asyncio.Lock] = {}
        self._connections: dict[uuid.UUID, UpstreamConnection] = {}
        self._invalidation_tasks: set[asyncio.Task] = set()

    def _operation_lock(self, server_id: uuid.UUID) -> asyncio.Lock:
        return self._operation_locks.setdefault(server_id, asyncio.Lock())

    def _headers(self, server: ServerConfig) -> dict[str, str]:
        credential = decrypt_credential(server.credential_ciphertext, self.settings)
        if not credential or not server.auth_header:
            return {}
        return {server.auth_header: f"{server.auth_prefix}{credential}"}

    async def _client(self, server: ServerConfig, timeout_seconds: float) -> httpx.AsyncClient:
        # Revalidate before every network connection; redirects and ambient proxy settings are disabled.
        await asyncio.to_thread(validate_upstream_url, server.url, self.settings)
        return httpx.AsyncClient(
            headers=self._headers(server),
            timeout=httpx.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds)),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

    async def _connection(self, server: ServerConfig, timeout: float) -> UpstreamConnection:
        current = self._connections.get(server.id)
        if current and current.revision == server.revision:
            return current
        if current:
            await self._discard_locked(server.id)
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            http_client = await self._client(server, timeout)
            await stack.enter_async_context(http_client)
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(server.url, http_client=http_client)
            )
            client_session = ClientSession(read_stream, write_stream)
            await stack.enter_async_context(client_session)
            async with asyncio.timeout(timeout):
                await client_session.initialize()
            connection = UpstreamConnection(server.revision, stack, client_session)
            self._connections[server.id] = connection
            return connection
        except BaseException:
            await stack.aclose()
            raise

    async def _discard_locked(self, server_id: uuid.UUID) -> None:
        connection = self._connections.pop(server_id, None)
        if connection is not None:
            await connection.stack.aclose()

    async def invalidate_server(self, server_id: uuid.UUID) -> None:
        async with self._operation_lock(server_id):
            await self._discard_locked(server_id)

    def schedule_invalidation(self, server_id: uuid.UUID) -> None:
        task = asyncio.create_task(self.invalidate_server(server_id))
        self._invalidation_tasks.add(task)
        task.add_done_callback(self._invalidation_tasks.discard)

    async def close(self) -> None:
        if self._invalidation_tasks:
            await asyncio.gather(*self._invalidation_tasks, return_exceptions=True)
        await asyncio.gather(
            *(self.invalidate_server(server_id) for server_id in list(self._connections))
        )

    async def list_tools(
        self, server: UpstreamServer | ServerConfig, timeout: float | None = None
    ) -> list[Tool]:
        limit = timeout or self.settings.discovery_timeout_seconds
        config = server_config(server) if isinstance(server, UpstreamServer) else server
        lock = self._operation_lock(config.id)
        try:
            async with asyncio.timeout(0.05):
                await lock.acquire()
        except TimeoutError as exc:
            raise UpstreamBusyError("upstream_busy") from exc
        try:
            async with asyncio.timeout(limit):
                connection = await self._connection(config, limit)
                try:
                    tools: list[Tool] = []
                    cursor = None
                    for _ in range(20):
                        result = await connection.session.list_tools(cursor=cursor)
                        tools.extend(result.tools)
                        if len(tools) > 500:
                            raise ValueError("upstream_tool_catalog_too_large")
                        cursor = result.nextCursor
                        if not cursor:
                            return tools
                    raise ValueError("upstream_tool_catalog_too_large")
                except Exception:
                    await self._discard_locked(config.id)
                    raise
        finally:
            lock.release()

    async def refresh_one(self, server_id: uuid.UUID) -> tuple[bool, str | None]:
        async with self.sessions() as session:
            server = await session.get(UpstreamServer, server_id)
            if server is None or server.deleted_at is not None:
                return False, "server_removed"
            detached = server_config(server)
            await session.commit()
            try:
                tools = await self.list_tools(detached)
                used: set[str] = set()
                normalized: list[tuple[Tool, str, str]] = []
                if len(tools) > 500:
                    raise ValueError("upstream_tool_catalog_too_large")
                seen_original_names: set[str] = set()
                for tool in sorted(tools, key=lambda item: item.name):
                    if tool.name in seen_original_names:
                        continue
                    seen_original_names.add(tool.name)
                    if len(tool.name) > 256:
                        continue
                    schema = tool.inputSchema or {"type": "object", "properties": {}}
                    if (
                        len(json.dumps(schema, separators=(",", ":")))
                        > self.settings.max_tool_schema_bytes
                    ):
                        continue
                    jsonschema.validators.validator_for(schema).check_schema(schema)
                    public_name = public_tool_name(server.alias, tool.name, used)
                    payload = tool.model_dump(mode="json", exclude_none=True)
                    schema_hash = hashlib.sha256(
                        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                    normalized.append((tool, public_name, schema_hash))
                old_tools = list(
                    (
                        await session.scalars(
                            select(DiscoveredTool).where(DiscoveredTool.server_id == server.id)
                        )
                    ).all()
                )
                new_hashes = {tool.name: schema_hash for tool, _, schema_hash in normalized}
                member_grants = set(server.member_tools or [])
                for old_tool in old_tools:
                    if new_hashes.get(old_tool.original_name) != old_tool.schema_hash:
                        member_grants.discard(old_tool.original_name)
                server.member_tools = sorted(member_grants)
                await session.execute(
                    delete(DiscoveredTool).where(DiscoveredTool.server_id == server.id)
                )
                for tool, public_name, schema_hash in normalized:
                    annotations = (
                        tool.annotations.model_dump(mode="json", exclude_none=True)
                        if tool.annotations
                        else {}
                    )
                    await session.merge(
                        DiscoveredTool(
                            server_id=server.id,
                            original_name=tool.name,
                            public_name=public_name,
                            description=(tool.description or "")[:1000],
                            input_schema=tool.inputSchema or {"type": "object", "properties": {}},
                            output_schema=tool.outputSchema,
                            annotations=annotations,
                            schema_hash=schema_hash,
                            discovered_at=now_utc(),
                        )
                    )
                server.last_check_at = now_utc()
                server.last_success_at = server.last_check_at
                server.last_error = None
                server.failure_count = 0
                server.status = "connected" if server.enabled else "disabled"
                await session.commit()
                return True, None
            except UpstreamBusyError:
                # Do not erase a known-good catalog or mark a healthy server unreachable because
                # a periodic discovery happened to overlap with a user call.
                return False, "upstream_busy"
            except Exception as exc:  # noqa: BLE001 - SDK, schema and transport errors share the health path.
                await session.rollback()
                server = await session.get(UpstreamServer, server_id)
                if server:
                    server.last_check_at = now_utc()
                    server.failure_count += 1
                    server.last_error = safe_error(exc)
                    server.status = "disabled" if not server.enabled else "unreachable"
                    await session.commit()
                return False, safe_error(exc)

    async def refresh_all(self) -> None:
        async with self.sessions() as session:
            rows = list(
                (
                    await session.scalars(
                        select(UpstreamServer).where(UpstreamServer.deleted_at.is_(None))
                    )
                ).all()
            )
            ids = [row.id for row in rows]
        await asyncio.gather(*(self.refresh_one(server_id) for server_id in ids))

    async def call_tool(
        self, server: ServerConfig, original_name: str, arguments: dict
    ) -> CallToolResult:
        timeout = min(max(server_timeout(server, self.settings), 1.0), 60.0)
        lock = self._operation_lock(server.id)
        dispatched = False
        try:
            async with asyncio.timeout(timeout):
                async with lock:
                    connection = await self._connection(server, timeout)
                    dispatched = True
                    try:
                        return await connection.session.call_tool(original_name, arguments)
                    except TimeoutError as exc:
                        await self._discard_locked(server.id)
                        raise UpstreamTimeoutError(True) from exc
                    except httpx.TimeoutException as exc:
                        await self._discard_locked(server.id)
                        raise UpstreamTimeoutError(True) from exc
                    except httpx.HTTPStatusError as exc:
                        await self._discard_locked(server.id)
                        if exc.response.status_code in {401, 403}:
                            raise UpstreamCallError(False, "upstream_auth_failed") from exc
                        raise UpstreamCallError() from exc
                    except Exception as exc:
                        await self._discard_locked(server.id)
                        raise UpstreamCallError() from exc
        except TimeoutError as exc:
            # A timeout while waiting for a session or initializing is known not to have called the tool.
            # Once call_tool has begun, the upstream may have committed a write without returning a reply.
            raise UpstreamTimeoutError(dispatched) from exc


def server_timeout(server: UpstreamServer, settings: Settings) -> float:
    # Per-server overrides are added through the API in the next control-plane iteration.
    return settings.tool_timeout_seconds


def safe_error(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "upstream_timeout"
    if isinstance(exc, httpx.ConnectError):
        return "upstream_unreachable"
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code in {401, 403}:
            return "upstream_auth_failed"
        return "upstream_http_error"
    return "upstream_protocol_error"


def result_size(result: CallToolResult) -> int:
    return len(result.model_dump_json().encode())

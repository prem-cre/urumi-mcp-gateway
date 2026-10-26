"""Upstream MCP client operations, safe catalog refresh and tool dispatch."""

import asyncio
import hashlib
import json
import logging
import re
import time
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
from .metrics import UPSTREAM_CIRCUIT_STATE
from .models import DiscoveredTool, UpstreamServer, now_utc
from .security import decrypt_credential, validate_upstream_url

_TOOL_NAME = re.compile(r"[^A-Za-z0-9_-]+")
logger = logging.getLogger("urumi.gateway.upstreams")


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
class UpstreamCommand:
    kind: str
    timeout: float
    future: asyncio.Future
    name: str | None = None
    arguments: dict | None = None
    dispatched: bool = False


@dataclass
class UpstreamWorker:
    revision: int
    queue: asyncio.Queue
    task: asyncio.Task


@dataclass
class CircuitState:
    failures: int = 0
    open_until: float = 0.0
    half_open: bool = False


class UpstreamTimeoutError(Exception):
    def __init__(self, outcome_unknown: bool):
        self.outcome_unknown = outcome_unknown
        super().__init__("upstream_timeout")


class UpstreamBusyError(Exception):
    """A health check skipped a server that is serving a real request."""


class UpstreamCallError(Exception):
    """A transport/protocol failure with dispatch certainty attached."""

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
        self._workers: dict[uuid.UUID, UpstreamWorker] = {}
        self._invalidation_tasks: set[asyncio.Task] = set()
        self._circuits: dict[uuid.UUID, CircuitState] = {}

    def _before_request(self, server: ServerConfig) -> None:
        state = self._circuits.setdefault(server.id, CircuitState())
        now = time.monotonic()
        if state.open_until > now:
            raise UpstreamCallError(False, "upstream_circuit_open")
        if state.open_until and not state.half_open:
            # One serialized probe is allowed after the cooldown. Other calls stay blocked.
            state.half_open = True
        elif state.half_open:
            raise UpstreamCallError(False, "upstream_circuit_half_open")

    def _operation_succeeded(self, server: ServerConfig) -> None:
        state = self._circuits.get(server.id)
        if state and state.open_until:
            UPSTREAM_CIRCUIT_STATE.labels(server.alias, "closed").inc()
        self._circuits[server.id] = CircuitState()

    def _operation_failed(self, server: ServerConfig, error: Exception) -> None:
        if isinstance(error, UpstreamBusyError):
            state = self._circuits.get(server.id)
            if state and state.half_open:
                state.half_open = False
                state.open_until = 0
            return
        state = self._circuits.setdefault(server.id, CircuitState())
        state.failures += 1
        if state.half_open or state.failures >= self.settings.circuit_breaker_failure_threshold:
            state.open_until = time.monotonic() + self.settings.circuit_breaker_cooldown_seconds
            state.half_open = False
            UPSTREAM_CIRCUIT_STATE.labels(server.alias, "open").inc()

    def _release_half_open(self, server: ServerConfig) -> None:
        state = self._circuits.get(server.id)
        if state and state.half_open:
            state.half_open = False
            state.open_until = 0

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

    async def _worker_locked(self, server: ServerConfig) -> UpstreamWorker:
        current = self._workers.get(server.id)
        if current and current.revision == server.revision and not current.task.done():
            return current
        if current:
            await self._stop_worker_locked(server.id)
        queue = asyncio.Queue(maxsize=max(4, self.settings.upstream_concurrency * 4))
        task = asyncio.create_task(
            self._run_worker(server, queue), name=f"mcp-upstream-{server.alias}"
        )
        worker = UpstreamWorker(server.revision, queue, task)
        self._workers[server.id] = worker
        return worker

    async def _enqueue_locked(
        self,
        server: ServerConfig,
        kind: str,
        timeout: float,
        name: str | None = None,
        arguments: dict | None = None,
    ) -> UpstreamCommand:
        worker = await self._worker_locked(server)
        future = asyncio.get_running_loop().create_future()
        future.add_done_callback(_consume_future_exception)
        command = UpstreamCommand(kind, timeout, future, name, arguments)
        try:
            worker.queue.put_nowait(command)
        except asyncio.QueueFull as exc:
            raise UpstreamBusyError("upstream_queue_full") from exc
        return command

    async def _run_worker(self, server: ServerConfig, queue: asyncio.Queue) -> None:
        """Own the upstream MCP session in one task for its entire lifetime.

        The Python MCP client's Streamable HTTP transport owns an AnyIO task group.
        Keeping the session in a dedicated worker avoids closing that task group from
        a different HTTP request task when a server is edited, disabled, or removed.
        """
        stack: AsyncExitStack | None = None
        client_session: ClientSession | None = None
        while True:
            command = await queue.get()
            if command.kind == "close":
                if stack is not None:
                    await _close_stack(stack)
                if not command.future.done():
                    command.future.set_result(None)
                return

            try:
                async with asyncio.timeout(command.timeout):
                    if client_session is None:
                        stack = AsyncExitStack()
                        await stack.__aenter__()
                        try:
                            http_client = await self._client(server, command.timeout)
                            await stack.enter_async_context(http_client)
                            read_stream, write_stream, _ = await stack.enter_async_context(
                                streamable_http_client(server.url, http_client=http_client)
                            )
                            client_session = ClientSession(read_stream, write_stream)
                            await stack.enter_async_context(client_session)
                            await client_session.initialize()
                        except BaseException:
                            await _close_stack(stack)
                            stack = None
                            client_session = None
                            raise

                    if command.kind == "list_tools":
                        result = await self._list_session_tools(client_session)
                    elif command.kind == "call_tool":
                        command.dispatched = True
                        result = await client_session.call_tool(
                            command.name or "", command.arguments or {}
                        )
                    else:
                        raise ValueError("unknown_upstream_operation")
                self._operation_succeeded(server)
                if not command.future.done():
                    command.future.set_result(result)
            except asyncio.CancelledError:
                if stack is not None:
                    await _close_stack(stack)
                    stack = None
                    client_session = None
                if not command.future.done():
                    command.future.set_exception(
                        UpstreamCallError(
                            command.dispatched,
                            "upstream_transport_interrupted"
                            if command.dispatched
                            else "upstream_unreachable",
                        )
                    )
                raise
            except Exception as exc:  # noqa: BLE001 - normalize SDK/transport failures for this command.
                self._operation_failed(server, exc)
                if stack is not None:
                    await _close_stack(stack)
                    stack = None
                    client_session = None
                if not command.future.done():
                    command.future.set_exception(_operation_error(exc, command.dispatched))

    async def _list_session_tools(self, session: ClientSession) -> list[Tool]:
        tools: list[Tool] = []
        cursor = None
        for _ in range(20):
            result = await session.list_tools(cursor=cursor)
            tools.extend(result.tools)
            if len(tools) > 500:
                raise ValueError("upstream_tool_catalog_too_large")
            cursor = result.nextCursor
            if not cursor:
                return tools
        raise ValueError("upstream_tool_catalog_too_large")

    async def _stop_worker_locked(self, server_id: uuid.UUID) -> None:
        worker = self._workers.pop(server_id, None)
        if worker is None:
            return
        if worker.task.done():
            await asyncio.gather(worker.task, return_exceptions=True)
            return
        future = asyncio.get_running_loop().create_future()
        await worker.queue.put(UpstreamCommand("close", 1, future))
        await future
        await asyncio.gather(worker.task, return_exceptions=True)

    async def invalidate_server(self, server_id: uuid.UUID) -> None:
        async with self._operation_lock(server_id):
            await self._stop_worker_locked(server_id)
            self._circuits.pop(server_id, None)

    def schedule_invalidation(self, server_id: uuid.UUID) -> None:
        task = asyncio.create_task(self.invalidate_server(server_id))
        self._invalidation_tasks.add(task)
        task.add_done_callback(self._invalidation_tasks.discard)

    async def close(self) -> None:
        if self._invalidation_tasks:
            await asyncio.gather(*self._invalidation_tasks, return_exceptions=True)
        await asyncio.gather(
            *(self.invalidate_server(server_id) for server_id in list(self._workers))
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
        command = None
        try:
            async with asyncio.timeout(limit):
                self._before_request(config)
                command = await self._enqueue_locked(config, "list_tools", limit)
                return await asyncio.shield(command.future)
        except UpstreamBusyError:
            self._release_half_open(config)
            raise
        except TimeoutError as exc:
            raise UpstreamTimeoutError(command.dispatched if command else False) from exc
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
        command = None
        acquired = False
        try:
            async with asyncio.timeout(timeout):
                await lock.acquire()
                acquired = True
                self._before_request(server)
                command = await self._enqueue_locked(
                    server, "call_tool", timeout, original_name, arguments
                )
                return await asyncio.shield(command.future)
        except UpstreamBusyError:
            self._release_half_open(server)
            raise
        except TimeoutError as exc:
            # A timeout while waiting for a session or initializing is known not to have called the tool.
            # Once call_tool has begun, the upstream may have committed a write without returning a reply.
            raise UpstreamTimeoutError(command.dispatched if command else False) from exc
        finally:
            if acquired:
                lock.release()


def _consume_future_exception(future: asyncio.Future) -> None:
    if not future.cancelled():
        future.exception()


async def _close_stack(stack: AsyncExitStack) -> None:
    try:
        await stack.aclose()
    except Exception as exc:  # noqa: BLE001 - cleanup errors are contained after a failed request.
        logger.warning("Failed to close an upstream MCP session (%s)", type(exc).__name__)


def _operation_error(exc: BaseException, dispatched: bool) -> BaseException:
    if isinstance(exc, UpstreamTimeoutError):
        return exc
    causes = list(_nested_exceptions(exc))
    if any(isinstance(cause, (TimeoutError, httpx.TimeoutException)) for cause in causes):
        return UpstreamTimeoutError(dispatched)
    for cause in causes:
        if isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code in {401, 403}:
            return UpstreamCallError(False, "upstream_auth_failed")
    if dispatched:
        return UpstreamCallError()
    return exc


def _nested_exceptions(exc: BaseException):
    if isinstance(exc, BaseExceptionGroup):
        for nested in exc.exceptions:
            yield from _nested_exceptions(nested)
    else:
        yield exc


def _transport_error(exc: BaseException) -> str | None:
    causes = list(_nested_exceptions(exc))
    if any(isinstance(cause, (TimeoutError, httpx.TimeoutException)) for cause in causes):
        return "upstream_timeout"
    if any(isinstance(cause, httpx.ConnectError) for cause in causes):
        return "upstream_unreachable"
    for cause in causes:
        if isinstance(cause, httpx.HTTPStatusError):
            if cause.response.status_code in {401, 403}:
                return "upstream_auth_failed"
            return "upstream_http_error"
    return None


def server_timeout(server: UpstreamServer, settings: Settings) -> float:
    # Per-server overrides are added through the API in the next control-plane iteration.
    return settings.tool_timeout_seconds


def safe_error(exc: Exception) -> str:
    return _transport_error(exc) or "upstream_protocol_error"


def result_size(result: CallToolResult) -> int:
    return len(result.model_dump_json().encode())

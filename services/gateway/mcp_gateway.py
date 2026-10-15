"""Downstream MCP endpoint and the single audited upstream tool dispatch path."""

import json
import time
import uuid

import jsonschema
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
from sqlalchemy import select
from starlette.requests import Request

from .models import DiscoveredTool, ToolAudit, UpstreamServer, now_utc
from .security import Principal, decrypt_credential
from .upstreams import (
    UpstreamBusyError,
    UpstreamCallError,
    UpstreamManager,
    UpstreamTimeoutError,
    result_size,
    server_config,
)


def _error(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=message)], isError=True)


def _principal(request: Request) -> Principal:
    principal = request.scope.get("state", {}).get("principal")
    if principal is None:
        raise RuntimeError("MCP request was not authenticated")
    return principal


def _wire_tool(row: DiscoveredTool) -> Tool:
    annotations = None
    if row.annotations:
        annotations = ToolAnnotations.model_validate(row.annotations)
    return Tool(
        name=row.public_name,
        title=row.public_name,
        description=row.description or "",
        inputSchema=row.input_schema,
        outputSchema=row.output_schema,
        annotations=annotations,
    )


def _redact(value, credential: str | None):
    if isinstance(value, str):
        return value.replace(credential, "[redacted]") if credential else value
    if isinstance(value, list):
        return [_redact(item, credential) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item, credential) for key, item in value.items()}
    return value


def build_mcp_server(sessions, settings, upstreams: UpstreamManager) -> FastMCP:
    mcp = FastMCP(
        "Urumi Team MCP Gateway",
        host="0.0.0.0",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.mcp_allowed_hosts,
            allowed_origins=settings.mcp_allowed_origins,
        ),
    )

    @mcp._mcp_server.list_tools()  # type: ignore[attr-defined]  # SDK low-level handler is the dynamic catalog boundary.
    async def list_tools() -> list[Tool]:
        principal = _principal(mcp.get_context().request_context.request)
        async with sessions() as session:
            rows = list(
                (
                    await session.execute(
                        select(DiscoveredTool, UpstreamServer)
                        .join(UpstreamServer, DiscoveredTool.server_id == UpstreamServer.id)
                        .where(
                            UpstreamServer.enabled.is_(True), UpstreamServer.deleted_at.is_(None)
                        )
                        .order_by(UpstreamServer.alias, DiscoveredTool.public_name)
                    )
                ).all()
            )
            if principal.role == "admin":
                return [_wire_tool(tool) for tool, _ in rows]
            return [
                _wire_tool(tool)
                for tool, server in rows
                if tool.original_name in set(server.member_tools or [])
            ]

    @mcp._mcp_server.call_tool(validate_input=False)  # type: ignore[attr-defined]
    async def call_tool(name: str, arguments: dict) -> CallToolResult:
        context = mcp.get_context().request_context
        request = context.request
        principal = _principal(request)
        started = time.monotonic()
        correlation_id = str(uuid.uuid4())
        args = arguments or {}
        if (
            not isinstance(args, dict)
            or len(json.dumps(args, separators=(",", ":")).encode()) > 32768
        ):
            await _record_rejection(sessions, principal, correlation_id, name, "invalid_arguments")
            return _error("Invalid tool arguments: request is too large or is not an object.")

        async with sessions() as session:
            selection = await session.execute(
                select(DiscoveredTool, UpstreamServer)
                .join(UpstreamServer, DiscoveredTool.server_id == UpstreamServer.id)
                .where(DiscoveredTool.public_name == name)
            )
            row = selection.first()
            if row is None:
                audit = ToolAudit(
                    correlation_id=correlation_id,
                    user_id=principal.user_id,
                    key_id=principal.key_id,
                    user_email=principal.email,
                    tool_name=name[:256],
                    outcome="denied",
                    error_category="unknown_tool",
                    duration_ms=0,
                    completed_at=now_utc(),
                )
                session.add(audit)
                await session.commit()
                return _error("This tool is not in the current gateway catalog.")
            tool, server = row
            allowed = principal.role == "admin" or tool.original_name in set(
                server.member_tools or []
            )
            if not allowed:
                await _write_audit(
                    session,
                    principal,
                    correlation_id,
                    server,
                    name,
                    "denied",
                    "tool_permission_denied",
                    int((time.monotonic() - started) * 1000),
                )
                return _error("Your role is not allowed to call this tool.")
            if not server.enabled or server.deleted_at is not None:
                await _write_audit(
                    session,
                    principal,
                    correlation_id,
                    server,
                    name,
                    "denied",
                    "server_disabled",
                    int((time.monotonic() - started) * 1000),
                )
                return _error("The upstream server is disabled.")
            if server.status == "unreachable":
                await _write_audit(
                    session,
                    principal,
                    correlation_id,
                    server,
                    name,
                    "failed",
                    "upstream_unreachable",
                    int((time.monotonic() - started) * 1000),
                )
                return _error("The upstream server is unreachable. Try again after it recovers.")
            try:
                jsonschema.validate(args, tool.input_schema)
            except jsonschema.ValidationError as exc:
                await _write_audit(
                    session,
                    principal,
                    correlation_id,
                    server,
                    name,
                    "denied",
                    "invalid_arguments",
                    int((time.monotonic() - started) * 1000),
                )
                return _error(f"Invalid tool arguments: {exc.message[:300]}")
            # Save the intent before any upstream traffic. If this commit fails, no tool executes.
            intent = ToolAudit(
                correlation_id=correlation_id,
                user_id=principal.user_id,
                key_id=principal.key_id,
                server_id=server.id,
                user_email=principal.email,
                server_alias=server.alias,
                tool_name=name,
                outcome="in_progress",
                started_at=now_utc(),
            )
            session.add(intent)
            await session.commit()
            server_id = server.id
            original_name = tool.original_name
            detached_server = server_config(server)

        dispatched = False
        try:
            result = await upstreams.call_tool(detached_server, original_name, args)
            dispatched = True
            async with sessions() as session:
                # A permission or disable change during a call cannot cancel an in-flight upstream write,
                # but the next call observes it. The dispatched request used the grant at intent time.
                payload = result.model_dump(mode="json", exclude_none=True)
                credential = None
                if detached_server.credential_ciphertext:
                    credential = decrypt_credential(detached_server.credential_ciphertext, settings)
                clean_result = CallToolResult.model_validate(_redact(payload, credential))
                if result_size(clean_result) > settings.max_tool_result_bytes:
                    clean_result = _error(
                        "Upstream result exceeded the gateway response size limit."
                    )
                    outcome, category = "failed", "result_too_large"
                elif clean_result.isError:
                    outcome, category = "failed", "upstream_tool_error"
                else:
                    outcome, category = "success", None
                audit = await session.scalar(
                    select(ToolAudit).where(ToolAudit.correlation_id == correlation_id)
                )
                if audit:
                    audit.outcome = outcome
                    audit.error_category = category
                    audit.duration_ms = int((time.monotonic() - started) * 1000)
                    audit.completed_at = now_utc()
                await session.commit()
                return clean_result
        except UpstreamTimeoutError as exc:
            if exc.outcome_unknown:
                outcome, category, response = (
                    "unknown_outcome",
                    "upstream_timeout",
                    _error(
                        "The upstream timed out after dispatch. A write may have completed; "
                        "the gateway did not retry it. Check the store before trying again."
                    ),
                )
            else:
                outcome, category, response = (
                    "failed",
                    "upstream_timeout",
                    _error("The upstream timed out before the tool was dispatched."),
                )
        except UpstreamCallError as exc:
            outcome, category = (
                ("unknown_outcome", exc.category)
                if exc.outcome_unknown
                else ("failed", exc.category)
            )
            response = _error(
                "The upstream rejected its configured credential. Check the server credential."
                if category == "upstream_auth_failed"
                else "The upstream could not be reached; the tool was not dispatched."
                if category == "upstream_unreachable"
                else "The connection ended after dispatch. The operation may have completed; "
                "check the upstream state before trying again."
            )
        except UpstreamBusyError:
            outcome, category = "failed", "upstream_busy"
            response = _error("This upstream is handling another request. Try again shortly.")
        except Exception as exc:  # noqa: BLE001 - contain unexpected MCP/DB failures at the protocol boundary.
            from .upstreams import safe_error

            if dispatched:
                category = "audit_finalization_error"
                outcome = "unknown_outcome"
                response = _error(
                    "The upstream replied but the gateway could not finalize its audit record. "
                    "Check the upstream state before retrying."
                )
            else:
                category = safe_error(exc)
                outcome = "failed"
                response = _error(
                    "The upstream call failed. Check server status and retry only if safe."
                )
        async with sessions() as session:
            audit = await session.scalar(
                select(ToolAudit).where(ToolAudit.correlation_id == correlation_id)
            )
            if audit:
                audit.outcome = outcome
                audit.error_category = category
                audit.duration_ms = int((time.monotonic() - started) * 1000)
                audit.completed_at = now_utc()
            server = await session.get(UpstreamServer, server_id)
            if server:
                server.last_check_at = now_utc()
                server.last_error = category
                if category != "upstream_busy":
                    server.failure_count += 1
                    if server.enabled and category != "audit_finalization_error":
                        server.status = "unreachable"
            await session.commit()
        return response

    return mcp


async def _write_audit(
    session,
    principal: Principal,
    correlation_id: str,
    server: UpstreamServer,
    tool_name: str,
    outcome: str,
    category: str,
    duration_ms: int,
) -> None:
    session.add(
        ToolAudit(
            correlation_id=correlation_id,
            user_id=principal.user_id,
            key_id=principal.key_id,
            server_id=server.id,
            user_email=principal.email,
            server_alias=server.alias,
            tool_name=tool_name[:256],
            outcome=outcome,
            error_category=category,
            duration_ms=duration_ms,
            completed_at=now_utc(),
        )
    )
    await session.commit()


async def _record_rejection(
    sessions, principal: Principal, correlation_id: str, name: str, category: str
):
    async with sessions() as session:
        session.add(
            ToolAudit(
                correlation_id=correlation_id,
                user_id=principal.user_id,
                key_id=principal.key_id,
                user_email=principal.email,
                tool_name=str(name)[:256],
                outcome="denied",
                error_category=category,
                duration_ms=0,
                completed_at=now_utc(),
            )
        )
        await session.commit()

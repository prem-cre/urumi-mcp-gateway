"""Dashboard API for workspace identity, MCP server registry and audit activity."""

import asyncio
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import ceil
from typing import Annotated, Literal

import httpx
import jwt
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from .chat import ChatNotConfiguredError, ChatObservation, ChatProviderError, run_chat_turn
from .config import Settings
from .db import session_dependency
from .models import (
    AgentInvestigation,
    AgentObservation,
    AgentTrace,
    ApiKey,
    DiscoveredTool,
    InvestigationFinding,
    InvestigationRun,
    Invitation,
    ToolAudit,
    UpstreamServer,
    User,
    WebSession,
    now_utc,
)
from .oidc import authorization_url, read_state_cookie, verified_email
from .rate_limit import consume_request
from .security import (
    Principal,
    create_api_key,
    digest_secret,
    encrypt_credential,
    hash_password,
    new_session_values,
    require_admin,
    require_session,
    validate_upstream_url,
    verify_csrf,
    verify_password,
)
from .trace_content import safe_trace_preview

SessionDep = Annotated[AsyncSession, Depends(session_dependency)]
SessionUser = Annotated[Principal, Depends(require_session)]
AdminUser = Annotated[Principal, Depends(require_admin)]


def _normalize_email(value: str) -> str:
    return validate_email(
        value,
        check_deliverability=False,
        test_environment=True,
    ).normalized


EmailAddress = Annotated[
    str,
    BeforeValidator(_normalize_email),
    StringConstraints(min_length=3, max_length=320),
]


class LoginBody(BaseModel):
    email: EmailAddress
    password: SecretStr


class KeyBody(BaseModel):
    label: str = Field(default="Personal key", min_length=1, max_length=100)


class InvitationBody(BaseModel):
    email: EmailAddress
    display_name: str = Field(min_length=1, max_length=120)
    role: Literal["member", "admin"] = "member"


class AcceptInvitationBody(BaseModel):
    token: str = Field(min_length=30, max_length=180)
    email: EmailAddress
    password: SecretStr

    @field_validator("password")
    @classmethod
    def enforce_password_length(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 12:
            raise ValueError("Password must be at least 12 characters")
        return value


class ServerCreateBody(BaseModel):
    alias: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    display_name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=240)
    url: str = Field(min_length=12, max_length=2048)
    auth_header: str | None = Field(default="Authorization", max_length=100)
    auth_prefix: str = Field(default="Bearer ", max_length=80)
    static_credential: SecretStr | None = None
    enabled: bool = False

    @field_validator("auth_header")
    @classmethod
    def validate_header(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", value):
            raise ValueError("Invalid credential header name")
        if value.lower() in {"host", "cookie", "forwarded", "x-forwarded-for", "content-length"}:
            raise ValueError("This header cannot carry an upstream credential")
        return value

    @field_validator("auth_prefix")
    @classmethod
    def validate_auth_prefix(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("Credential prefixes cannot contain newlines")
        return value


class ServerUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=240)
    url: str | None = Field(default=None, min_length=12, max_length=2048)
    auth_header: str | None = Field(default=None, max_length=100)
    auth_prefix: str | None = Field(default=None, max_length=80)
    static_credential: SecretStr | None = None
    enabled: bool | None = None
    member_tools: list[str] | None = Field(default=None, max_length=500)

    @field_validator("auth_header")
    @classmethod
    def validate_header(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", value):
            raise ValueError("Invalid credential header name")
        if value.lower() in {"host", "cookie", "forwarded", "x-forwarded-for", "content-length"}:
            raise ValueError("This header cannot carry an upstream credential")
        return value

    @field_validator("auth_prefix")
    @classmethod
    def validate_auth_prefix(cls, value: str | None) -> str | None:
        if value is not None and ("\r" in value or "\n" in value):
            raise ValueError("Credential prefixes cannot contain newlines")
        return value


class MemberToolGrantBody(BaseModel):
    tools: list[str] = Field(max_length=500)


class ChatHistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=10)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be blank")
        return value


class InvestigationCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=120)
    expected_behavior: str = Field(min_length=10, max_length=2000)
    checks: list[str] = Field(default_factory=list, max_length=10)
    window_hours: int = Field(default=24, ge=1, le=168)
    sample_limit: int = Field(default=25, ge=1, le=100)
    max_cost_usd: Decimal = Field(default=Decimal("0.50"), gt=0, le=5)

    @field_validator("name", "expected_behavior")
    @classmethod
    def trim_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        return value

    @field_validator("checks")
    @classmethod
    def normalize_checks(cls, value: list[str]) -> list[str]:
        normalized = [item.strip()[:300] for item in value if item.strip()]
        if len(normalized) != len(value):
            raise ValueError("Checks cannot be blank")
        return normalized


class FindingFeedbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved: bool = False
    feedback: str | None = Field(default=None, max_length=1000)


async def csrf_for(request: Request) -> None:
    session: WebSession | None = getattr(request.state, "web_session", None)
    if session is None:
        raw = request.cookies.get("urumi_session", "")
        if not raw:
            raise HTTPException(401, "Authentication required")
        settings: Settings = request.app.state.settings
        digest = digest_secret(raw, settings.session_pepper.get_secret_value())
        async with request.app.state.sessions() as db:
            session = await db.scalar(select(WebSession).where(WebSession.token_digest == digest))
    if session is None:
        raise HTTPException(401, "Session expired")
    verify_csrf(request, session, request.app.state.settings)


def _tool_is_allowed(server: UpstreamServer, tool: DiscoveredTool, principal: Principal) -> bool:
    if principal.role == "admin":
        return True
    return tool.original_name in set(server.member_tools or [])


def _server_view(server: UpstreamServer, tools: list[DiscoveredTool], principal: Principal) -> dict:
    visible = [tool for tool in tools if _tool_is_allowed(server, tool, principal)]
    if principal.role != "admin" and not server.enabled:
        visible = []
    result = {
        "id": str(server.id),
        "alias": server.alias,
        "name": server.display_name,
        "description": server.description,
        "url": server.url,
        "auth_header": server.auth_header,
        "auth_prefix": server.auth_prefix,
        "has_credential": server.credential_ciphertext is not None,
        "enabled": server.enabled,
        "status": "disabled" if not server.enabled else server.status,
        "last_check_at": server.last_check_at,
        "last_success_at": server.last_success_at,
        "last_error": server.last_error,
        "tools": [
            {
                "name": tool.original_name,
                "public_name": tool.public_name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "member_allowed": tool.original_name in set(server.member_tools or []),
                "discovered_at": tool.discovered_at,
            }
            for tool in visible
        ],
        "tool_count": len(visible),
    }
    return result


def create_api_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/auth/oidc/status")
    async def oidc_status(request: Request):
        return {"enabled": request.app.state.settings.oidc_enabled}

    @router.get("/auth/oidc/start")
    async def oidc_start(request: Request):
        settings: Settings = request.app.state.settings
        if not settings.oidc_enabled:
            raise HTTPException(404, "Single sign-on is not configured.")
        try:
            location, state_cookie = await authorization_url(settings)
        except Exception as exc:
            raise HTTPException(503, "The identity provider is unavailable.") from exc
        response = RedirectResponse(location, status_code=302)
        response.set_cookie(
            "urumi_oidc_state",
            state_cookie,
            httponly=True,
            secure=settings.session_cookie_secure,
            samesite="lax",
            max_age=600,
            path="/api/auth/oidc",
        )
        return response

    @router.get("/auth/oidc/callback")
    async def oidc_callback(
        request: Request,
        db: SessionDep,
        code: str = "",
        state: str = "",
        error: str = "",
    ):
        settings: Settings = request.app.state.settings
        state_value = request.cookies.get("urumi_oidc_state", "")
        state_payload = read_state_cookie(settings, state_value, state) if state else None
        redirect = RedirectResponse("/?auth_error=oidc", status_code=303)
        redirect.delete_cookie("urumi_oidc_state", path="/api/auth/oidc")
        if not settings.oidc_enabled or error or not code or not state_payload:
            return redirect
        try:
            email = await verified_email(settings, code, state_payload)
            normalized_email = _normalize_email(email) if email else None
        except (
            EmailNotValidError,
            httpx.HTTPError,
            jwt.PyJWTError,
            KeyError,
            TypeError,
            ValueError,
        ):
            return redirect
        if not normalized_email:
            return redirect
        user = await db.scalar(select(User).where(User.email == normalized_email))
        # OIDC authenticates invited accounts; it does not silently provision users.
        if user is None or not user.active:
            return redirect
        raw_token, csrf, expires = new_session_values(settings)
        db.add(
            WebSession(
                user_id=user.id,
                token_digest=digest_secret(raw_token, settings.session_pepper.get_secret_value()),
                csrf_digest=digest_secret(csrf, settings.session_pepper.get_secret_value()),
                expires_at=expires,
            )
        )
        await db.commit()
        success = RedirectResponse("/", status_code=303)
        success.delete_cookie("urumi_oidc_state", path="/api/auth/oidc")
        success.set_cookie(
            "urumi_session",
            raw_token,
            httponly=True,
            secure=settings.session_cookie_secure,
            samesite="strict",
            max_age=settings.session_ttl_hours * 3600,
            path="/",
        )
        success.set_cookie(
            "urumi_csrf",
            csrf,
            httponly=False,
            secure=settings.session_cookie_secure,
            samesite="strict",
            max_age=settings.session_ttl_hours * 3600,
            path="/",
        )
        return success

    @router.post("/auth/login")
    async def login(body: LoginBody, request: Request, response: Response, db: SessionDep):
        email = body.email.lower()
        user = await db.scalar(select(User).where(User.email == email))
        if (
            user is None
            or not user.active
            or not verify_password(body.password.get_secret_value(), user.password_hash)
        ):
            raise HTTPException(401, "Email or password is incorrect")
        settings: Settings = request.app.state.settings
        raw_token, csrf, expires = new_session_values(settings)
        web_session = WebSession(
            user_id=user.id,
            token_digest=digest_secret(raw_token, settings.session_pepper.get_secret_value()),
            csrf_digest=digest_secret(csrf, settings.session_pepper.get_secret_value()),
            expires_at=expires,
        )
        db.add(web_session)
        await db.commit()
        response.set_cookie(
            "urumi_session",
            raw_token,
            httponly=True,
            secure=settings.session_cookie_secure,
            samesite="strict",
            max_age=settings.session_ttl_hours * 3600,
            path="/",
        )
        response.set_cookie(
            "urumi_csrf",
            csrf,
            httponly=False,
            secure=settings.session_cookie_secure,
            samesite="strict",
            max_age=settings.session_ttl_hours * 3600,
            path="/",
        )
        return {
            "user": {
                "id": str(user.id),
                "email": user.email,
                "name": user.display_name,
                "role": user.role,
            },
            "csrf_token": csrf,
        }

    @router.get("/auth/me")
    async def me(request: Request, principal: SessionUser):
        return {
            "user": {
                "id": str(principal.user_id),
                "email": principal.email,
                "name": principal.display_name,
                "role": principal.role,
            },
            "csrf_token": request.cookies.get("urumi_csrf", ""),
            "mcp_url": f"{str(request.base_url).rstrip('/')}/mcp",
        }

    @router.post("/auth/logout")
    async def logout(request: Request, response: Response, principal: SessionUser):
        await csrf_for(request)
        session_record: WebSession = request.state.web_session
        async with request.app.state.sessions() as db:
            record = await db.get(WebSession, session_record.id)
            if record:
                await db.delete(record)
                await db.commit()
        response.delete_cookie("urumi_session", path="/")
        response.delete_cookie("urumi_csrf", path="/")
        return {"ok": True}

    @router.get("/overview")
    async def overview(
        request: Request,
        db: SessionDep,
        principal: SessionUser,
    ):
        servers = list(
            (
                await db.scalars(select(UpstreamServer).where(UpstreamServer.deleted_at.is_(None)))
            ).all()
        )
        server_ids = [server.id for server in servers if server.enabled]
        tool_rows = list(
            (
                await db.scalars(
                    select(DiscoveredTool).where(DiscoveredTool.server_id.in_(server_ids))
                    if server_ids
                    else select(DiscoveredTool).where(False)
                )
            ).all()
        )
        tool_count = sum(
            1
            for tool in tool_rows
            if any(s.id == tool.server_id and _tool_is_allowed(s, tool, principal) for s in servers)
        )
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        audit_query = select(ToolAudit).where(ToolAudit.started_at >= today)
        if principal.role != "admin":
            audit_query = audit_query.where(ToolAudit.user_id == principal.user_id)
        audits = list((await db.scalars(audit_query)).all())
        member_count = await db.scalar(select(func.count(User.id)).where(User.active.is_(True)))
        connected = sum(1 for server in servers if server.enabled and server.status == "connected")
        return {
            "connected_servers": connected,
            "enabled_servers": sum(server.enabled for server in servers),
            "servers": len(servers),
            "tools": tool_count,
            "calls_today": len(audits),
            "members": member_count or 0,
            "success_rate": round(
                100 * sum(row.outcome == "success" for row in audits) / len(audits), 1
            )
            if audits
            else None,
            "average_duration_ms": round(
                sum(row.duration_ms or 0 for row in audits if row.duration_ms is not None)
                / max(1, sum(row.duration_ms is not None for row in audits))
            )
            if audits
            else None,
            "store_url": request.app.state.settings.store_url,
        }

    @router.post("/chat")
    async def chat(
        body: ChatBody,
        request: Request,
        response: Response,
        principal: SessionUser,
        db: SessionDep,
    ):
        await csrf_for(request)
        from .security import create_chat_assertion

        settings: Settings = request.app.state.settings
        allowed, remaining, reset_seconds = await consume_request(
            request.app.state.sessions, principal, settings.api_rpm_limit
        )
        if not allowed:
            db.add(
                ToolAudit(
                    correlation_id=str(uuid.uuid4()),
                    user_id=principal.user_id,
                    user_email=principal.email,
                    outcome="denied",
                    error_category="rate_limited",
                    duration_ms=0,
                    completed_at=now_utc(),
                )
            )
            await db.commit()
            raise HTTPException(
                429,
                "Gateway request rate limit exceeded.",
                headers={
                    "RateLimit-Limit": str(settings.api_rpm_limit),
                    "RateLimit-Remaining": "0",
                    "RateLimit-Reset": str(reset_seconds),
                },
            )
        response.headers["RateLimit-Limit"] = str(settings.api_rpm_limit)
        response.headers["RateLimit-Remaining"] = str(remaining)
        response.headers["RateLimit-Reset"] = str(reset_seconds)
        assertion = create_chat_assertion(principal, settings)
        trace = AgentTrace(
            user_id=principal.user_id,
            user_email=principal.email,
            input_preview=(
                safe_trace_preview(body.message) if settings.trace_content_capture_enabled else None
            ),
            content_captured=settings.trace_content_capture_enabled,
            outcome="in_progress",
            pricing_status="pending",
        )
        db.add(trace)
        await db.commit()
        observation_sequence = 0

        async def write_observation(event: ChatObservation) -> None:
            nonlocal observation_sequence
            observation_sequence += 1
            async with request.app.state.sessions() as telemetry_db:
                telemetry_db.add(
                    AgentObservation(
                        trace_id=trace.id,
                        sequence=observation_sequence,
                        kind=event.kind,
                        name=event.name[:256],
                        server_alias=event.server_alias,
                        status=event.status,
                        model=event.model[:160] if event.model else None,
                        provider=event.provider[:120] if event.provider else None,
                        route_tier=event.route_tier,
                        route_reason=event.route_reason[:240] if event.route_reason else None,
                        input_tokens=event.input_tokens,
                        output_tokens=event.output_tokens,
                        total_tokens=event.total_tokens,
                        input_cost_usd=event.input_cost_usd,
                        output_cost_usd=event.output_cost_usd,
                        started_at=event.started_at,
                        completed_at=event.completed_at,
                        duration_ms=event.duration_ms,
                        error_category=event.error_category,
                        input_preview=event.input_preview,
                        output_preview=event.output_preview,
                    )
                )
                await telemetry_db.commit()

        async def finish_trace(
            *,
            outcome: str,
            error_category: str | None = None,
            route_tier: str | None = None,
            route_reason: str | None = None,
            selected_model: str | None = None,
            provider: str | None = None,
            output_preview: str | None = None,
        ) -> None:
            observations = list(
                (
                    await db.scalars(
                        select(AgentObservation)
                        .where(AgentObservation.trace_id == trace.id)
                        .order_by(AgentObservation.sequence)
                    )
                ).all()
            )
            generations = [item for item in observations if item.kind == "generation"]
            last_generation = generations[-1] if generations else None
            token_rows = [item for item in generations if item.total_tokens is not None]
            priced_rows = [
                item
                for item in generations
                if item.input_cost_usd is not None and item.output_cost_usd is not None
            ]
            known_cost = sum(
                (Decimal(item.input_cost_usd or 0) + Decimal(item.output_cost_usd or 0))
                for item in priced_rows
            )
            trace.outcome = outcome
            trace.error_category = error_category
            trace.route_tier = route_tier or (
                last_generation.route_tier if last_generation else None
            )
            resolved_reason = route_reason or (
                last_generation.route_reason if last_generation else None
            )
            resolved_model = selected_model or (last_generation.model if last_generation else None)
            resolved_provider = provider or (last_generation.provider if last_generation else None)
            trace.route_reason = resolved_reason[:240] if resolved_reason else None
            trace.selected_model = resolved_model[:160] if resolved_model else None
            trace.provider = resolved_provider[:120] if resolved_provider else None
            trace.output_preview = (
                safe_trace_preview(output_preview)
                if settings.trace_content_capture_enabled and output_preview is not None
                else None
            )
            trace.input_tokens = (
                sum(item.input_tokens or 0 for item in token_rows) if token_rows else None
            )
            trace.output_tokens = (
                sum(item.output_tokens or 0 for item in token_rows) if token_rows else None
            )
            trace.total_tokens = (
                sum(item.total_tokens or 0 for item in token_rows) if token_rows else None
            )
            trace.estimated_cost_usd = known_cost if priced_rows else None
            trace.pricing_status = (
                "not_priced"
                if not generations or not priced_rows
                else "priced"
                if len(priced_rows) == len(generations)
                else "partial"
            )
            trace.completed_at = datetime.now(UTC)
            trace.duration_ms = int((trace.completed_at - trace.started_at).total_seconds() * 1000)
            await db.commit()

        try:
            async with asyncio.timeout(settings.chat_turn_timeout_seconds):
                turn = await run_chat_turn(
                    body.message,
                    settings,
                    assertion,
                    [item.model_dump() for item in body.history],
                    observation_writer=write_observation,
                )
        except ChatNotConfiguredError as exc:
            await finish_trace(outcome="failed", error_category="model_not_configured")
            raise HTTPException(503, str(exc)) from exc
        except TimeoutError as exc:
            await finish_trace(outcome="failed", error_category="chat_timeout")
            raise HTTPException(504, "The chat request exceeded its time limit.") from exc
        except ChatProviderError as exc:
            await finish_trace(outcome="failed", error_category="model_or_tool_error")
            raise HTTPException(502, str(exc)) from exc
        await finish_trace(
            outcome=(
                "partial"
                if any(item.outcome not in {"success"} for item in turn.tools_called)
                else "success"
            ),
            route_tier=turn.route_tier,
            route_reason=turn.route_reason,
            selected_model=turn.selected_model,
            provider=turn.provider,
            output_preview=turn.response,
        )
        return {
            "trace_id": str(trace.id),
            "response": turn.response,
            "route": {
                "tier": turn.route_tier,
                "reason": turn.route_reason,
                "model": turn.selected_model,
                "provider": turn.provider,
            },
            "tools_called": [
                {"name": item.name, "server": item.server, "outcome": item.outcome}
                for item in turn.tools_called
            ],
        }

    @router.get("/chat/status")
    async def chat_status(request: Request, principal: SessionUser):
        settings: Settings = request.app.state.settings
        return {
            "configured": bool(
                settings.llm_base_url.strip()
                and any(
                    (
                        settings.llm_model.strip(),
                        settings.llm_router_model.strip(),
                        settings.llm_standard_model.strip(),
                        settings.llm_complex_model.strip(),
                    )
                )
            ),
            "model": settings.llm_model if settings.llm_base_url.strip() else None,
            "router_model": settings.llm_router_model or None,
            "standard_model": settings.llm_standard_model or settings.llm_model or None,
            "complex_model": settings.llm_complex_model or settings.llm_model or None,
            "provider": settings.llm_provider_name,
            "routing_enabled": bool(
                settings.llm_standard_model.strip()
                or settings.llm_complex_model.strip()
                or settings.llm_router_model.strip()
            ),
            "trace_content_capture": {
                "enabled": settings.trace_content_capture_enabled,
                "retention_days": settings.trace_content_retention_days,
            },
            "investigations_enabled": bool(
                settings.trace_content_capture_enabled
                and settings.llm_base_url.strip()
                and settings.llm_model_pricing.get(
                    settings.llm_analysis_model.strip()
                    or settings.llm_complex_model.strip()
                    or settings.llm_model.strip()
                )
            ),
        }

    @router.get("/servers")
    async def list_servers(request: Request, db: SessionDep, principal: SessionUser):
        servers = list(
            (
                await db.scalars(
                    select(UpstreamServer)
                    .where(UpstreamServer.deleted_at.is_(None))
                    .order_by(UpstreamServer.created_at)
                )
            ).all()
        )
        response = []
        for server in servers:
            tools = list(
                (
                    await db.scalars(
                        select(DiscoveredTool).where(DiscoveredTool.server_id == server.id)
                    )
                ).all()
            )
            response.append(_server_view(server, tools, principal))
        return response

    @router.post("/servers", status_code=201)
    async def add_server(
        body: ServerCreateBody,
        request: Request,
        db: SessionDep,
        principal: AdminUser,
    ):
        await csrf_for(request)
        url = await asyncio.to_thread(validate_upstream_url, body.url, request.app.state.settings)
        if await db.scalar(select(UpstreamServer.id).where(UpstreamServer.alias == body.alias)):
            raise HTTPException(409, "That server alias is already in use")
        credential = body.static_credential.get_secret_value() if body.static_credential else None
        server = UpstreamServer(
            alias=body.alias,
            display_name=body.display_name.strip(),
            description=body.description.strip(),
            url=url,
            auth_header=body.auth_header,
            auth_prefix=body.auth_prefix,
            credential_ciphertext=(
                encrypt_credential(credential, request.app.state.settings) if credential else None
            ),
            enabled=body.enabled,
            status="unreachable" if body.enabled else "disabled",
            member_tools=[],
        )
        db.add(server)
        await db.commit()
        await request.app.state.upstreams.refresh_one(server.id)
        await db.refresh(server)
        return await _load_server_view(db, server.id, principal)

    @router.patch("/servers/{server_id}")
    async def update_server(
        server_id: uuid.UUID,
        body: ServerUpdateBody,
        request: Request,
        db: SessionDep,
        principal: AdminUser,
    ):
        await csrf_for(request)
        server = await db.get(UpstreamServer, server_id)
        if server is None or server.deleted_at is not None:
            raise HTTPException(404, "MCP server not found")
        update = body.model_dump(exclude_unset=True, exclude={"static_credential"})
        if "url" in update and update["url"] is not None:
            update["url"] = await asyncio.to_thread(
                validate_upstream_url, update["url"], request.app.state.settings
            )
        if "member_tools" in update and update["member_tools"] is not None:
            current = set(
                (
                    await db.scalars(
                        select(DiscoveredTool.original_name).where(
                            DiscoveredTool.server_id == server.id
                        )
                    )
                ).all()
            )
            unknown = set(update["member_tools"]) - current
            if unknown:
                raise HTTPException(422, "Member grants may reference only discovered tools")
        connection_changed = (
            any(
                key in update and value is not None
                for key, value in update.items()
                if key in {"url", "auth_header", "auth_prefix"}
            )
            or body.static_credential is not None
        )
        enablement_changed = body.enabled is not None and body.enabled != server.enabled
        for key, value in update.items():
            if value is not None:
                setattr(server, key, value)
        if body.static_credential is not None:
            raw = body.static_credential.get_secret_value()
            server.credential_ciphertext = (
                encrypt_credential(raw, request.app.state.settings) if raw else None
            )
        server.updated_at = datetime.now(UTC)
        if connection_changed or enablement_changed:
            server.revision += 1
        if body.enabled is False:
            server.status = "disabled"
        elif body.enabled is True and server.status == "disabled":
            server.status = "unreachable"
        await db.commit()
        if connection_changed or enablement_changed:
            request.app.state.upstreams.schedule_invalidation(server.id)
        if connection_changed or body.enabled is True:
            await request.app.state.upstreams.refresh_one(server.id)
            await db.refresh(server)
        return await _load_server_view(db, server.id, principal)

    @router.delete("/servers/{server_id}")
    async def delete_server(
        server_id: uuid.UUID,
        request: Request,
        db: SessionDep,
        principal: AdminUser,
    ):
        await csrf_for(request)
        server = await db.get(UpstreamServer, server_id)
        if server is None or server.deleted_at is not None:
            raise HTTPException(404, "MCP server not found")
        server.enabled = False
        server.status = "disabled"
        server.deleted_at = datetime.now(UTC)
        server.revision += 1
        await db.commit()
        request.app.state.upstreams.schedule_invalidation(server.id)
        return {"ok": True}

    @router.post("/servers/{server_id}/refresh")
    async def refresh_server(
        server_id: uuid.UUID,
        request: Request,
        db: SessionDep,
        principal: AdminUser,
    ):
        await csrf_for(request)
        server = await db.get(UpstreamServer, server_id)
        if server is None or server.deleted_at is not None:
            raise HTTPException(404, "MCP server not found")
        ok, reason = await request.app.state.upstreams.refresh_one(server.id)
        await db.refresh(server)
        result = await _load_server_view(db, server.id, principal)
        result["refresh_ok"] = ok
        result["refresh_error"] = reason
        return result

    @router.get("/members")
    async def list_members(db: SessionDep, principal: SessionUser):
        query = select(User).order_by(User.created_at)
        if principal.role != "admin":
            query = query.where(User.id == principal.user_id)
        users = list((await db.scalars(query)).all())
        response = []
        for user in users:
            key_count = await db.scalar(
                select(func.count(ApiKey.id)).where(
                    ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None)
                )
            )
            response.append(
                {
                    "id": str(user.id),
                    "name": user.display_name,
                    "email": user.email,
                    "role": user.role,
                    "active": user.active,
                    "key_status": "Active" if key_count else "No active key",
                    "active_keys": key_count or 0,
                }
            )
        return response

    @router.post("/members/invitations", status_code=201)
    async def invite_member(
        body: InvitationBody,
        request: Request,
        db: SessionDep,
        principal: AdminUser,
    ):
        await csrf_for(request)
        email = body.email.lower()
        if await db.scalar(select(User.id).where(User.email == email)):
            raise HTTPException(409, "A member with that email already exists")
        token = secrets.token_urlsafe(40)
        invitation = Invitation(
            email=email,
            display_name=body.display_name.strip(),
            role=body.role,
            token_digest=digest_secret(
                token, request.app.state.settings.session_pepper.get_secret_value()
            ),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            created_by=principal.user_id,
        )
        db.add(invitation)
        await db.commit()
        # This token is shown once; email delivery is intentionally not configured in the assessment.
        return {
            "id": str(invitation.id),
            "email": invitation.email,
            "role": invitation.role,
            "invite_token": token,
        }

    @router.post("/invitations/accept", status_code=201)
    async def accept_invitation(body: AcceptInvitationBody, request: Request, db: SessionDep):
        digest = digest_secret(
            body.token, request.app.state.settings.session_pepper.get_secret_value()
        )
        invitation = await db.scalar(select(Invitation).where(Invitation.token_digest == digest))
        if (
            invitation is None
            or invitation.accepted_at is not None
            or invitation.expires_at <= datetime.now(UTC)
            or invitation.email != body.email.lower()
        ):
            raise HTTPException(400, "Invitation is invalid, expired or for another email")
        user = User(
            email=invitation.email,
            display_name=invitation.display_name,
            role=invitation.role,
            password_hash=hash_password(body.password.get_secret_value()),
        )
        invitation.accepted_at = datetime.now(UTC)
        db.add(user)
        await db.commit()
        return {"id": str(user.id), "email": user.email, "role": user.role}

    @router.post("/keys", status_code=201)
    async def create_key(
        body: KeyBody,
        request: Request,
        db: SessionDep,
        principal: SessionUser,
    ):
        await csrf_for(request)
        record, raw_key = create_api_key(
            principal.user_id,
            body.label.strip(),
            request.app.state.settings.api_key_pepper.get_secret_value(),
        )
        db.add(record)
        await db.commit()
        return {
            "id": record.id,
            "label": record.label,
            "key": raw_key,
            "created_at": record.created_at,
        }

    @router.get("/keys")
    async def list_keys(request: Request, db: SessionDep, principal: SessionUser):
        user_id = request.query_params.get("user_id")
        if user_id and principal.role != "admin":
            raise HTTPException(403, "Only administrators can view another member's keys")
        try:
            target_id = uuid.UUID(user_id) if user_id else principal.user_id
        except ValueError as exc:
            raise HTTPException(422, "Invalid member id") from exc
        records = list(
            (
                await db.scalars(
                    select(ApiKey)
                    .where(ApiKey.user_id == target_id)
                    .order_by(ApiKey.created_at.desc())
                )
            ).all()
        )
        return [
            {
                "id": record.id,
                "label": record.label,
                "created_at": record.created_at,
                "last_used_at": record.last_used_at,
                "revoked_at": record.revoked_at,
                "status": "Revoked" if record.revoked_at else "Active",
            }
            for record in records
        ]

    @router.delete("/keys/{key_id}")
    async def revoke_key(
        key_id: str,
        request: Request,
        db: SessionDep,
        principal: SessionUser,
    ):
        await csrf_for(request)
        record = await db.get(ApiKey, key_id)
        if record is None:
            raise HTTPException(404, "API key not found")
        if record.user_id != principal.user_id and principal.role != "admin":
            raise HTTPException(403, "You can revoke only your own keys")
        if record.revoked_at is None:
            record.revoked_at = datetime.now(UTC)
            await db.commit()
        return {"ok": True, "status": "Revoked"}

    @router.get("/observability/summary")
    async def observability_summary(db: SessionDep, principal: SessionUser, days: int = 7):
        days = min(max(days, 1), 30)
        since = datetime.now(UTC) - timedelta(days=days)
        traces_query = select(AgentTrace).where(AgentTrace.started_at >= since)
        audits_query = select(ToolAudit).where(ToolAudit.started_at >= since)
        if principal.role != "admin":
            traces_query = traces_query.where(AgentTrace.user_id == principal.user_id)
            audits_query = audits_query.where(ToolAudit.user_id == principal.user_id)
        traces = list((await db.scalars(traces_query)).all())
        audits = list((await db.scalars(audits_query)).all())
        trace_ids = [trace.id for trace in traces]
        observations = (
            list(
                (
                    await db.scalars(
                        select(AgentObservation)
                        .where(AgentObservation.trace_id.in_(trace_ids))
                        .order_by(AgentObservation.started_at)
                    )
                ).all()
            )
            if trace_ids
            else []
        )
        generations = [item for item in observations if item.kind == "generation"]
        generation_durations = sorted(
            item.duration_ms for item in generations if item.duration_ms is not None
        )
        tool_durations = sorted(item.duration_ms for item in audits if item.duration_ms is not None)
        by_model: dict[str, dict] = {}
        for item in generations:
            model = item.model or "Unknown model"
            group = by_model.setdefault(
                model,
                {
                    "model": model,
                    "calls": 0,
                    "failed_calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "known_cost": Decimal(0),
                    "unpriced_calls": 0,
                    "durations": [],
                },
            )
            group["calls"] += 1
            group["failed_calls"] += item.status != "success"
            group["input_tokens"] += item.input_tokens or 0
            group["output_tokens"] += item.output_tokens or 0
            group["durations"].append(item.duration_ms or 0)
            if item.input_cost_usd is None or item.output_cost_usd is None:
                group["unpriced_calls"] += 1
            else:
                group["known_cost"] += Decimal(item.input_cost_usd) + Decimal(item.output_cost_usd)
        models = [
            {
                "model": group["model"],
                "calls": group["calls"],
                "failed_calls": group["failed_calls"],
                "input_tokens": group["input_tokens"],
                "output_tokens": group["output_tokens"],
                "known_cost_usd": float(group["known_cost"]),
                "unpriced_calls": group["unpriced_calls"],
                "average_latency_ms": round(sum(group["durations"]) / len(group["durations"]))
                if group["durations"]
                else None,
            }
            for group in sorted(by_model.values(), key=lambda row: row["known_cost"], reverse=True)
        ]
        by_server: dict[str, dict] = {}
        for audit in audits:
            server = audit.server_alias or "Gateway"
            group = by_server.setdefault(
                server, {"server": server, "calls": 0, "failed_calls": 0, "durations": []}
            )
            group["calls"] += 1
            group["failed_calls"] += audit.outcome != "success"
            if audit.duration_ms is not None:
                group["durations"].append(audit.duration_ms)
        servers = [
            {
                "server": group["server"],
                "calls": group["calls"],
                "failed_calls": group["failed_calls"],
                "average_latency_ms": round(sum(group["durations"]) / len(group["durations"]))
                if group["durations"]
                else None,
            }
            for group in sorted(by_server.values(), key=lambda row: row["calls"], reverse=True)
        ]
        priced_traces = [trace for trace in traces if trace.estimated_cost_usd is not None]
        return {
            "days": days,
            "chat_turns": len(traces),
            "failed_chat_turns": sum(trace.outcome != "success" for trace in traces),
            "model_calls": len(generations),
            "failed_model_calls": sum(item.status != "success" for item in generations),
            "tool_calls": len(audits),
            "failed_tool_calls": sum(item.outcome != "success" for item in audits),
            "input_tokens": sum(item.input_tokens or 0 for item in generations),
            "output_tokens": sum(item.output_tokens or 0 for item in generations),
            "total_tokens": sum(item.total_tokens or 0 for item in generations),
            "known_cost_usd": float(
                sum(
                    (Decimal(trace.estimated_cost_usd or 0) for trace in priced_traces),
                    Decimal(0),
                )
            ),
            "unpriced_model_calls": sum(
                item.input_cost_usd is None or item.output_cost_usd is None for item in generations
            ),
            "p95_model_latency_ms": _p95(generation_durations),
            "p95_tool_latency_ms": _p95(tool_durations),
            "models": models,
            "servers": servers,
        }

    @router.get("/observability/traces")
    async def observability_traces(
        db: SessionDep, principal: SessionUser, days: int = 7, limit: int = 50
    ):
        days = min(max(days, 1), 30)
        limit = min(max(limit, 1), 100)
        query = (
            select(AgentTrace)
            .where(AgentTrace.started_at >= datetime.now(UTC) - timedelta(days=days))
            .order_by(AgentTrace.started_at.desc())
            .limit(limit)
        )
        if principal.role != "admin":
            query = query.where(AgentTrace.user_id == principal.user_id)
        return [_trace_view(row) for row in (await db.scalars(query)).all()]

    @router.get("/observability/traces/{trace_id}")
    async def observability_trace_detail(
        trace_id: uuid.UUID, request: Request, db: SessionDep, principal: SessionUser
    ):
        trace = await db.get(AgentTrace, trace_id)
        if trace is None or (principal.role != "admin" and trace.user_id != principal.user_id):
            raise HTTPException(404, "Trace not found")
        observations = list(
            (
                await db.scalars(
                    select(AgentObservation)
                    .where(AgentObservation.trace_id == trace.id)
                    .order_by(AgentObservation.sequence)
                )
            ).all()
        )
        return {
            **_trace_view(trace),
            "input_preview": trace.input_preview if trace.content_captured else None,
            "output_preview": trace.output_preview if trace.content_captured else None,
            "observations": [
                {
                    "id": str(item.id),
                    "sequence": item.sequence,
                    "kind": item.kind,
                    "name": item.name,
                    "server": item.server_alias,
                    "status": item.status,
                    "model": item.model,
                    "provider": item.provider,
                    "route_tier": item.route_tier,
                    "route_reason": item.route_reason,
                    "input_tokens": item.input_tokens,
                    "output_tokens": item.output_tokens,
                    "total_tokens": item.total_tokens,
                    "input_cost_usd": float(item.input_cost_usd)
                    if item.input_cost_usd is not None
                    else None,
                    "output_cost_usd": float(item.output_cost_usd)
                    if item.output_cost_usd is not None
                    else None,
                    "started_at": item.started_at,
                    "duration_ms": item.duration_ms,
                    "error_category": item.error_category,
                    "input_preview": item.input_preview if trace.content_captured else None,
                    "output_preview": item.output_preview if trace.content_captured else None,
                }
                for item in observations
            ],
            "privacy": (
                f"Redacted trace previews are captured for up to {request.app.state.settings.trace_content_retention_days} days. "
                "Regex and field-name redaction is best effort; hidden model reasoning and system prompts are never stored."
                if trace.content_captured
                else "Trace content was not captured by workspace policy; only metrics and step metadata are stored."
            ),
        }

    @router.get("/investigations")
    async def list_investigations(db: SessionDep, principal: AdminUser):
        rows = list(
            (
                await db.scalars(
                    select(AgentInvestigation)
                    .order_by(AgentInvestigation.updated_at.desc())
                    .limit(100)
                )
            ).all()
        )
        result = []
        for row in rows:
            latest = await db.scalar(
                select(InvestigationRun)
                .where(InvestigationRun.investigation_id == row.id)
                .order_by(InvestigationRun.created_at.desc())
                .limit(1)
            )
            result.append(
                {**_investigation_view(row), "latest_run": _run_view(latest) if latest else None}
            )
        return result

    @router.post("/investigations", status_code=201)
    async def create_investigation(
        body: InvestigationCreateBody,
        request: Request,
        db: SessionDep,
        principal: AdminUser,
    ):
        await csrf_for(request)
        settings: Settings = request.app.state.settings
        if not settings.trace_content_capture_enabled:
            raise HTTPException(
                409,
                "Enable redacted trace content capture in Helm values before creating investigations.",
            )
        if body.sample_limit > settings.analysis_max_traces:
            raise HTTPException(
                422,
                f"The configured analysis limit is {settings.analysis_max_traces} traces per run.",
            )
        row = AgentInvestigation(
            name=body.name,
            expected_behavior=body.expected_behavior,
            checks=body.checks,
            window_hours=body.window_hours,
            sample_limit=body.sample_limit,
            max_cost_usd=body.max_cost_usd,
            created_by=principal.user_id,
        )
        db.add(row)
        await db.commit()
        return _investigation_view(row)

    @router.get("/investigations/{investigation_id}")
    async def get_investigation(investigation_id: uuid.UUID, db: SessionDep, principal: AdminUser):
        investigation = await db.get(AgentInvestigation, investigation_id)
        if investigation is None:
            raise HTTPException(404, "Investigation not found")
        runs = list(
            (
                await db.scalars(
                    select(InvestigationRun)
                    .where(InvestigationRun.investigation_id == investigation.id)
                    .order_by(InvestigationRun.created_at.desc())
                    .limit(20)
                )
            ).all()
        )
        return {**_investigation_view(investigation), "runs": [_run_view(item) for item in runs]}

    @router.post("/investigations/{investigation_id}/runs", status_code=202)
    async def run_investigation(
        investigation_id: uuid.UUID,
        request: Request,
        db: SessionDep,
        principal: AdminUser,
    ):
        await csrf_for(request)
        settings: Settings = request.app.state.settings
        if not settings.trace_content_capture_enabled:
            raise HTTPException(409, "Trace content capture is disabled by workspace policy.")
        if not settings.llm_base_url.strip():
            raise HTTPException(503, "Configure an analysis model provider before starting a run.")
        analysis_model_name = (
            settings.llm_analysis_model.strip()
            or settings.llm_complex_model.strip()
            or settings.llm_model.strip()
        )
        prices = settings.llm_model_pricing.get(analysis_model_name)
        if not prices:
            raise HTTPException(
                503, "Configure explicit model input and output prices before starting a run."
            )
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", idempotency_key):
            raise HTTPException(400, "Provide an Idempotency-Key header of 8 to 64 characters.")
        investigation = await db.get(AgentInvestigation, investigation_id)
        if investigation is None:
            raise HTTPException(404, "Investigation not found")
        existing = await db.scalar(
            select(InvestigationRun).where(
                InvestigationRun.investigation_id == investigation.id,
                InvestigationRun.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return _run_view(existing)
        active = await db.scalar(
            select(InvestigationRun.id)
            .where(
                InvestigationRun.investigation_id == investigation.id,
                InvestigationRun.status.in_(("queued", "running")),
            )
            .limit(1)
        )
        if active is not None:
            raise HTTPException(409, "This investigation already has a queued or running job.")
        pending_count = await db.scalar(
            select(func.count(InvestigationRun.id)).where(InvestigationRun.status == "queued")
        )
        if (pending_count or 0) >= 10:
            raise HTTPException(
                429, "The investigation queue is full; retry after a run completes."
            )
        since = datetime.now(UTC) - timedelta(hours=investigation.window_hours)
        trace_rows = list(
            (
                await db.scalars(
                    select(AgentTrace.id)
                    .where(
                        AgentTrace.started_at >= since,
                        AgentTrace.content_captured.is_(True),
                        AgentTrace.outcome != "in_progress",
                    )
                    .order_by(AgentTrace.started_at.desc())
                    .limit(min(investigation.sample_limit, settings.analysis_max_traces))
                )
            ).all()
        )
        if not trace_rows:
            raise HTTPException(
                409,
                "No completed traces with captured content are available in this time window.",
            )
        run = InvestigationRun(
            investigation_id=investigation.id,
            idempotency_key=idempotency_key,
            status="queued",
            stage="queued",
            selected_trace_ids=[str(item) for item in trace_rows],
            sampled_count=len(trace_rows),
            pricing_status="pending",
        )
        db.add(run)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(
                select(InvestigationRun).where(
                    InvestigationRun.investigation_id == investigation.id,
                    InvestigationRun.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise HTTPException(409, "This investigation already has a queued or running job.")
            return _run_view(existing)
        return _run_view(run)

    @router.get("/investigation-runs/{run_id}")
    async def get_investigation_run(run_id: uuid.UUID, db: SessionDep, principal: AdminUser):
        run = await db.get(InvestigationRun, run_id)
        if run is None:
            raise HTTPException(404, "Investigation run not found")
        findings = list(
            (
                await db.scalars(
                    select(InvestigationFinding)
                    .where(InvestigationFinding.run_id == run.id)
                    .order_by(InvestigationFinding.created_at)
                )
            ).all()
        )
        return {
            **_run_view(run),
            "findings": [_finding_view(item) for item in findings],
        }

    @router.patch("/investigation-findings/{finding_id}")
    async def update_investigation_finding(
        finding_id: uuid.UUID,
        body: FindingFeedbackBody,
        request: Request,
        db: SessionDep,
        principal: AdminUser,
    ):
        await csrf_for(request)
        finding = await db.get(InvestigationFinding, finding_id)
        if finding is None:
            raise HTTPException(404, "Finding not found")
        finding.feedback = (
            body.feedback.strip()[:1000] if body.feedback and body.feedback.strip() else None
        )
        finding.resolved_at = datetime.now(UTC) if body.resolved else None
        await db.commit()
        return _finding_view(finding)

    @router.get("/audit")
    async def audit_log(
        request: Request,
        db: SessionDep,
        principal: SessionUser,
        limit: int = 100,
    ):
        limit = min(max(limit, 1), 500)
        query = select(ToolAudit).order_by(ToolAudit.started_at.desc()).limit(limit)
        if principal.role != "admin":
            query = query.where(ToolAudit.user_id == principal.user_id)
        rows = list((await db.scalars(query)).all())
        return [
            {
                "id": str(row.id),
                "user": row.user_email or "Unknown caller",
                "action": row.tool_name or "request",
                "server": row.server_alias or "Gateway",
                "time": row.started_at,
                "duration_ms": row.duration_ms,
                "outcome": row.outcome,
                "error_category": row.error_category,
            }
            for row in rows
        ]

    return router


def _trace_view(trace: AgentTrace) -> dict:
    return {
        "id": str(trace.id),
        "user": trace.user_email,
        "agent": trace.agent_name,
        "content_captured": trace.content_captured,
        "outcome": trace.outcome,
        "error_category": trace.error_category,
        "route_tier": trace.route_tier,
        "route_reason": trace.route_reason,
        "model": trace.selected_model,
        "provider": trace.provider,
        "input_tokens": trace.input_tokens,
        "output_tokens": trace.output_tokens,
        "total_tokens": trace.total_tokens,
        "estimated_cost_usd": float(trace.estimated_cost_usd)
        if trace.estimated_cost_usd is not None
        else None,
        "pricing_status": trace.pricing_status,
        "started_at": trace.started_at,
        "duration_ms": trace.duration_ms,
    }


def _investigation_view(item: AgentInvestigation) -> dict:
    return {
        "id": str(item.id),
        "name": item.name,
        "expected_behavior": item.expected_behavior,
        "checks": item.checks or [],
        "window_hours": item.window_hours,
        "sample_limit": item.sample_limit,
        "max_cost_usd": float(item.max_cost_usd),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _run_view(item: InvestigationRun) -> dict:
    return {
        "id": str(item.id),
        "investigation_id": str(item.investigation_id),
        "status": item.status,
        "stage": item.stage,
        "sampled_count": item.sampled_count,
        "model": item.model,
        "provider": item.provider,
        "input_tokens": item.input_tokens,
        "output_tokens": item.output_tokens,
        "estimated_cost_usd": float(item.estimated_cost_usd)
        if item.estimated_cost_usd is not None
        else None,
        "pricing_status": item.pricing_status,
        "duration_ms": item.duration_ms,
        "error_category": item.error_category,
        "error_message": item.error_message,
        "attempts": item.attempts,
        "created_at": item.created_at,
        "started_at": item.started_at,
        "completed_at": item.completed_at,
    }


def _finding_view(item: InvestigationFinding) -> dict:
    return {
        "id": str(item.id),
        "run_id": str(item.run_id),
        "category": item.category,
        "priority": item.priority,
        "title": item.title,
        "summary": item.summary,
        "recommendation": item.recommendation,
        "evidence": item.evidence or [],
        "feedback": item.feedback,
        "resolved_at": item.resolved_at,
        "created_at": item.created_at,
    }


def _p95(sorted_values: list[int]) -> int | None:
    if not sorted_values:
        return None
    return sorted_values[max(0, ceil(len(sorted_values) * 0.95) - 1)]


async def _load_server_view(db: AsyncSession, server_id: uuid.UUID, principal: Principal) -> dict:
    server = await db.get(UpstreamServer, server_id)
    tools = list(
        (
            await db.scalars(select(DiscoveredTool).where(DiscoveredTool.server_id == server_id))
        ).all()
    )
    return _server_view(server, tools, principal)

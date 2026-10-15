"""Dashboard API for workspace identity, MCP server registry and audit activity."""

import asyncio
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .db import session_dependency
from .models import ApiKey, DiscoveredTool, Invitation, ToolAudit, UpstreamServer, User, WebSession
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

SessionDep = Annotated[AsyncSession, Depends(session_dependency)]
SessionUser = Annotated[Principal, Depends(require_session)]
AdminUser = Annotated[Principal, Depends(require_admin)]


class LoginBody(BaseModel):
    email: EmailStr
    password: SecretStr


class KeyBody(BaseModel):
    label: str = Field(default="Personal key", min_length=1, max_length=100)


class InvitationBody(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    role: Literal["member", "admin"] = "member"


class AcceptInvitationBody(BaseModel):
    token: str = Field(min_length=30, max_length=180)
    email: EmailStr
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


async def _load_server_view(db: AsyncSession, server_id: uuid.UUID, principal: Principal) -> dict:
    server = await db.get(UpstreamServer, server_id)
    tools = list(
        (
            await db.scalars(select(DiscoveredTool).where(DiscoveredTool.server_id == server_id))
        ).all()
    )
    return _server_view(server, tools, principal)

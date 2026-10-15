"""FastAPI application hosting the dashboard API and MCP gateway endpoint."""

import asyncio
import logging
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .api import create_api_router
from .bootstrap import bootstrap_workspace
from .config import Settings
from .db import make_database
from .investigations import investigation_worker
from .mcp_gateway import build_mcp_server
from .models import ApiKey, ToolAudit, User, now_utc
from .security import authenticate_api_key, authenticate_chat_assertion
from .upstreams import UpstreamManager

logger = logging.getLogger("urumi.gateway")


class MCPKeyMiddleware(BaseHTTPMiddleware):
    """Authenticate every downstream MCP HTTP request with a live, revocable user key."""

    def __init__(self, app, sessions, settings: Settings):
        super().__init__(app)
        self.sessions = sessions
        self.settings = settings
        self.request_times: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request, call_next):
        path = request.url.path
        if path != "/mcp" and not path.startswith("/mcp/"):
            return await call_next(request)
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            await self._audit_rejection(None, None, "missing_or_invalid_bearer")
            return JSONResponse(
                {
                    "error": "unauthorized",
                    "message": "Provide a gateway API key as a Bearer token.",
                },
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        async with self.sessions() as session:
            if token.startswith("chatv1."):
                principal, reason = await authenticate_chat_assertion(session, token, self.settings)
            else:
                principal, reason = await authenticate_api_key(session, token, self.settings)
            if principal is None or reason != "ok":
                key_id = token[4:].partition(".")[0] if token.startswith("urm_") else None
                user_email = principal.email if principal else None
                user_id = principal.user_id if principal else None
                if key_id:
                    key = await session.get(ApiKey, key_id)
                    if key:
                        user = await session.get(User, key.user_id)
                        if user:
                            user_email, user_id = user.email, user.id
                await self._audit_rejection(user_id, key_id, reason, user_email)
                return JSONResponse(
                    {
                        "error": "unauthorized",
                        "message": "The gateway API key is invalid, revoked, or inactive.",
                    },
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
        now = time.monotonic()
        rate_key = principal.key_id or f"user:{principal.user_id}"
        window = self.request_times[rate_key]
        while window and window[0] <= now - 60:
            window.popleft()
        if len(window) >= self.settings.api_rpm_limit:
            await self._audit_rejection(
                principal.user_id, principal.key_id, "rate_limited", principal.email
            )
            return JSONResponse(
                {"error": "rate_limited", "message": "Gateway request rate limit exceeded."},
                status_code=429,
            )
        window.append(now)
        request.scope.setdefault("state", {})["principal"] = principal
        return await call_next(request)

    async def _audit_rejection(self, user_id, key_id, category: str, email: str | None = None):
        try:
            async with self.sessions() as session:
                session.add(
                    ToolAudit(
                        correlation_id=str(uuid.uuid4()),
                        user_id=user_id,
                        key_id=key_id,
                        user_email=email,
                        outcome="denied",
                        error_category=category,
                        duration_ms=0,
                        completed_at=now_utc(),
                    )
                )
                await session.commit()
        except SQLAlchemyError:
            logger.exception("Could not persist rejected MCP request audit")


async def _probe_loop(upstreams: UpstreamManager, settings: Settings):
    while True:
        await asyncio.sleep(settings.probe_interval_seconds)
        try:
            await upstreams.refresh_all()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background MCP discovery cycle failed")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine, sessions = make_database(settings.database_url)
    upstreams = UpstreamManager(sessions, settings)
    mcp = build_mcp_server(sessions, settings, upstreams)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessions = sessions
        app.state.upstreams = upstreams
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        # The Helm migration Job owns schema changes; fail early when it has not run.
        async with sessions() as session:
            await session.execute(select(User.id).limit(1))
            await session.execute(
                update(ToolAudit)
                .where(ToolAudit.outcome == "in_progress")
                .values(
                    outcome="unknown_outcome",
                    error_category="gateway_restarted",
                    completed_at=now_utc(),
                )
            )
            await session.commit()
        await bootstrap_workspace(sessions, settings)
        await upstreams.refresh_all()
        task = asyncio.create_task(_probe_loop(upstreams, settings), name="mcp-discovery")
        analysis_task = asyncio.create_task(
            investigation_worker(sessions, settings), name="trace-investigations"
        )
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            task.cancel()
            analysis_task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            try:
                await analysis_task
            except asyncio.CancelledError:
                pass
            await upstreams.close()
            await engine.dispose()

    app = FastAPI(title="Urumi Team MCP Gateway", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessions = sessions
    app.state.upstreams = upstreams
    app.add_middleware(MCPKeyMiddleware, sessions=sessions, settings=settings)
    app.include_router(create_api_router())

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"status": "alive"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz():
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return {"status": "ready"}

    app.mount("/", mcp.streamable_http_app())
    return app

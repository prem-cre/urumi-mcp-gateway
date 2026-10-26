"""PostgreSQL-backed, atomic fixed-window rate limits for every gateway replica."""

import hashlib
import time

from sqlalchemy import case
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import RateLimitBucket
from .security import Principal


async def consume_request(
    sessions: async_sessionmaker,
    principal: Principal,
    requests_per_minute: int,
    now: float | None = None,
) -> tuple[bool, int, int]:
    """Consume one user-scoped request and return (allowed, remaining, reset_seconds)."""
    timestamp = time.time() if now is None else now
    window_start = int(timestamp // 60) * 60
    reset_seconds = max(1, window_start + 60 - int(timestamp))
    identity = f"{principal.user_id}:{principal.key_id or 'dashboard'}".encode()
    key_hash = hashlib.sha256(identity).hexdigest()

    async with sessions() as session:
        dialect = session.get_bind().dialect.name
        insert = pg_insert if dialect == "postgresql" else sqlite_insert
        if dialect not in {"postgresql", "sqlite"}:
            raise RuntimeError("Rate limiting requires PostgreSQL or SQLite storage")
        statement = insert(RateLimitBucket).values(
            key_hash=key_hash,
            window_start=window_start,
            request_count=1,
        )
        excluded = statement.excluded
        same_window = RateLimitBucket.window_start == excluded.window_start
        statement = statement.on_conflict_do_update(
            index_elements=[RateLimitBucket.key_hash],
            set_={
                "window_start": excluded.window_start,
                "request_count": case((same_window, RateLimitBucket.request_count + 1), else_=1),
            },
        ).returning(RateLimitBucket.request_count)
        count = (await session.execute(statement)).scalar_one()
        await session.commit()

    allowed = count <= requests_per_minute
    return allowed, max(0, requests_per_minute - count), reset_seconds

"""Persistent identity, server registry, tool catalog and audit records."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


def now_utc() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16), default="member")
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    keys: Mapped[list["ApiKey"]] = relationship(back_populates="user")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    label: Mapped[str] = mapped_column(String(100), default="Personal key")
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="keys")


class WebSession(Base):
    __tablename__ = "web_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_digest: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16), default="member")
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class UpstreamServer(Base):
    __tablename__ = "upstream_servers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alias: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(240), default="")
    url: Mapped[str] = mapped_column(String(2048))
    auth_header: Mapped[str | None] = mapped_column(String(100), nullable=True)
    auth_prefix: Mapped[str] = mapped_column(String(80), default="Bearer ")
    credential_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    member_tools: Mapped[list[str]] = mapped_column(JsonType, default=list)
    status: Mapped[str] = mapped_column(String(20), default="unreachable")
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(240), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )

    tools: Mapped[list["DiscoveredTool"]] = relationship(back_populates="server")


class DiscoveredTool(Base):
    __tablename__ = "discovered_tools"
    __table_args__ = (UniqueConstraint("server_id", "original_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("upstream_servers.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(256))
    public_name: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(1000), default="")
    input_schema: Mapped[dict] = mapped_column(JsonType)
    output_schema: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    annotations: Mapped[dict] = mapped_column(JsonType, default=dict)
    schema_hash: Mapped[str] = mapped_column(String(64))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    server: Mapped[UpstreamServer] = relationship(back_populates="tools")


class ToolAudit(Base):
    __tablename__ = "tool_audits"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    key_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    server_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("upstream_servers.id"), nullable=True
    )
    user_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    server_alias: Mapped[str | None] = mapped_column(String(48), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), default="in_progress", index=True)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

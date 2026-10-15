"""Credential hashing, session authentication and upstream secret encryption."""

import hashlib
import hmac
import ipaddress
import secrets
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import ApiKey, User, WebSession

_password_hasher = PasswordHasher()


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    key_id: str | None = None
    session_id: uuid.UUID | None = None


def digest_secret(secret: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), secret.encode(), hashlib.sha256).hexdigest()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def create_api_key(user_id: uuid.UUID, label: str, pepper: str) -> tuple[ApiKey, str]:
    key_id = uuid.uuid4().hex
    secret = secrets.token_urlsafe(32)
    raw_key = f"urm_{key_id}.{secret}"
    record = ApiKey(id=key_id, user_id=user_id, label=label, digest=digest_secret(secret, pepper))
    return record, raw_key


async def authenticate_api_key(
    session: AsyncSession, presented: str, settings: Settings
) -> tuple[Principal | None, str]:
    if not presented.startswith("urm_"):
        return None, "invalid_key"
    key_id, separator, secret = presented[4:].partition(".")
    if not separator or len(key_id) != 32 or not secret:
        return None, "invalid_key"
    record = await session.get(ApiKey, key_id)
    if record is None:
        return None, "invalid_key"
    user = await session.get(User, record.user_id)
    if user is None or not user.active:
        return None, "inactive_user"
    expected = digest_secret(secret, settings.api_key_pepper.get_secret_value())
    if not hmac.compare_digest(record.digest, expected):
        return None, "invalid_key"
    if record.revoked_at is not None:
        return Principal(
            record.user_id, user.email, user.display_name, user.role, key_id
        ), "revoked_key"
    now = datetime.now(UTC)
    if record.last_used_at is None or (now - record.last_used_at).total_seconds() >= 60:
        record.last_used_at = now
        await session.commit()
    return Principal(user.id, user.email, user.display_name, user.role, key_id), "ok"


def create_chat_assertion(principal: Principal, settings: Settings) -> str:
    """Mint a short-lived internal bearer token for one dashboard chat turn."""
    if principal.session_id is None:
        raise ValueError("A dashboard session is required for chat delegation")
    expires_at = int((datetime.now(UTC) + timedelta(minutes=2)).timestamp())
    payload = ".".join(
        (
            "chatv1",
            str(principal.user_id),
            str(principal.session_id),
            str(expires_at),
            secrets.token_urlsafe(12),
        )
    )
    signature = hmac.new(
        settings.session_pepper.get_secret_value().encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


async def authenticate_chat_assertion(
    session: AsyncSession, presented: str, settings: Settings
) -> tuple[Principal | None, str]:
    """Validate an internal assertion and re-check its source session on every MCP request."""
    parts = presented.split(".")
    if len(parts) != 6 or parts[0] != "chatv1":
        return None, "invalid_chat_assertion"
    _, raw_user_id, raw_session_id, raw_expiry, nonce, signature = parts
    if not nonce or not raw_expiry.isdecimal() or len(signature) != 64:
        return None, "invalid_chat_assertion"
    payload = ".".join(parts[:-1])
    expected = hmac.new(
        settings.session_pepper.get_secret_value().encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None, "invalid_chat_assertion"
    try:
        user_id = uuid.UUID(raw_user_id)
        session_id = uuid.UUID(raw_session_id)
        expires_at = int(raw_expiry)
    except ValueError:
        return None, "invalid_chat_assertion"
    now = datetime.now(UTC)
    user = await session.get(User, user_id)
    if user is None:
        return None, "inactive_user"
    principal = Principal(
        user.id,
        user.email,
        user.display_name,
        user.role,
        session_id=session_id,
    )
    if expires_at <= int(now.timestamp()):
        return principal, "expired_chat_assertion"
    web_session = await session.get(WebSession, session_id)
    if web_session is None or web_session.user_id != user_id or web_session.expires_at <= now:
        return principal, "inactive_chat_session"
    if not user.active:
        return principal, "inactive_user"
    return principal, "ok"


async def authenticate_session(request: Request) -> tuple[Principal, WebSession, str]:
    raw = request.cookies.get("urumi_session")
    if not raw:
        raise HTTPException(status_code=401, detail="Authentication required")
    settings: Settings = request.app.state.settings
    digest = digest_secret(raw, settings.session_pepper.get_secret_value())
    async with request.app.state.sessions() as session:
        web_session = await session.scalar(
            select(WebSession).where(WebSession.token_digest == digest)
        )
        if web_session is None or web_session.expires_at <= datetime.now(UTC):
            raise HTTPException(status_code=401, detail="Session expired")
        user = await session.get(User, web_session.user_id)
        if user is None or not user.active:
            raise HTTPException(status_code=401, detail="Account disabled")
        principal = Principal(
            user.id, user.email, user.display_name, user.role, session_id=web_session.id
        )
        return principal, web_session, digest


async def require_session(request: Request) -> Principal:
    principal, web_session, _ = await authenticate_session(request)
    request.state.web_session = web_session
    return principal


async def require_admin(request: Request) -> Principal:
    principal = await require_session(request)
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator role required")
    return principal


def verify_csrf(request: Request, web_session: WebSession, settings: Settings) -> None:
    supplied = request.headers.get("x-csrf-token", "")
    actual = digest_secret(supplied, settings.session_pepper.get_secret_value()) if supplied else ""
    if not hmac.compare_digest(web_session.csrf_digest, actual):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


def encrypt_credential(credential: str, settings: Settings) -> str:
    key = settings.upstream_encryption_key.get_secret_value().encode()
    return Fernet(key).encrypt(credential.encode()).decode()


def decrypt_credential(ciphertext: str | None, settings: Settings) -> str | None:
    if ciphertext is None:
        return None
    key = settings.upstream_encryption_key.get_secret_value().encode()
    try:
        return Fernet(key).decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise RuntimeError(
            "Upstream credential cannot be decrypted; check the configured encryption key"
        ) from exc


def validate_upstream_url(url: str, settings: Settings) -> str:
    """Restrict upstream targets and reject private/reserved DNS results for public HTTPS URLs."""
    parsed = urlsplit(url.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise HTTPException(
            status_code=422, detail="Use an HTTP(S) URL without embedded credentials"
        )
    if parsed.fragment or parsed.query:
        raise HTTPException(
            status_code=422, detail="Upstream URL cannot include a query or fragment"
        )
    host = parsed.hostname.rstrip(".").lower()
    allowed_internal = {value.rstrip(".").lower() for value in settings.internal_hosts}
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if host in allowed_internal:
        if parsed.scheme != "http" and parsed.scheme != "https":
            raise HTTPException(status_code=422, detail="Invalid internal URL scheme")
        return url.strip()
    if ip is not None:
        raise HTTPException(status_code=422, detail="IP-address upstream URLs are not allowed")
    if parsed.scheme != "https":
        raise HTTPException(status_code=422, detail="External upstreams must use HTTPS")
    if host in {"localhost", "metadata.google.internal"} or host.endswith(
        (".localhost", ".local", ".internal")
    ):
        raise HTTPException(status_code=422, detail="Local and metadata hostnames are not allowed")
    try:
        addresses = {
            result[4][0]
            for result in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        }
    except OSError as exc:
        raise HTTPException(status_code=422, detail="Upstream hostname did not resolve") from exc
    if not addresses:
        raise HTTPException(status_code=422, detail="Upstream hostname did not resolve")
    for address in addresses:
        resolved = ipaddress.ip_address(address)
        if not resolved.is_global:
            raise HTTPException(
                status_code=422, detail="External upstream resolves to a non-public address"
            )
    return url.strip()


def new_session_values(settings: Settings) -> tuple[str, str, datetime]:
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours)
    return token, csrf, expires

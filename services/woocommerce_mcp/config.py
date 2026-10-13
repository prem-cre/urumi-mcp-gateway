"""Deployment-only configuration. Secret values are never included in reprs."""

from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WC_MCP_", extra="ignore")
    signing_url: str | None = None
    store_url: str
    consumer_key: SecretStr
    consumer_secret: SecretStr
    gateway_token: SecretStr
    timezone: str = "Asia/Kolkata"
    timeout_seconds: float = 15
    max_response_bytes: int = 1_048_576
    allowed_hosts: list[str] = ["urumi-woocommerce-mcp:8000", "localhost:*", "127.0.0.1:*"]

    @field_validator("store_url", "signing_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("A fixed HTTP(S) store URL without credentials/query is required")
        return value.rstrip("/")

    @field_validator("timezone")
    @classmethod
    def validate_zone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("gateway_token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("Use a gateway service token of at least 32 characters")
        return value

    @field_validator("timeout_seconds", "max_response_bytes")
    @classmethod
    def positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Must be positive")
        return value

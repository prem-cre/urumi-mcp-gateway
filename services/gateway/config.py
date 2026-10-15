"""Environment-backed gateway configuration."""

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", extra="ignore")

    database_url: str
    api_key_pepper: SecretStr
    session_pepper: SecretStr
    upstream_encryption_key: SecretStr
    bootstrap_admin_email: str
    bootstrap_admin_password: SecretStr
    bootstrap_admin_name: str = "Workspace Admin"
    bootstrap_demo_token: SecretStr
    store_url: str = "http://store.localhost:8080"
    woo_mcp_url: str = "http://urumi-woocommerce-mcp:8000/mcp"
    inventory_mcp_url: str = "http://urumi-inventory-mcp:8000/mcp"
    fulfillment_mcp_url: str = "http://urumi-fulfillment-mcp:8000/mcp"
    woo_mcp_token: SecretStr
    internal_hosts: list[str] = [
        "urumi-woocommerce-mcp",
        "urumi-inventory-mcp",
        "urumi-fulfillment-mcp",
    ]
    mcp_allowed_hosts: list[str] = [
        "gateway.localhost:*",
        "gateway.example.com",
        "localhost:*",
        "127.0.0.1:*",
    ]
    mcp_allowed_origins: list[str] = [
        "http://gateway.localhost:8080",
        "https://gateway.example.com",
        "http://localhost:*",
        "http://127.0.0.1:*",
    ]
    session_cookie_secure: bool = False
    session_ttl_hours: int = 12
    api_rpm_limit: int = 120
    probe_interval_seconds: int = 30
    discovery_timeout_seconds: float = 5
    tool_timeout_seconds: float = 20
    upstream_concurrency: int = 5
    max_tool_schema_bytes: int = 65536
    max_tool_result_bytes: int = 32768

    @field_validator("bootstrap_admin_email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()

"""Environment-backed gateway configuration."""

from pydantic import Field, SecretStr, field_validator
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
    chat_gateway_mcp_url: str = "http://127.0.0.1:8000/mcp"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_router_model: str = ""
    llm_standard_model: str = ""
    llm_complex_model: str = ""
    llm_provider_name: str = "OpenAI-compatible endpoint"
    llm_model_pricing: dict[str, dict[str, float]] = Field(default_factory=dict)
    llm_api_key: SecretStr | None = None
    llm_analysis_model: str = ""
    trace_content_capture_enabled: bool = False
    trace_content_retention_days: int = Field(default=7, ge=1, le=30)
    analysis_poll_seconds: float = Field(default=2, ge=1, le=30)
    analysis_lease_seconds: int = Field(default=180, ge=60, le=900)
    analysis_timeout_seconds: float = Field(default=60, ge=5, le=180)
    analysis_max_output_tokens: int = Field(default=1200, ge=256, le=4000)
    analysis_max_traces: int = Field(default=25, ge=1, le=100)
    llm_timeout_seconds: float = 45
    chat_turn_timeout_seconds: float = 120
    chat_max_tool_rounds: int = 5
    chat_max_tool_calls: int = 8

    @field_validator("bootstrap_admin_email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()

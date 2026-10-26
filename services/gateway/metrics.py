"""Low-cardinality Prometheus instrumentation for gateway and MCP activity."""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

HTTP_REQUESTS = Counter(
    "urumi_gateway_http_requests_total",
    "HTTP requests handled by the gateway",
    ("route", "method", "status"),
)
HTTP_DURATION = Histogram(
    "urumi_gateway_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ("route", "method"),
)
MCP_TOOL_CALLS = Counter(
    "urumi_gateway_mcp_tool_calls_total",
    "MCP tool calls finalized by the gateway",
    ("server_alias", "outcome"),
)
MCP_TOOL_DURATION = Histogram(
    "urumi_gateway_mcp_tool_duration_seconds",
    "MCP tool call duration in seconds",
    ("server_alias", "outcome"),
)
UPSTREAM_CIRCUIT_STATE = Counter(
    "urumi_gateway_upstream_circuit_events_total",
    "Circuit breaker state transitions",
    ("server_alias", "state"),
)


def prometheus_response():
    from starlette.responses import Response

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

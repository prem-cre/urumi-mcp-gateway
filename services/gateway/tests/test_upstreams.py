import httpx

from services.gateway.upstreams import UpstreamCallError, _operation_error, safe_error


def test_sdk_exception_group_with_connect_error_is_classified_as_unreachable():
    error = ExceptionGroup("MCP transport failed", [httpx.ConnectError("connection refused")])

    assert safe_error(error) == "upstream_unreachable"
    routed = _operation_error(error, dispatched=True)
    assert isinstance(routed, UpstreamCallError)
    assert routed.outcome_unknown is True

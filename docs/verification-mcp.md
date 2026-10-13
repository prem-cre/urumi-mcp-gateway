# WooCommerce MCP verification — 13 October 2026

Verified against the running local WooCommerce store, using the locked official Python SDK (MCP 1.30.0) and a temporary in-cluster gateway-labeled developer Job. This is not a deployed team gateway.

| Requirement / check | Evidence |
| --- | --- |
| Own server, Streamable HTTP | Python source in services/woocommerce_mcp; official client negotiated 2025-11-25 |
| Discover tools | Exactly list_products, create_product, list_orders, get_order, update_order_status |
| list_products | Returned real seed product with bounded pagination |
| create_product | Created product **#14**, MCP Verification Notebook, INR 349.00, published; public WooCommerce Store API confirmed purchasable |
| list_orders(today) | Returned today's real visitor COD order **#13**, using configured Asia/Kolkata |
| get_order | Returned order #13 items, totals and status, without billing/shipping/order secrets |
| update_order_status | Changed dummy order #13 from processing to on-hold, then restored processing; authenticated admin check confirmed order |
| Authentication | Missing gateway service bearer token rejected with HTTP 401; credentialed MCP client worked |
| Validation | Negative price rejected before REST dispatch; missing order returned MCP isError; invalid pagination/refund status tested |
| Networking | Unrelated pod resolved service DNS but TCP to MCP port 8000 was denied; gateway-labeled Job reached it |
| Secret placement | Store API key and independent gateway token in Kubernetes Secret; no browser input, committed secret or runtime Kubernetes API token |
| Workload security | Non-root UID 10001, read-only filesystem, dropped capabilities, no service-account token |
| Health and resources | Startup/liveness and store-authenticated readiness passed; requests/limits configured |
| Failure behavior | Tests prove a timed-out write is attempted once, sanitized as unknown_outcome, not replayed; redirects and oversized responses rejected |
| Helm | local and production configurations render with MCP enabled; no MCP ingress/NodePort |

Reproduce with `make mcp-test`, `make check`, `./scripts/deploy-mcp.sh`, then create a fresh dummy COD order and run `KUBECONFIG="$PWD/.local/kubeconfig" uv run python scripts/verify-mcp.py ORDER_ID`. The verification Job and ConfigMap are removed afterward. A verification product is intentionally left for storefront inspection. The script mutates only the explicitly supplied fixture order; do not pass real customer orders.

The local deployment script gives each new image a distinct development tag, preventing Kubernetes from silently reusing old code. VPS deployment needs a pushed registry image/digest and the matching Secret. No live VPS deployment is claimed.

## Remaining assessment work

The actual team gateway must aggregate at least three upstreams and implement user roles, revocable hashed API keys, registry management, permissions, audits and failure isolation. Chat and external clients must then use that gateway. The present developer harness has no team identity or audit implementation and is explicitly temporary.

The upstream service account has WooCommerce shop-manager capabilities; the MCP surface exposes only five operations, but compromise of its store credential has broader consequences. Gateway permissions are still mandatory. Network labels are not authentication: cluster administrators who can create labeled pods and read Secrets remain trusted.

Local HTTP OAuth signing does not encrypt traffic or sign JSON request bodies. TLS and Secret encryption at rest remain production improvements. Store/MCP timezone alignment is a deployment requirement. Emails can be triggered by status changes; the local store has no configured outgoing email delivery.

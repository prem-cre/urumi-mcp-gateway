# WooCommerce MCP server

Own Python implementation using the official MCP SDK + FastAPI. Five tools over **stateless Streamable HTTP** at `/mcp`, with JSON results and structured content. This is a domain adapter, not an installed WooCommerce MCP plugin.

| Tool | Input | Result / effect |
| --- | --- | --- |
| list_products | page, per_page (1–50), search | product summaries and pagination |
| create_product | name, decimal-string price, optional SKU, publish | simple published/draft product; never automatically retried |
| list_orders | page, per_page, status, today | order items/totals/status, pagination, store timezone |
| get_order | positive order_id | same privacy-minimized order summary |
| update_order_status | positive order_id, allowed status | changes status; may trigger stock/email effects |

No customer names, email, addresses, IP addresses, order keys or arbitrary metadata are returned. Refunding/deleting orders is deliberately not offered by update_order_status. Prices stay decimal strings. List results are bounded to 50 records; HTTP response bodies to 1 MiB. Tool argument validation occurs before REST dispatch.

## Deployment

```sh
# Store foundation must already run:
./scripts/local-up.sh
./scripts/deploy-mcp.sh
export KUBECONFIG="$PWD/.local/kubeconfig"
kubectl -n urumi get pods,svc
```

`deploy-mcp.sh` provisions a dedicated shop-manager service user and read/write WooCommerce API key, creates the `urumi-woocommerce-mcp` Secret, builds the container, imports it into k3d and enables the Helm component. The runtime only calls supported REST endpoints: provisioning administrative code is not the MCP data path. The service user has broader WooCommerce permissions than the five-tool surface; member/tool permissions and attribution belong in the upcoming gateway.

Secret keys: `consumer-key`, `consumer-secret`, `gateway-token`. Provisioning uses captured stdin/stdout, never CLI credential arguments or browser state. Existing Secret is retained. If provisioning fails before Secret creation, retry replaces the orphan service key. Credentials are not written to committed files.

The MCP server is a ClusterIP Service, has no Ingress/NodePort, and only same-namespace gateway-labeled pods may reach port 8000. Every MCP request also needs the independent gateway service bearer token. Health paths are probe endpoints and expose no data. The container runs as UID 10001 with a read-only root filesystem, no privileges, no mounted Kubernetes API token and a writable bounded `/tmp` volume.

## REST authentication and failure behavior

REST `wc/v3` separates this implementation from WordPress storage details/HPOS. Local internal HTTP requests use WooCommerce OAuth 1.0a HMAC-SHA256 with fresh nonce/timestamp. `WC_MCP_SIGNING_URL` matches WordPress's canonical home URL, while `WC_MCP_STORE_URL` selects the fixed internal network destination. WooCommerce computes the signature using its home URL, not the internal Service hostname. HTTPS requests use HTTP Basic with the consumer key/secret. No credentials are passed in query strings as consumer secrets, and HTTP client INFO logs are suppressed.

Signing on HTTP does not encrypt response or request body data. OAuth here does not sign JSON bodies. Use internal TLS or a trusted isolated network for stronger confidentiality/integrity; keep production public access HTTPS. Redirects are rejected and TLS validation is never disabled. Network egress permits WordPress + DNS only; this phase's policy supports the internal HTTP Service. A future TLS WordPress Service needs an explicit policy port change.

Requests have a 15-second read timeout and 5-second connect timeout. HTTP errors, not-found, auth failure, oversized data and transport failures are sanitized. Timed-out writes return `unknown_outcome`; they may have committed. The service never automatically retries writes. Call a read tool to reconcile instead. Liveness tests the process; readiness verifies the configured store credential with a minimal products query.

## Verification

```sh
uv run pytest services/woocommerce_mcp/tests -q
uv run ruff check scripts services
# Creates a dummy COD order today; note its ID:
uv run python scripts/verify-store.py --checkout
# Uses a temporary gateway-labeled Job; creates a test product and
# changes/restores ONLY the supplied dummy order:
KUBECONFIG="$PWD/.local/kubeconfig" uv run python scripts/verify-mcp.py DUMMY_ORDER_ID
```

The temporary verification Job is a developer harness, not an implemented gateway and not the external-client acceptance flow. It is cleaned up after completion. Do not supply a real customer's order. The sample product is left published so its storefront visibility can be inspected. Today queries assume `WC_MCP_TIMEZONE` matches the store setting (`Asia/Kolkata`); responses explicitly name it.

For a VPS, publish the image to your registry and set `woocommerceMcp.enabled=true`, its image reference/digest and Secret name through production Helm values. The same manifests apply. No public MCP route is added.

The next phase is the actual gateway: member authentication/revocation, tool aggregation across three upstreams, routing, policies and audit logging. This service's bearer token is a backend service credential, not a team member API key or a full MCP OAuth authorization server.

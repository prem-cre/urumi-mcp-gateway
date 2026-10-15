# Scope and acceptance checklist

As of 15 October 2026, the store foundation, WooCommerce MCP adapter, team gateway and live dashboard are implemented and running on local Kubernetes. The gateway has been exercised through its real MCP endpoint with all three sources and separate admin/member identities. The full assessment is not yet end-to-end: chat, the store-order flow, a complete two-user demo recording and VPS deployment remain.

| Requirement | Status |
| --- | --- |
| Local Kubernetes and same Helm chart for VPS | Local gateway deployment verified; VPS not deployed. |
| Persistent WordPress, MariaDB and PostgreSQL; ingress, probes, limits, Secrets, network policies | Implemented in Helm; local negative network isolation passed `./scripts/verify-network.sh`. |
| WooCommerce REST-backed MCP with five required tools, Streamable HTTP, internal-only service | Implemented; see `services/woocommerce_mcp/README.md` and `docs/verification-mcp.md`. |
| Three upstreams, discovery, tool name collisions, routing and outage isolation | All 12 tools from WooCommerce plus two labeled sample services were listed and called live through `/mcp`. Inventory scale-to-zero produced an Unreachable status and MCP error while WooCommerce stayed healthy; recovery was verified. |
| Admin/member, invitations, hashed user API keys, immediate revocation, member tool grants | Live-tested with separate admin/member accounts, including write-tool filtering, direct denial and key revocation. |
| Every MCP tool call audited with principal, server/tool, timing and outcome | Live-tested with attributed success and denial rows; intent is committed before dispatch. |
| React dashboard for servers, grants, keys, team and activity | Implemented against live API. Chat page is a clearly marked next milestone. |
| Chat LLM tool loop acting as current user | Not implemented yet. Must use the same gateway MCP path and secret handling. |
| Two-user end-to-end demo, cross-server prompt, revoke/disable/pod-failure walkthrough | Gateway identity, permission, disable and outage cases passed with separate accounts; store order, chat and recorded walkthrough remain. |
| README, architecture/tradeoffs, threat model, local/VPS setup | Updated with implemented and planned behavior separated. |
| VPS/TLS, distributed rate limits, circuit breaker, exported metrics, optional fine-grained per-user grants | Deferred differentiators after mandatory flow works. |

## Gateway implementation boundary

The gateway terminates downstream Streamable HTTP, authenticates a current user key, filters its catalog, maps tool names to saved upstream records, validates inputs, and dispatches with a server-side credential. It is a protocol-aware gateway rather than an HTTP byte proxy. Upstream connections are retained per server/revision inside the single gateway process; operations to the same upstream are serialized while different upstreams stay independent. It deliberately does not automatically retry calls.

See [ADR 003](decisions/003-team-gateway.md) for the accepted implementation boundary and [gateway README](../services/gateway/README.md) for local operation.

## Definition-of-done demo sequence

1. Buy a seed product with COD; inspect the order in WooCommerce admin.
2. User A creates a product through chat; verify the storefront.
3. Buy that product manually, ask chat for today's orders in the store's timezone.
4. User B configures an external MCP client with only gateway URL/key; list enabled tools and answer today's-orders question.
5. Ask a question requiring WooCommerce and another upstream.
6. Revoke B's key; reject its next request, including an already-established MCP client.
7. Disable an upstream; re-list tools in chat and external client; direct calls are rejected too.
8. Delete an upstream pod, observe unreachable state and another upstream still working. Because Kubernetes can restart it quickly, use a controlled temporary scale-to-zero if necessary and restore afterward; disclose it.
9. Inspect audits attributed to A and B with durations and outcomes.

Optional multi-store work starts only after this sequence is reproducible.

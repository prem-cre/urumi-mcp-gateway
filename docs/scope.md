# Scope and acceptance checklist

As of 15 October 2026, the store foundation, WooCommerce MCP adapter, team gateway, live dashboard, backend chat tool loop, first-party chat observability and an on-demand Lens-inspired investigation workflow are implemented in code. The gateway has been exercised through its real MCP endpoint with all three sources and separate admin/member identities. The chat provider is not configured in the local cluster, so model-backed turns, captured model usage/cost/routing, content capture and investigation runs are not yet runtime-verified. The full assessment is not yet end-to-end: the store-order flow, complete two-user demo recording and VPS deployment remain.

| Requirement | Status |
| --- | --- |
| Local Kubernetes and same Helm chart for VPS | Local gateway deployment verified; VPS not deployed. |
| Persistent WordPress, MariaDB and PostgreSQL; ingress, probes, limits, Secrets, network policies | Implemented in Helm; local negative network isolation passed `./scripts/verify-network.sh`. |
| WooCommerce REST-backed MCP with five required tools, Streamable HTTP, internal-only service | Implemented; see `services/woocommerce_mcp/README.md` and `docs/verification-mcp.md`. |
| Three upstreams, discovery, tool name collisions, routing and outage isolation | All 12 tools from WooCommerce plus two labeled sample services were listed and called live through `/mcp`. Inventory scale-to-zero produced an Unreachable status and MCP error while WooCommerce stayed healthy; recovery was verified. |
| Admin/member, invitations, hashed user API keys, immediate revocation, member tool grants | Live-tested with separate admin/member accounts, including write-tool filtering, direct denial and key revocation. |
| Every MCP tool call audited with principal, server/tool, timing and outcome | Live-tested with attributed success and denial rows; intent is committed before dispatch. |
| React dashboard for servers, grants, keys, team, activity and chat | Implemented against the live API. Chat displays model status, tool traces, loading and setup errors. |
| Chat LLM tool loop acting as current user | Implemented with an OpenAI-compatible Chat Completions loop. It uses a short-lived signed assertion for the current session and the same local `/mcp` endpoint; per-tool gateway audits and permissions apply. Unit-tested. No provider key/configuration is present in the local cluster, so live model behavior remains unverified. |
| Model/token/cost observability and explainable task routing | Implemented in code: ordered generation/tool spans, model usage, configured-price estimates and unpriced labels, latency/outcomes, per-model/per-server summaries, trace detail, optional LiteLLM Auto Router alias and deterministic standard/complex profiles. Runtime model/provider behavior remains unverified until a provider is configured. |
| Lens-inspired trace investigation | Implemented in code: optional bounded redacted previews, short content TTL, admin-defined expected behavior/checks, frozen trace samples, durable on-demand job, explicit-priced preflight cap, one analysis call, grouped findings with exact evidence links, feedback/resolution. Capture is enabled only in local synthetic-data values and disabled in production values. Live LLM analysis is unverified without a provider. OTLP intake, scheduled runs, ClickHouse and datasets remain deferred. |
| Two-user end-to-end demo, cross-server prompt, revoke/disable/pod-failure walkthrough | Gateway identity, permission, disable and outage cases passed with separate accounts; store order, chat and recorded walkthrough remain. |
| README, architecture/tradeoffs, threat model, local/VPS setup | Updated with implemented and planned behavior separated. |
| VPS/TLS, distributed rate limits, circuit breaker, Prometheus export, trace retention and cross-trace AI evaluation, optional fine-grained per-user grants | Deferred differentiators after mandatory flow works. |

## Gateway implementation boundary

The gateway terminates downstream Streamable HTTP, authenticates a current user key, filters its catalog, maps tool names to saved upstream records, validates inputs, and dispatches with a server-side credential. It is a protocol-aware gateway rather than an HTTP byte proxy. Upstream connections are retained per server/revision inside the single gateway process; operations to the same upstream are serialized while different upstreams stay independent. It deliberately does not automatically retry calls.

See [ADR 003](decisions/003-team-gateway.md) for the accepted implementation boundary, [ADR 004](decisions/004-chat-observability-and-routing.md) for telemetry and model routing, and [gateway README](../services/gateway/README.md) for local operation.

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

# Scope and acceptance checklist

As of 20 October 2026, the store foundation, WooCommerce MCP adapter, team gateway, dashboard, backend chat loop, chat observability, on-demand trace investigation and runtime-control implementations are present. The local flow has been exercised with two distinct accounts: User A created product #16 in chat, placed COD orders #17 (Store API) and #19 (visible storefront), verified #19 in WooCommerce admin and queried today's orders; chat returned #19. User B used an external MCP client to list permitted tools, retrieve today's orders and check inventory, also made a cross-server chat query, then had both test keys revoked and subsequent MCP requests rejected with HTTP 401. The system order/disable/outage and network isolation flows have existing live verification. A live PostgreSQL rate-limit concurrency check and automated OIDC/circuit behavior tests now cover the added controls. The metrics endpoint responds inside the gateway pod, but the local labeled-scraper connection check failed and remains unresolved. OIDC is disabled without provider settings. A VPS deployment requires a supplied target cluster, DNS and deployment secrets. The demo video and submission are the user's deliverables.

| Requirement | Status |
| --- | --- |
| Local Kubernetes and same Helm chart for VPS | Local gateway/store deployed and verified with the same chart; VPS not deployed. |
| Persistent WordPress, MariaDB and PostgreSQL; ingress, probes, limits, Secrets, network policies | Implemented in Helm; local negative network isolation passed `./scripts/verify-network.sh`. |
| WooCommerce REST-backed MCP with five required tools, Streamable HTTP, internal-only service | Implemented; see `services/woocommerce_mcp/README.md` and `docs/verification-mcp.md`. |
| Three upstreams, discovery, tool name collisions, routing and outage isolation | All 12 tools from WooCommerce plus two labeled sample services were listed and called live through `/mcp`. Inventory scale-to-zero produced an Unreachable status and MCP error while WooCommerce stayed healthy; recovery was verified. |
| Admin/member, invitations, hashed user API keys, immediate revocation, member tool grants | Live-tested with separate admin/member accounts, including write-tool filtering, direct denial and key revocation. |
| Every MCP tool call audited with principal, server/tool, timing and outcome | Live-tested with attributed success and denial rows; intent is committed before dispatch. |
| React dashboard for servers, grants, keys, team, activity and chat | Implemented against the live API. Chat displays model status, tool traces, loading and setup errors. |
| Chat LLM tool loop acting as current user | Implemented with an OpenAI-compatible Chat Completions loop. It uses a short-lived signed assertion for the current session and the same local `/mcp` endpoint; per-tool gateway audits and permissions apply. Live product creation and order-listing turns completed against Gemini for User A; a cross-server orders/inventory prompt completed for User B. |
| Model/token/cost observability and explainable task routing | Implemented in code: ordered generation/tool spans, provider usage where reported, configured-price estimates and unpriced labels, latency/outcomes, per-model/per-server summaries, trace detail, optional LiteLLM Auto Router alias and deterministic standard/complex profiles. Local model-backed telemetry has been confirmed. |
| Lens-inspired trace investigation | Implemented in code: optional bounded redacted previews, short content TTL, admin-defined expected behavior/checks, frozen trace samples, durable on-demand job, explicit-priced preflight cap, one analysis call, grouped findings with exact evidence links, feedback/resolution. Capture is enabled only in local synthetic-data values and disabled in production values. A capped one-trace Gemini run completed locally. OTLP intake, scheduled runs, ClickHouse and datasets remain deferred. |
| Two-user end-to-end demo, cross-server prompt, revoke/disable/pod-failure walkthrough | Store/chat/order, external MCP, cross-server calls and key revocation were completed with two distinct users; the subsequent revoked-key request returned HTTP 401. Disablement and upstream isolation have prior live verification. The recorded demo remains. |
| README, architecture/tradeoffs, threat model, local/VPS setup | Updated with implemented and planned behavior separated. |
| Shared request rate limit, optional OIDC dashboard sign-in, internal Prometheus metrics, per-upstream circuit breaker | Implemented in source and Helm and deployed locally. The PostgreSQL limiter passed a live 20-concurrent-request check (5 allowed, 15 limited; the next minute reset). OIDC state/JWT validation and breaker open/half-open/close transitions have automated tests. `/metrics` returned valid exposition from the allowed Traefik pod identity; a separate pod matching the configured Prometheus selectors could not connect in local k3d, so the custom scrape policy remains unverified. OIDC is off until a real provider is configured. |
| Gateway-managed multi-endpoint load balancing | Kubernetes Services/Traefik handle pod selection; the gateway does not choose between an admin-configured pool of URLs. One gateway replica remains the supported deployment. |
| True cross-organization multi-tenancy, WebAssembly policy plugins, exact provider-independent tokenization | Deliberately deferred. This assessment models one team; plugin trust/ABI and tenant isolation require separate designs. Provider-returned token usage and explicit-price estimates are already recorded, with byte-based investigation preflight. |

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

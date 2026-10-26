# ADR 006: Gateway runtime controls and extension boundaries

Accepted 19 October 2026.

## Context

The gateway already has user-scoped MCP policy, chat/tool tracing and Kubernetes Service routing. The assessment needs bounded failures and explainable operations, while its accepted scope remains one team and one gateway replica. Adding broad policy plugins or SaaS tenancy without a trust and isolation contract would increase risk without helping the required two-user demonstration.

## Decisions

1. Apply an atomic fixed-window request limit in PostgreSQL to external MCP traffic and dashboard chat turns. The key is a hash derived from the authenticated user and API-key ID (or dashboard principal). All replicas using the same database share the bucket. Rate limiting does not claim to enforce model-token or cost budgets. If the limiter database operation fails, the request does not proceed.
2. Add optional dashboard OIDC login using authorization code with PKCE. Validate discovery issuer, HTTPS endpoints, state signature/expiry, ID-token signature/audience/issuer, nonce and verified email. Map email only to an existing active account; invitation and role management remain in the gateway. External MCP clients continue to use gateway API keys. OIDC configuration and its optional client secret are deployment settings/Secret values and are off by default.
3. Export HTTP, MCP tool and circuit-breaker counters/histograms from `/metrics`. Keep the endpoint off public Ingress. NetworkPolicy permits a scraper only when its namespace and pod labels are explicitly configured. Tool names and user identifiers are excluded from metric labels; the PostgreSQL audit/trace views remain the source for per-user and per-tool investigation.
4. Add a per-upstream circuit breaker: configurable consecutive transport/protocol failure threshold, cooldown and one serialized half-open operation. It does not replay a call. State is process-local; gateway replicas remain pinned to one until stateful upstream session behavior and worker ownership are verified.
5. Keep load balancing at Kubernetes Service/Traefik layers for this deployment. Services route only to ready pods; a single upstream URL can point to a Service. There is no gateway-managed weighted/least-latency pool or multiple endpoint registry.
6. Preserve the current single-team boundary. Separate Helm releases/databases are the deployment isolation boundary; this is not a multi-tenant SaaS data plane. Do not claim row-level tenant isolation.
7. Do not load WebAssembly modules in the request path until the project defines a policy ABI, trusted module publication/signing and revocation, deterministic resource/fuel limits, host-call capabilities, and an audit/review process. A plugin process is not introduced just to satisfy a feature checklist.
8. Use provider-reported token usage where returned and label configured-price costs as estimates. Do not claim a provider-independent exact tokenizer. Investigation preflight remains a conservative byte-based upper bound; provider billing/tokenization can differ.

## Consequences

- PostgreSQL is on the synchronous MCP/chat request path for the fixed-window counter; a storage outage fails closed and may reject otherwise valid traffic.
- Metrics are scrapeable only from cluster locations allowed by NetworkPolicy. No Prometheus server or alert rules are bundled.
- The circuit breaker helps one process avoid repeated calls to an unhealthy upstream. It does not coordinate across replicas.
- OIDC improves dashboard identity integration but is not enabled in the local assessment environment and does not replace API-key revocation tests.
- Multi-tenant isolation, WASM policies and gateway-managed endpoint pools need separate ADRs and acceptance checks if they become actual product requirements.

## Verification status

The code and chart configuration are implemented and deployed to the local k3d cluster with the chart's single gateway replica. `make check`, `make verify`, `make test`, and `./scripts/verify-network.sh` pass. A 20-request concurrent PostgreSQL check produced 5 allowed and 15 limited requests at a limit of 5. Automated tests cover OIDC state/JWT validation and circuit open/half-open/close transitions. The running `/metrics` endpoint returned valid exposition inside the gateway pod, but a labeled scraper pod could not connect in local k3d, so the NetworkPolicy scrape path remains unverified. A real OIDC callback still needs a provider registration, and a real VPS deployment needs a target cluster and deployment credentials.

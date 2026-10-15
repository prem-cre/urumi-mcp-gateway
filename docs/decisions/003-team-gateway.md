# ADR 003: team MCP gateway control plane and dispatch path

Accepted 15 October 2026.

## Decision

Implement the assessment gateway as a single Python 3.12/FastAPI workload using the official Python MCP SDK. The React dashboard talks to its `/api` routes. External MCP clients connect to one stateless Streamable HTTP endpoint at `/mcp`. PostgreSQL stores identities, hashed keys/sessions, invitations, upstream configuration, discovered tools and audit history. Helm deploys the gateway, dashboard, PostgreSQL and three internal upstreams with the store chart.

The gateway terminates MCP. It is not a transparent HTTP proxy: it authenticates the downstream caller, applies current permissions, validates tool arguments, chooses one upstream by a persisted tool map, attaches that server's static credential, and records the result.

## Identity, policy and secrets

- Dashboard login uses a database-backed session cookie with `HttpOnly`, `SameSite=Strict`, and a CSRF token on mutations. Production values enable `Secure` cookies.
- External MCP calls use an independent per-user random API key. PostgreSQL stores only a HMAC digest and public lookup ID; the live key and user state are checked for every downstream MCP request.
- Admins can manage the server registry, member invitations and member tool allowlists. Members can use only tools granted for the `member` role and can issue/revoke their own gateway keys. Admins can revoke other users' keys.
- Upstream credentials are encrypted at rest in PostgreSQL with a Fernet key from Kubernetes Secret. They are decrypted only to make the selected server call and are excluded from API responses and audit payloads.
- A key from the caller is never forwarded. The gateway constructs an independent upstream authorization header.

## Catalog and routing

The immutable admin-assigned server alias namespaces each upstream tool as `alias__tool`. A persisted `DiscoveredTool` row records public name, server UUID, original name, schema and schema hash; dispatch does not parse the public name. Normalization collisions receive a stable hash suffix. Catalog reads use PostgreSQL snapshots; discovery runs in the background and refreshes each MCP server independently. Disabling a server removes its tools from `tools/list` and rejects direct calls.

Member permissions use an allowlist of original upstream tool names on each server, shared by all workspace members. The same rule filters `tools/list` and is rechecked against PostgreSQL at `tools/call`. When discovery removes a tool or changes its schema hash, its member grant is cleared and requires admin approval again. Per-user and per-argument policies remain an optional later expansion.

## Sessions, outages and writes

The gateway keeps one upstream MCP client session per server ID/revision. A dedicated asyncio worker owns each SDK session and its AnyIO task group, processes that server's commands serially, and closes the session itself when it receives an invalidation command. This avoids cross-task transport cleanup while preserving session behavior. Separate server workers keep a blocked upstream from locking another server. Changes to URL, credential, enablement or removal increment the revision and schedule connection invalidation. The downstream server uses stateless JSON Streamable HTTP, so each call carries the user key and observes current revocation.

Health refresh has independent deadlines. A failed upstream retains its last catalog and is marked unreachable; other catalogs and calls are not blocked. A call to an unreachable upstream returns a clear error. Gateway readiness depends on PostgreSQL, not upstream health. This version has no circuit breaker, global in-flight semaphore or distributed rate limiter. The local rate limiter is per process, and Helm fixes the gateway at one replica.

The gateway commits an audit intent before dispatch. It does not retry tool calls. A timeout or transport interruption after dispatch becomes `unknown_outcome`, because a write may have committed even if its response was lost. On restart, stranded `in_progress` audits are also marked unknown.

## Deployment and consequences

The existing `platform` Helm chart deploys the gateway and dashboard under the same Traefik host: `/api`, `/mcp`, health routes go to the gateway and `/` goes to the static React workload. Local uses `gateway.localhost:8080` and non-secure cookies; production values use a chosen DNS hostname, TLS Secret and secure cookies. The same image/chart is used in both environments.

The implementation prioritizes an explainable, testable core: three MCP sources, one route/auth/audit path, live revocation, role-level grants and bounded failures. One process owns live upstream sessions and rate state; a VPS is single-node and is not HA. Cross-replica rate state, Prometheus export, a circuit breaker and hosted upstream provisioning require separate work and verification. Chat-specific trace storage, model usage/cost reporting and complexity profiles are covered by [ADR 004](004-chat-observability-and-routing.md).

## Chat integration decision (15 October 2026)

Keep the chat loop in the gateway process. It obtains the same permission-filtered tool catalog through the local `/mcp` Streamable HTTP endpoint and sends each model-selected tool call back through that endpoint using the official MCP client. This reuses the existing routing, argument validation, live enablement/permission checks, and audit-intent lifecycle; chat does not connect to upstream MCP servers directly.

For chat only, the backend mints a two-minute HMAC-signed bearer assertion containing the authenticated user ID and web-session ID. It is never returned to the dashboard. The `/mcp` middleware verifies the signature and expiry and reloads the session and active user from PostgreSQL on every request. Therefore logout, account deactivation and role changes take effect during a chat turn as they do for external calls. The assertion uses `key_id = NULL` in audit rows because the browser is authenticated by its session, not a generated API key.

Use an OpenAI-compatible Chat Completions endpoint. Base URL and model are non-secret Helm values; an optional provider API key is read only from `urumi-gateway-app` Secret key `llm-api-key`. The current local cluster has no provider configured, so its UI reports setup required and the chat API responds with HTTP 503 until a model URL and model are configured. The loop bounds prompt length, tool rounds/calls and tool-result text, treats upstream descriptions/results as untrusted, and never automatically repeats a tool call after a timeout. The dashboard keeps only recent text history in memory; chat generation/tool telemetry is persisted without raw prompt/arguments/results as defined in ADR 004.

# Team MCP Gateway

The gateway is a policy-enforcing MCP server and the only MCP client of registered upstreams. It exposes the dashboard API and one Streamable HTTP endpoint at `/mcp`.

## Components

- `app.py`: FastAPI lifecycle, revocable bearer-key middleware, request limits, health/readiness, background discovery and the durable investigation worker.
- `api.py`: dashboard sessions, invitations, API keys, server registry, permissions, audit queries, trace inspection and admin investigation APIs.
- `mcp_gateway.py`: dynamic `tools/list` and the single authorized, audited `tools/call` path.
- `chat.py`: bounded OpenAI-compatible model loop with explainable standard/complex model routing. It discovers and invokes tools through this gateway's `/mcp` endpoint using a short-lived server-signed dashboard-session assertion, and emits one trace observation per model/tool step.
- `investigations.py`: bounded one-call analysis of a frozen redacted trace sample, evidence validation, durable job claiming, cost records and no-replay failure handling.
- `trace_content.py`: best-effort field/pattern redaction and preview-size limits before optional trace content is persisted.
- `upstreams.py`: MCP client sessions, URL checks, tool discovery, name mapping, per-server serialization, timeouts and routing.
- `models.py` / `migrations/`: PostgreSQL registry, user/key/session data, catalog snapshots, tool audits, agent traces/observations, investigation definitions, runs and findings.
- `security.py`: Argon2 passwords, HMAC-digested API/session secrets, CSRF validation and Fernet credential encryption.

The first three upstreams are WooCommerce, inventory and fulfillment. Inventory and fulfillment are bounded sample MCP services, explicitly labeled as demo data. A tool name is `<immutable-server-alias>__<upstream-tool-name>`; the database stores the full routing map, so dispatch never parses the public name.

## Request and security flow

1. The external MCP client sends the gateway API key as a Bearer token. The gateway checks the key digest, current revocation state and active user in PostgreSQL on every MCP HTTP request. Dashboard browser requests use a separate HttpOnly session cookie and CSRF token.
2. `tools/list` reads the saved catalog and omits disabled servers and member-restricted tools. `tools/call` resolves the stored public-name mapping, rechecks enablement and grants, validates arguments against the stored JSON Schema, then commits an audit intent.
3. Only after that commit does the gateway decrypt the selected upstream credential and make an MCP call. It creates a fresh outgoing request context and never copies the caller's authorization header.
4. The gateway bounds returned content, redacts the configured static credential, and records outcome, duration, principal and server/tool identity. Payloads are not stored in the audit table.

Member grants currently apply to all members of the workspace; admins can call all discovered tools. Grants are enforced both in the visible catalog and again at dispatch. A downstream client can cache a catalog, so clients should re-list after server or grant changes; the gateway's direct call check remains authoritative.

Upstream credentials are encrypted in PostgreSQL with a Fernet key supplied by a Kubernetes Secret and are never included in dashboard responses. User API keys are random, displayed once and stored as HMAC digests using a separate Secret. Local generated values live only in ignored `.local/credentials.json`. The local admin email is `admin@example.test`; read its generated password privately from that file.

## Failure and concurrency behavior

- One persistent upstream MCP session is retained per server ID and registry revision. A dedicated asyncio worker owns the SDK session and processes its commands serially, so the SDK's AnyIO task group is always closed by its owning task. Different upstreams have independent workers, so one slow or dead server does not block the others.
- A periodic discovery check refreshes the PostgreSQL catalog. A failed probe preserves the last known catalog and marks that server unreachable; the gateway still lists other healthy servers. Calls to a known-unreachable server fail clearly. Disabling a server immediately removes its tools from `tools/list` and blocks direct calls.
- A timeout before tool dispatch is a known failure. A timeout or transport interruption after dispatch is `unknown_outcome`; writes are never replayed automatically. The caller must inspect the upstream state before retrying.
- Audit intent is committed before dispatch. If the gateway restarts with an `in_progress` audit row, startup marks it `unknown_outcome` because the side effect may have occurred.
- The demo deployment uses one gateway replica. Its rate limit is in-process and is not a global limit across replicas. Scale-out, shared rate limiting and metrics export are later production work.

The app lifespan starts the downstream MCP session manager and owns upstream worker shutdown. Registry changes queue a worker close command; they never close an SDK session from an unrelated dashboard request task. Discovery exceptions from the SDK can be wrapped in exception groups, so transport classification checks nested causes before updating health status.

## Dashboard chat

The `/api/chat` endpoint accepts a message and up to ten recent user/assistant text turns. The backend loads the enabled, permission-filtered tool catalog through its local Streamable HTTP `/mcp` endpoint, passes that catalog to an OpenAI-compatible Chat Completions API, and sends model-selected calls back through the same MCP endpoint. Tool results are returned to the model for a final answer. Every dispatched tool therefore receives the existing live permission/enablement checks and audit record. Chat makes no direct connection to WooCommerce, inventory or fulfillment.

The browser sends its normal dashboard session cookie and CSRF header; it never receives a gateway API key, upstream credential or model key. The gateway mints a two-minute signed assertion tied to the current database session, then `/mcp` revalidates that session and the current user on every chat request. The assertion has no API key ID in audit records. Chat history is bounded and kept only in browser memory. Tool audits retain the tool, caller, outcome and timing. Trace metadata includes generation/tool step names, route/model, token usage, pricing, duration and outcome. Optional content capture adds bounded heuristic-redacted user/assistant/tool previews; it is enabled only for local synthetic demo data and cleared after a short TTL. It never captures system prompts or hidden reasoning. Redaction is not a guarantee that PII is removed.

Configure an OpenAI-compatible endpoint and model in Helm values `gateway.llm.baseUrl` and `gateway.llm.model`. To use LiteLLM Auto Router, set `gateway.llm.routerModel` to its configured alias. Otherwise, optional `gateway.llm.standardModel` and `gateway.llm.complexModel` use the gateway's deterministic cross-server/multi-step rule. Set `gateway.llm.analysisModel` to select an analysis model alias; it uses the same server-side provider URL/key. The dashboard records the selected route and reason. Add `gateway.llm.modelPricing` with per-million-token input/output USD rates to enable estimates and investigations. Unknown models or missing usage remain explicitly unpriced; these rates are estimates, not an invoice. For local hosted providers that need a key, run `scripts/set-llm-secret.py`; it prompts without echo and stores the value at `urumi-gateway-app/llm-api-key`. For a VPS, create that key in the Secret through your deployment's secret manager. The current cluster has no provider configured, so `/api/chat/status` reports `configured: false` and chat returns HTTP 503 until URL and a model are supplied. The tool-loop code is provider-neutral; hosted OpenAI-compatible APIs and a reachable local inference server can use the same interface.

The loop limits each message to 4,000 characters, passes at most ten prior text turns, permits up to five model tool rounds and eight calls, bounds tool output sent back to the provider, applies provider and whole-turn deadlines, and does not retry failed tools. Upstream descriptions and output are treated as untrusted data in the system prompt; permissions remain enforced by the gateway even if a model ignores that instruction. `/api/observability/summary`, `/api/observability/traces` and `/api/observability/traces/{id}` provide bounded-window metrics and the safe step tree. Members see their own traces; admins see the workspace.

The admin-only **Investigations** page stores expected behavior and checks, snapshots up to the configured trace count per run, and enqueues a durable PostgreSQL job. The worker processes one provider call without tools, requires explicit model input/output prices, estimates a conservative preflight cost from UTF-8 byte count and message overhead against the per-run cap, and records returned usage/cost. Provider tokenizer and invoice differences mean this is a guardrail rather than a billing guarantee. Findings require a quote that exactly matches a selected trace or step and link back to it. The worker does not automatically repeat a provider request after interruption; its cost may be unknown. `POST /api/investigations`, `POST /api/investigations/{id}/runs`, `GET /api/investigation-runs/{id}`, and `PATCH /api/investigation-findings/{id}` are admin-only. Capture and model-backed analysis are deployment settings; see [ADR 005](../../docs/decisions/005-lens-inspired-investigations.md).

## Run locally

From the repository root:

```sh
./scripts/local-up.sh
./scripts/deploy-gateway.sh
```

Both scripts set `KUBECONFIG` to the repository's `.local/kubeconfig`. The deploy script builds/imports the gateway, dashboard and demo upstream images, deploys the WooCommerce MCP service if needed, and enables the relevant Helm workloads.

- Dashboard: `http://gateway.localhost:8080`
- MCP endpoint: `http://gateway.localhost:8080/mcp`
- Store: `http://store.localhost:8080`

Sign in with the local bootstrap credentials from `.local/credentials.json`. Invite a second user under **Team**, copy the one-time link privately, and complete account setup. Each user generates their own MCP key in **Team**. In MCP Inspector, select Streamable HTTP, enter the gateway URL and set `Authorization: Bearer <user-key>`.

Use **Servers** to inspect the three discovered catalogs, grant member tools, connect an additional HTTPS MCP server, refresh health, disable or remove a server. The **Assistant** page uses the logged-in dashboard account and displays the selected route and tools called; **Observability** shows token/cost/latency summaries and per-turn step traces; admins can use **Investigations** to run evidence-linked cross-trace analysis. Configure the provider URL/model and explicit prices in local Helm values and set its key Secret before running the chat/investigation demo. External clients still use their own per-user API key.

## Endpoints

Dashboard API endpoints are under `/api`: `/auth/login`, `/auth/me`, `/auth/logout`, `/overview`, `/servers`, `/members`, `/members/invitations`, `/invitations/accept`, `/keys`, `/audit`, `/chat/status`, `/chat`, `/observability/*`, and `/investigations/*`. Mutating dashboard calls require a valid session and CSRF token; investigation management requires the admin role. `/healthz` is liveness; `/readyz` checks PostgreSQL only, so an upstream outage does not make the gateway unready.

For chart configuration, Secret keys, local-to-VPS values and the current system boundaries, see [deployment.md](../../docs/deployment.md), [architecture.md](../../docs/architecture.md), and [threat-model.md](../../docs/threat-model.md).

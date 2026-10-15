# Team MCP Gateway

The gateway is a policy-enforcing MCP server and the only MCP client of registered upstreams. It exposes the dashboard API and one Streamable HTTP endpoint at `/mcp`.

## Components

- `app.py`: FastAPI lifecycle, revocable bearer-key middleware, request limits, health/readiness and background discovery.
- `api.py`: dashboard sessions, invitations, API keys, server registry, permissions and audit queries.
- `mcp_gateway.py`: dynamic `tools/list` and the single authorized, audited `tools/call` path.
- `upstreams.py`: MCP client sessions, URL checks, tool discovery, name mapping, per-server serialization, timeouts and routing.
- `models.py` / `migrations/`: PostgreSQL registry, user/key/session data, catalog snapshots and audit records.
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

- One persistent upstream MCP session is retained per server ID and registry revision. Calls and discovery for that server are serialized because MCP session concurrency behavior differs between upstreams. Different upstreams operate concurrently and have independent deadlines.
- A periodic discovery check refreshes the PostgreSQL catalog. A failed probe preserves the last known catalog and marks that server unreachable; the gateway still lists other healthy servers. Calls to a known-unreachable server fail clearly. Disabling a server immediately removes its tools from `tools/list` and blocks direct calls.
- A timeout before tool dispatch is a known failure. A timeout or transport interruption after dispatch is `unknown_outcome`; writes are never replayed automatically. The caller must inspect the upstream state before retrying.
- Audit intent is committed before dispatch. If the gateway restarts with an `in_progress` audit row, startup marks it `unknown_outcome` because the side effect may have occurred.
- The demo deployment uses one gateway replica. Its rate limit is in-process and is not a global limit across replicas. Scale-out, shared rate limiting and metrics export are later production work.

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

Use **Servers** to inspect the three discovered catalogs, grant member tools, connect an additional HTTPS MCP server, refresh health, disable or remove a server. The **Activity** page shows permitted calls and rejections. The assistant page intentionally indicates the next milestone; the chat-to-gateway LLM loop has not been added in this gateway phase.

## Endpoints

Dashboard API endpoints are under `/api`: `/auth/login`, `/auth/me`, `/auth/logout`, `/overview`, `/servers`, `/members`, `/members/invitations`, `/invitations/accept`, `/keys`, and `/audit`. Mutating dashboard calls require a valid session and CSRF token; server/member management also requires the admin role. `/healthz` is liveness; `/readyz` checks PostgreSQL only, so an upstream outage does not make the gateway unready.

For chart configuration, Secret keys, local-to-VPS values and the current system boundaries, see [deployment.md](../../docs/deployment.md), [architecture.md](../../docs/architecture.md), and [threat-model.md](../../docs/threat-model.md).

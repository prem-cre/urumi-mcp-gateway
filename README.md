# Urumi Team MCP Gateway

A Python MCP gateway and React dashboard for a Kubernetes-hosted WooCommerce store. This repository is being built for the Urumi AI FDE Internship assessment.

## Current status

The store, WooCommerce MCP adapter and team gateway control plane/data plane are implemented. The gateway aggregates WooCommerce plus two sample MCP sources through one Streamable HTTP endpoint. It supports admin/member accounts, invitations, individually hashed/revocable API keys, member tool grants, server registry controls, health discovery and attributed audit logs. The live dashboard uses the gateway API.

The dashboard chat/LLM tool loop, first-party Observability page and an on-demand, Lens-inspired trace investigation workflow are implemented. Chat tool calls use the same authenticated MCP gateway path; traces include model/tool steps, token usage, configured-rate cost estimates, latency and model-routing explanations. Admins can define expected behavior and checks, analyze a bounded sample of redacted previews, and review findings linked to source traces. Local values enable preview capture for synthetic demo data; production values disable it. The local cluster currently has no model provider configured, so real usage, cost, routing and investigation runs remain unverified; the store-order demo flow and VPS deployment also remain. Local gateway verification has exercised three-source routing, member permissions, invitations, key revocation, disable filtering, dead-upstream isolation and NetworkPolicy isolation. The architecture and threat model distinguish implemented controls from planned controls.

## Local setup

Prerequisites: Docker Desktop, Helm, k3d, kubectl, Python 3.12 and `uv`.

```sh
uv python install 3.12
uv sync --locked
./scripts/local-up.sh
./scripts/deploy-gateway.sh
```

The scripts use only `.local/kubeconfig`. `deploy-gateway.sh` builds/imports gateway, dashboard and demo upstream images, ensures the WooCommerce MCP service is deployed, and enables the chart workloads.

- Store: [http://store.localhost:8080](http://store.localhost:8080)
- Dashboard: [http://gateway.localhost:8080](http://gateway.localhost:8080)
- MCP endpoint: `http://gateway.localhost:8080/mcp`

If `.localhost` does not resolve in your browser, add `127.0.0.1 store.localhost gateway.localhost` to your local hosts file. Local generated credentials are in ignored `.local/credentials.json`; read them privately and never commit or record that file. The bootstrap admin email is `admin@example.test`.

For the local gateway demo, sign in as the bootstrap admin, invite a second user under **Team**, complete their one-time invite, and generate a separate MCP key from each account. In MCP Inspector, select Streamable HTTP, configure the gateway URL and set `Authorization: Bearer <user-key>`. Never use a real customer or payment credential in the demo store.

The **Assistant** view sends model-selected calls through the same `/mcp` route, using the logged-in user session and showing the called tools and selected route. Configure its provider URL/model profiles, optional pricing map and Kubernetes Secret key using the chat setup section in [deployment instructions](docs/deployment.md). **Observability** shows team-scoped model usage and estimated costs, latency and ordered traces. Admins can use **Investigations** to compare captured traces with expected behavior, review evidence-backed findings and mark behavior expected/resolved. Content capture is heuristic-redacted and still sensitive; use synthetic demo data. The **Servers** view shows WooCommerce, inventory and fulfillment status/catalogs, grants, and admin controls to register, refresh, enable, disable and remove upstreams. **Team** handles invitations and keys. **Activity** shows outcomes and call duration. Each MCP tool call rechecks live identity, server enablement and grants, and audit intent is committed before dispatch.

See [gateway operations](services/gateway/README.md) for the MCP request path and endpoints. See [deployment instructions](docs/deployment.md) for checkout, Kubernetes resources, local domains and the production-like Helm values path.

## Architecture and decisions

- [System design and reference tradeoffs](docs/architecture.md)
- [ADR 001: platform foundation](docs/decisions/001-foundation.md)
- [ADR 002: WooCommerce MCP adapter](docs/decisions/002-woocommerce-mcp.md)
- [ADR 003: team MCP gateway](docs/decisions/003-team-gateway.md)
- [ADR 004: chat observability and routing](docs/decisions/004-chat-observability-and-routing.md)
- [ADR 005: Lens-inspired investigations](docs/decisions/005-lens-inspired-investigations.md)
- [LiteLLM Lens product and codebase study](docs/research/litellm-lens-reference.md)
- [Scope and assessment checklist](docs/scope.md)
- [Threat model](docs/threat-model.md)
- [Local/VPS setup, upgrades and rollback](docs/deployment.md)
- [Foundation verification evidence](docs/verification.md)
- [WooCommerce MCP verification evidence](docs/verification-mcp.md)

## Repository layout

```text
apps/dashboard/             React + TypeScript dashboard
services/gateway/           Python API, identity, MCP aggregation, policy and audit
services/demo_mcp/          Read-only sample inventory and fulfillment servers
services/woocommerce_mcp/    Own authenticated WooCommerce MCP adapter
charts/platform/             Helm workloads, ingress, storage, Secrets and NetworkPolicies
migrations/                  PostgreSQL Alembic migrations
scripts/                     Local cluster, secret and service deployment helpers
docs/                        Architecture, tradeoffs, scope and security notes
```

## Checks

```sh
make check
uv run pytest services/gateway/tests -q
```

`make check` runs Ruff formatting/lint and Helm lint/rendering for local and production-like values. `make verify` checks the local store; `make checkout` creates a real dummy COD order. `./scripts/verify-network.sh` checks local network isolation. These runtime checks require the dedicated `.local/kubeconfig`; `make checkout` creates a store order.

The gateway image applies Alembic migrations in an init container before application startup. PostgreSQL data and WordPress content/database data use persistent volumes. Helm rollback restores manifests, not database data/schema. The local cluster and production values describe a single-node demonstration deployment, not high availability.

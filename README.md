# Urumi MCP Gateway

A Python team MCP gateway with a React dashboard, a tool-using chat agent and a Kubernetes-hosted WooCommerce store. Built for the Urumi AI FDE Internship assessment.

## Current status

The repository/system design and Kubernetes store foundation are implemented. The store uses WordPress + WooCommerce, MariaDB and persistent content/database volumes. PostgreSQL is deployed for the upcoming gateway. Helm defines local and production-like values, probes, resources, ingress and network boundaries.

The Python gateway, WooCommerce MCP server, team dashboard and chat are the next phases. They are not available yet. The production values are a deployment configuration, not evidence of a live VPS deployment.

## Start locally

Prerequisites: running Docker Desktop, k3d, kubectl, Helm and uv.

```sh
brew install helm k3d
uv python install 3.12
uv sync --locked
./scripts/local-up.sh
export KUBECONFIG="$PWD/.local/kubeconfig"
kubectl -n urumi get pods,pvc,ingress
```

- Store: **http://store.localhost:8080/**
- WordPress admin: **http://store.localhost:8080/wp-admin/**
- Admin credentials: ignored, private `.local/credentials.json`.

The initialization Job installs pinned WooCommerce, creates a demo notebook, enables free shipping and cash on delivery, and can be rerun. Follow [deployment instructions](docs/deployment.md) to place a real demo order and inspect it in admin. This is a development store with dummy data and no real payments.

```sh
# Verify the storefront and visitor cart:
uv run python scripts/verify-store.py
# Explicitly create a real demo order through WooCommerce visitor checkout:
uv run python scripts/verify-store.py --checkout
```

No LLM key or external account is needed for this phase. Credentials are generated separately from Helm; never commit `.local`, `.env`, kubeconfigs or Secrets.

## Design and decisions

- [System design](docs/architecture.md): gateway flow, sessions, tool aggregation, permissions, audit, failure handling and scaling.
- [Architecture decision record](docs/decisions/001-foundation.md): Python, database split, k3d/k3s, secrets and scope.
- [Scope and demo checklist](docs/scope.md): required features and implementation sequence.
- [Threat model](docs/threat-model.md): implemented boundaries, planned controls and current limitations.
- [Local/VPS setup and rollback](docs/deployment.md): same chart with environment values; backup requirements.
- [Foundation verification](docs/verification.md): evidence collected from the running cluster.

## Layout

```text
apps/dashboard/             React dashboard boundary (implementation follows)
services/gateway/           Python gateway/auth/audit/chat boundary
services/woocommerce_mcp/    Own WooCommerce MCP server boundary
charts/platform/            Helm chart, local/prod values and Kubernetes templates
infra/local/                Dedicated k3d cluster configuration
scripts/                    Secrets, deployment and visitor checkout verification
docs/                       Design, decisions, scope, security and evidence
```

## Planned end-to-end behavior

An admin connects three upstream MCP servers once. Two users reach them through a single gateway endpoint or the dashboard chat. API keys are hashed and revocable; upstream credentials stay server-side. Every tool call follows the same authorization and audit path. Disabled servers disappear from discovery and a dead upstream does not prevent calls to healthy servers.

External MCP client setup and dashboard chat instructions will be added when those services work. Optional multi-store provisioning starts only after the mandatory two-user flow passes.

# Local and VPS deployment

## Local prerequisites

Docker Desktop must be running. Install Helm, k3d, kubectl, Docker BuildKit and uv. The foundation was developed on Apple Silicon/macOS; cluster images support that architecture. Python dependencies use `uv sync --locked` and Python 3.12. An OpenAI-compatible model endpoint is optional for deploying the store and gateway, and is required to run dashboard chat.

```sh
brew install helm k3d
uv python install 3.12
uv sync --locked
./scripts/local-up.sh
export KUBECONFIG="$PWD/.local/kubeconfig"
kubectl -n urumi get pods,pvc,ingress
```

The script creates a dedicated `urumi` k3d cluster, one server and one agent, Traefik ingress, generated Secrets, and the `urumi` Helm release. It does not install into the previously selected cluster. It binds port 8080 for HTTP and 8443 for future TLS. Local credentials are generated with secure randomness, written at mode 0600 in ignored `.local/credentials.json`, and sent to kubectl through stdin rather than command-line arguments. Existing Kubernetes Secrets are retained on reruns. No Secrets are rendered by Helm.

Open `http://store.localhost:8080/` and `http://store.localhost:8080/wp-admin/`. Chromium browsers resolve `.localhost` names to loopback. If another browser or resolver does not, add `127.0.0.1 store.localhost` to your host file. curl can avoid DNS entirely:

```sh
curl --resolve store.localhost:8080:127.0.0.1 http://store.localhost:8080/
```

Read admin username/password from `.local/credentials.json` privately. Do not paste that file into issues, logs, recordings or commits. The bootstrap creates WooCommerce pages, one INR 299 product (`URUMI-DEMO-001`), free demo shipping, guest checkout and COD. This is a demonstration store; do not process real customer data or payments.

## Place and inspect an order

1. Open the shop and add **Urumi Demo Notebook** to the cart.
2. Proceed to checkout, enter dummy billing/shipping details, select Cash on delivery and place the order.
3. Open WooCommerce admin > Orders with the local admin credentials. Confirm the item, total and order status.

The checkout page and admin are real WooCommerce, not gateway mock data. A purchase creates an order in MariaDB. Outgoing email is not configured; use WooCommerce admin to inspect orders.

## Deploy the gateway locally

After `local-up.sh`, run:

```sh
./scripts/deploy-gateway.sh
```

This builds and imports the gateway, dashboard and sample inventory/fulfillment MCP images into k3d. It provisions the WooCommerce MCP credentials if that service is not already installed, then enables all gateway workloads in the same Helm release. The chart runs the Alembic migration in a gateway init container before starting the API. Check the deployment using the dedicated context:

```sh
KUBECONFIG="$PWD/.local/kubeconfig" kubectl -n urumi get pods,pvc,ingress
```

Dashboard: `http://gateway.localhost:8080`. MCP URL: `http://gateway.localhost:8080/mcp`. Read the generated admin password from `.local/credentials.json`. Invite a second account and use each account's own key for the external MCP client. The local chart's generated DB and app Secrets are `urumi-gateway-db` and `urumi-gateway-app`; the dedicated WooCommerce MCP runtime uses `urumi-woocommerce-mcp`.

The chat loop uses an OpenAI-compatible Chat Completions endpoint. The model URL and name are non-secret deployment settings. `scripts/deploy-gateway.sh` loads `.local/urumi.local.env` automatically when present; otherwise pass model settings in the shell and use the hidden prompt for the provider key:

```sh
URUMI_LLM_BASE_URL=https://api.example.com/v1 \
URUMI_LLM_MODEL=your-model-name \
./scripts/deploy-gateway.sh
```

Optionally set `URUMI_LLM_ROUTER_MODEL` to delegate model selection to a LiteLLM Auto Router alias. Otherwise, `URUMI_LLM_STANDARD_MODEL` and `URUMI_LLM_COMPLEX_MODEL` configure this gateway's transparent two-tier heuristic. Explicit cross-server or multi-step prompts select the complex profile. Set `URUMI_LLM_ANALYSIS_MODEL` to choose an investigation model; it uses the same provider URL and server-side key as chat. Set `gateway.llm.providerName` to a display label. Define `gateway.llm.modelPricing` in values with `input_per_million` and `output_per_million` USD rates. The Observability page combines response token counts with those rates to show estimates; absent or mismatched prices are marked unpriced. Investigations require explicit model prices so they can apply a conservative estimated per-run cap.

The script prompts for a key without echo and applies it as `llm-api-key` in `urumi-gateway-app`. On this local checkout, `scripts/deploy-gateway.sh` sources `.local/urumi.local.env` if present; that owner-only (`0600`), gitignored file can hold local model and bootstrap credentials and is never committed. The admin email in that file is passed as a Helm value; its password remains in `.local/credentials.json` and the Kubernetes Secret. A VPS deployment supplies the model key and bootstrap admin password through its secret manager, not a developer env file.

Each chat turn creates an admin/member-scoped trace with one observation per model generation and tool call. Trace metadata contains model, route reason, provider-reported token usage, configured-price estimates, latency, outcomes and safe error categories. Content previews are opt-in deployment settings: local values enable bounded heuristic-redacted previews for synthetic demo data, production values keep capture disabled, and previews are cleared within five minutes after the configured TTL (7 days by default). Redaction can miss personal data; do not enable content capture for real customer data before reviewing consent, encryption and deletion controls. Analysis sends only the selected redacted previews to the configured model. It has no tools and must return evidence linked to exact captured steps. Investigation runs/findings are admin-only, on-demand, capped by sample size, output tokens and a conservative byte-based preflight estimate; provider tokenization and invoice amounts can differ. Run metadata and findings do not yet have a separate retention policy. The Observability page summarizes a selectable 1, 7 or 30 day window; high-volume production use needs external trace storage and retention/alerting policies. Prometheus metrics are exposed on the gateway ClusterIP at `/metrics`, not through Ingress. Scraper pods are allowed only when `gateway.observability.metricsNamespaceSelectorLabels` and `metricsPodSelectorLabels` are configured. Optional OIDC uses `gateway.oidc.issuerUrl`, `clientId` and `redirectUrl`, with an optional client secret in the gateway Secret under `oidc-client-secret`; only existing active accounts with verified email can use it.

## What is installed

| Resource | Exposure / persistence |
| --- | --- |
| WordPress Deployment | one replica, Recreate, HTTP Service and store Ingress; content PVC |
| MariaDB StatefulSet | internal headless Service; data PVC |
| PostgreSQL StatefulSet | internal headless Service; separate persistent data PVC for identities, registry, catalog and audits |
| Gateway Deployment | one replica, internal ClusterIP; `/api`, `/mcp`, liveness and readiness are exposed on the dashboard host through Ingress |
| Dashboard Deployment | rootless static React workload; same host and ingress as the gateway |
| Inventory/Fulfillment MCP Deployments | rootless, read-only sample services; ClusterIP and gateway-only ingress policy |
| Bootstrap Job | bounded 15-minute initialization, UID 33, content PVC, version-pinned WooCommerce |
| Secrets | database and WordPress admin credentials; existing Secret references in values |
| NetworkPolicies | default deny for stack pods, explicit DNS/ingress/database/bootstrap paths |

All long-running containers have startup, readiness and liveness probes and resource requests/limits. Completed initialization Jobs do not have server probes; Job completion and activeDeadlineSeconds govern them.

The standard WordPress/MariaDB/PostgreSQL images initialize files with their default entrypoints; these are not claimed as wholly non-root deployments. Gateway, dashboard, WooCommerce MCP and sample MCP containers run as non-root, with read-only root filesystems and dropped capabilities. Service-account tokens are not mounted into these workloads.

Local PVCs use k3s local-path storage. Pod restarts preserve data. A bind mount on the agent maps its storage into `.local/storage` on the host; storage on the server node remains in that Docker node. Never delete the cluster as a substitute for restarting pods: cluster deletion can lose volume metadata and server-node data. This setup is not a backup strategy or replicated storage.

## Reruns and diagnosis

`./scripts/local-up.sh` is safe to rerun: it retains Secrets and volumes and checks existing WordPress/product state. Each Helm revision creates a fresh bootstrap Job. A different existing WooCommerce version triggers an explicit error rather than silently changing database schemas.

```sh
export KUBECONFIG="$PWD/.local/kubeconfig"
kubectl -n urumi get pods
kubectl -n urumi get events --sort-by=.lastTimestamp
kubectl -n urumi logs deployment/urumi-wordpress
kubectl -n urumi logs statefulset/urumi-mariadb
kubectl -n urumi get jobs
# Substitute the revision returned above:
kubectl -n urumi logs job/urumi-bootstrap-1
```

Local NetworkPolicies are enforced by the k3s network-policy controller. Inspect denied connections with an unlabeled test pod; adding a Service without ingress does not itself prove gateway-only access.

## Production-like single-node k3s

This is the deployment path and configurable values, not a claim that a VPS has already been provisioned. Install a supported, pinned k3s release on your VPS and ensure Traefik and a NetworkPolicy-capable network configuration run. Keep database ports private; expose only HTTP/HTTPS as needed. Configure DNS for the store host.

Use the same chart with `values-prod.yaml`, setting the actual host, URL, storage class, registry image references and TLS Secrets. Enable `woocommerceMcp.enabled`, `demoMcp.enabled`, `gateway.enabled` and `dashboard.enabled` as appropriate. Supply `urumi-store`, `urumi-woocommerce-mcp`, `urumi-gateway-db` and `urumi-gateway-app` Secrets through your deployment process; do not use the local secret script against a VPS. TLS Secrets `urumi-store-tls` and `urumi-gateway-tls` must exist before ingress TLS works; cert-manager/certificate issuance is not configured. Use secure HTTPS URLs for production store and upstreams.

```sh
# Set the kubeconfig to the intended VPS cluster before executing these commands.
# Create the namespace, credential Secrets and TLS Secret separately.
helm upgrade --install urumi charts/platform -n urumi --create-namespace \
  -f charts/platform/values-prod.yaml \
  --set store.host=store.YOUR_DOMAIN \
  --set store.url=https://store.YOUR_DOMAIN \
  --set gateway.dashboardHost=gateway.YOUR_DOMAIN \
  --set woocommerceMcp.enabled=true \
  --set demoMcp.enabled=true \
  --set gateway.enabled=true \
  --set dashboard.enabled=true \
  --wait --wait-for-jobs --timeout 20m
```

Host, URL, ingress class/TLS, storage class, Secret references, images and resource sizes are configuration changes only. Use images pushed to a registry reachable by the VPS; local `k3d image import` tags are not available there. Bootstrap administration changes should be deliberate: existing admin accounts are not silently reset. This is a production-like deployment recipe, not evidence of a live VPS install or HA service.

## Upgrades, rollback and backups

Record Helm history and back up MariaDB plus WordPress files before upgrades. Pin image versions (and digests for a production release). Inspect rendered manifests before applying. The chart's bootstrap seed is idempotent; it does not guarantee safe plugin schema downgrade.

```sh
helm -n urumi history urumi
helm upgrade urumi charts/platform -n urumi -f charts/platform/values-local.yaml \
  --wait --wait-for-jobs --timeout 20m
# Use a verified previous revision; no implicit database rollback:
helm rollback urumi PREVIOUS_REVISION -n urumi --wait --timeout 10m
```

Helm rollback restores Kubernetes manifests, not MariaDB/PostgreSQL schema or WordPress content. A plugin or database schema change needs a tested restore path. Single-node local-path volumes are not HA. Production should add off-node backups with restore rehearsals before handling real orders. The backup/restore automation and real VPS deployment are later work.

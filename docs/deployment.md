# Local and VPS deployment

## Local prerequisites

Docker Desktop must be running. Install Helm, k3d, kubectl and uv. The foundation was developed on Apple Silicon/macOS; cluster images support that architecture. Python dependencies use `uv sync --locked` and Python 3.12. No LLM account is required for the store foundation.

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

The checkout page and admin are real WooCommerce, not gateway mock data. A purchase creates an order in MariaDB. The gateway services and dashboard do not run yet.

## What is installed

| Resource | Exposure / persistence |
| --- | --- |
| WordPress Deployment | one replica, Recreate, HTTP Service and store Ingress; content PVC |
| MariaDB StatefulSet | internal headless Service; data PVC |
| PostgreSQL StatefulSet | internal headless Service; separate data PVC, ready for future gateway |
| Bootstrap Job | bounded 15-minute initialization, UID 33, content PVC, version-pinned WooCommerce |
| Secrets | database and WordPress admin credentials; existing Secret references in values |
| NetworkPolicies | default deny for stack pods, explicit DNS/ingress/database/bootstrap paths |

All long-running containers have startup, readiness and liveness probes and resource requests/limits. Completed initialization Jobs do not have server probes; Job completion and activeDeadlineSeconds govern them.

The standard WordPress/MariaDB/PostgreSQL images initialize files with their default entrypoints; these are not claimed as wholly non-root deployments. Custom Python services will be hardened separately. Service-account tokens are not mounted into these workloads.

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

Use the same chart with `values-prod.yaml`, setting the actual host, URL, storage class and TLS Secret. Supply `urumi-store` and `urumi-gateway-db` Secrets through your deployment process with the keys documented in `scripts/create-secrets.py`. TLS Secret `urumi-store-tls` must exist before ingress TLS works; this phase does not install cert-manager or obtain certificates. Use secure HTTPS URLs for production API credentials.

```sh
# Set the kubeconfig to the intended VPS cluster before executing these commands.
# Create the namespace, credential Secrets and TLS Secret separately.
helm upgrade --install urumi charts/platform -n urumi --create-namespace \
  -f charts/platform/values-prod.yaml \
  --set store.host=store.YOUR_DOMAIN \
  --set store.url=https://store.YOUR_DOMAIN \
  --wait --wait-for-jobs --timeout 20m
```

Host, URL, ingress class/TLS, storage class, Secret references, images and resource sizes are configuration changes only. Bootstrap administration changes should be deliberate: existing admin accounts are not silently reset.

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

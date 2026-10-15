#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export KUBECONFIG="$PWD/.local/kubeconfig"
test -f "$KUBECONFIG" || { echo "Missing .local/kubeconfig; run scripts/local-up.sh first." >&2; exit 1; }

python3 scripts/create-secrets.py
if ! kubectl -n urumi get deployment urumi-woocommerce-mcp >/dev/null 2>&1; then
  ./scripts/deploy-mcp.sh
fi

tag="dev-$(date +%s)"
gateway_image="urumi/gateway:$tag"
demo_image="urumi/demo-mcp:$tag"
dashboard_image="urumi/dashboard:$tag"
docker build -f services/gateway/Dockerfile -t "$gateway_image" .
docker build -f services/demo_mcp/Dockerfile -t "$demo_image" .
docker build -f apps/dashboard/Dockerfile -t "$dashboard_image" .
k3d image import "$gateway_image" -c urumi
k3d image import "$demo_image" -c urumi
k3d image import "$dashboard_image" -c urumi

helm upgrade --install urumi charts/platform -n urumi \
  -f charts/platform/values-local.yaml \
  --set woocommerceMcp.enabled=true \
  --set demoMcp.enabled=true \
  --set gateway.enabled=true \
  --set dashboard.enabled=true \
  --set-string woocommerceMcp.image="urumi/woocommerce-mcp:$(kubectl -n urumi get deployment urumi-woocommerce-mcp -o jsonpath='{.spec.template.spec.containers[0].image}' | cut -d: -f2-)" \
  --set-string demoMcp.image="$demo_image" \
  --set-string gateway.image="$gateway_image" \
  --set-string dashboard.image="$dashboard_image" \
  --wait --wait-for-jobs --timeout 10m

kubectl -n urumi rollout status deployment/urumi-gateway --timeout=180s
kubectl -n urumi rollout status deployment/urumi-inventory-mcp --timeout=180s
kubectl -n urumi rollout status deployment/urumi-fulfillment-mcp --timeout=180s
kubectl -n urumi rollout status deployment/urumi-dashboard --timeout=180s
echo "Gateway: http://gateway.localhost:8080"
echo "MCP endpoint: http://gateway.localhost:8080/mcp"

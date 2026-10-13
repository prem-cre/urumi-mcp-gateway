#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export KUBECONFIG="$PWD/.local/kubeconfig"
kubectl -n urumi rollout status deployment/urumi-wordpress --timeout=180s
python3 scripts/provision-mcp-secrets.py
image="urumi/woocommerce-mcp:dev-$(date +%s)"
docker build -f services/woocommerce_mcp/Dockerfile -t "$image" .
k3d image import "$image" -c urumi
helm upgrade --install urumi charts/platform -n urumi \
  -f charts/platform/values-local.yaml --set woocommerceMcp.enabled=true --set-string woocommerceMcp.image="$image" \
  --wait --wait-for-jobs --timeout 20m

kubectl -n urumi rollout status deployment/urumi-woocommerce-mcp --timeout=180s

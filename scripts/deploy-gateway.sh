#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export KUBECONFIG="$PWD/.local/kubeconfig"
test -f "$KUBECONFIG" || { echo "Missing .local/kubeconfig; run scripts/local-up.sh first." >&2; exit 1; }
local_env="$PWD/.local/urumi.local.env"
if [[ -f "$local_env" ]]; then
  chmod 600 "$local_env"
  set -a
  # This ignored, owner-only file is created locally for development credentials.
  source "$local_env"
  set +a
fi

python3 scripts/create-secrets.py
if [[ -n "${URUMI_LLM_API_KEY:-}" ]]; then
  python3 scripts/set-llm-secret.py
fi
if ! kubectl -n urumi get deployment urumi-woocommerce-mcp >/dev/null 2>&1; then
  ./scripts/deploy-mcp.sh
fi

tag="dev-$(date +%s)"
gateway_image="urumi/gateway:$tag"
demo_image="urumi/demo-mcp:$tag"
dashboard_image="urumi/dashboard:$tag"
# Keep one sentinel element so Bash with `set -u` can safely expand the array
# when no command-line LLM overrides are provided. Slice it off at the Helm call.
llm_args=("")
admin_args=("")
if [[ -n "${URUMI_ADMIN_EMAIL:-}" ]]; then
  admin_args+=(--set-string "gateway.adminEmail=$URUMI_ADMIN_EMAIL")
fi
if [[ -n "${URUMI_LLM_BASE_URL:-}" || -n "${URUMI_LLM_MODEL:-}" || -n "${URUMI_LLM_ROUTER_MODEL:-}" || -n "${URUMI_LLM_STANDARD_MODEL:-}" || -n "${URUMI_LLM_COMPLEX_MODEL:-}" || -n "${URUMI_LLM_ANALYSIS_MODEL:-}" ]]; then
  [[ -n "${URUMI_LLM_BASE_URL:-}" ]] || {
    echo "Set URUMI_LLM_BASE_URL when configuring a model, or leave all model variables empty." >&2
    exit 1
  }
  [[ -n "${URUMI_LLM_MODEL:-}${URUMI_LLM_ROUTER_MODEL:-}${URUMI_LLM_STANDARD_MODEL:-}${URUMI_LLM_COMPLEX_MODEL:-}${URUMI_LLM_ANALYSIS_MODEL:-}" ]] || {
    echo "Set a chat model/profile or URUMI_LLM_ANALYSIS_MODEL." >&2
    exit 1
  }
  llm_args+=(--set-string "gateway.llm.baseUrl=$URUMI_LLM_BASE_URL")
  llm_args+=(--set-string "gateway.llm.model=${URUMI_LLM_MODEL:-}")
  llm_args+=(--set-string "gateway.llm.routerModel=${URUMI_LLM_ROUTER_MODEL:-}")
  llm_args+=(--set-string "gateway.llm.standardModel=${URUMI_LLM_STANDARD_MODEL:-}")
  llm_args+=(--set-string "gateway.llm.complexModel=${URUMI_LLM_COMPLEX_MODEL:-}")
  llm_args+=(--set-string "gateway.llm.analysisModel=${URUMI_LLM_ANALYSIS_MODEL:-}")
  if [[ -n "${URUMI_LLM_PROVIDER_NAME:-}" ]]; then
    llm_args+=(--set-string "gateway.llm.providerName=$URUMI_LLM_PROVIDER_NAME")
  fi
fi
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
  "${llm_args[@]:1}" \
  "${admin_args[@]:1}" \
  --wait --wait-for-jobs --timeout 10m

kubectl -n urumi rollout status deployment/urumi-gateway --timeout=180s
kubectl -n urumi rollout status deployment/urumi-inventory-mcp --timeout=180s
kubectl -n urumi rollout status deployment/urumi-fulfillment-mcp --timeout=180s
kubectl -n urumi rollout status deployment/urumi-dashboard --timeout=180s
echo "Gateway: http://gateway.localhost:8080"
echo "MCP endpoint: http://gateway.localhost:8080/mcp"

#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in docker k3d kubectl helm python3; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
done
docker info >/dev/null
mkdir -p .local/storage
export URUMI_DATA_DIR="$(pwd)/.local/storage"
if ! k3d cluster list -o json | python3 -c 'import json,sys; sys.exit(not any(c["name"]=="urumi" for c in json.load(sys.stdin)))'; then
  k3d cluster create --config infra/local/k3d.yaml --kubeconfig-update-default=false --kubeconfig-switch-context=false
fi
export KUBECONFIG="$(pwd)/.local/kubeconfig"
k3d kubeconfig get urumi > "$KUBECONFIG"
chmod 600 "$KUBECONFIG"
kubectl wait --for=condition=Ready nodes --all --timeout=180s
python3 scripts/create-secrets.py
helm upgrade --install urumi charts/platform -n urumi \
  -f charts/platform/values-local.yaml --reuse-values --wait --wait-for-jobs --timeout 20m
kubectl -n urumi get pods,pvc,ingress

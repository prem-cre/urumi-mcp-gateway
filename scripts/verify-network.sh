#!/usr/bin/env bash
# Test real isolation against ready endpoints; DNS failure is not a passing denial.
set -euo pipefail
cd "$(dirname "$0")/.."
export KUBECONFIG="$PWD/.local/kubeconfig"
probe=urumi-network-verification
cleanup() { kubectl -n urumi delete pod "$probe" --ignore-not-found --wait=false >/dev/null; }
trap cleanup EXIT
kubectl -n urumi wait --for=condition=Ready pod -l 'instance=urumi,app in (wordpress,mariadb,postgres)' --timeout=120s
python3 - <<'PY' | kubectl create -f -
import json
command = '''
for entry in urumi-mariadb:3306 urumi-postgres:5432 urumi-wordpress:80 urumi-woocommerce-mcp:8000; do
  host="${entry%:*}.urumi.svc.cluster.local"
  port="${entry##*:}"
  if [ "$entry" = "urumi-woocommerce-mcp:8000" ] && ! nslookup "$host" >/dev/null 2>&1; then
    echo "SKIP MCP Service not deployed"; continue
  fi
  nslookup "$host"
  if nc -z -w 3 "$host" "$port"; then
    echo "FAIL unexpected access: $entry"; exit 1
  else
    echo "PASS denied: $entry (DNS resolved)"
  fi
done
'''
print(json.dumps({
  'apiVersion': 'v1', 'kind': 'Pod',
  'metadata': {'name': 'urumi-network-verification', 'namespace': 'urumi'},
  'spec': {
    'restartPolicy': 'Never', 'automountServiceAccountToken': False,
    'securityContext': {'runAsUser': 65534, 'runAsNonRoot': True,
                        'seccompProfile': {'type': 'RuntimeDefault'}},
    'containers': [{
      'name': 'probe', 'image': 'busybox:1.37.0', 'command': ['sh', '-ec', command],
      'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
      'resources': {'requests': {'cpu': '10m', 'memory': '16Mi'},
                    'limits': {'cpu': '100m', 'memory': '32Mi'}},
    }],
  },
}))
PY
for attempt in $(seq 1 45); do
  phase=$(kubectl -n urumi get pod "$probe" -o jsonpath='{.status.phase}')
  if [ "$phase" = Succeeded ]; then kubectl -n urumi logs "$probe"; exit 0; fi
  if [ "$phase" = Failed ]; then kubectl -n urumi logs "$probe"; exit 1; fi
  sleep 2
done
echo "Network verification timed out" >&2
exit 1

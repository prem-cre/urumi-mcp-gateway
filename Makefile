.PHONY: setup up status check verify checkout isolation

setup:
	uv python install 3.12
	uv sync --locked

up:
	./scripts/local-up.sh

status:
	KUBECONFIG="$(CURDIR)/.local/kubeconfig" kubectl -n urumi get pods,pvc,ingress

check:
	uv run ruff check scripts services
	uv run ruff format --check scripts services
	helm lint charts/platform
	helm template urumi charts/platform -f charts/platform/values-local.yaml >/dev/null
	helm template urumi charts/platform -f charts/platform/values-prod.yaml >/dev/null

verify:
	uv run python scripts/verify-store.py

# Creates an actual COD test order with dummy buyer data.
checkout:
	uv run python scripts/verify-store.py --checkout

isolation:
	./scripts/verify-network.sh

.PHONY: mcp-up mcp-test
mcp-up:
	./scripts/deploy-mcp.sh

mcp-test:
	uv run pytest services/woocommerce_mcp/tests -q

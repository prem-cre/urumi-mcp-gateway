# Project instructions

Read `docs/architecture.md`, `docs/decisions/001-foundation.md`, and `docs/scope.md` before changing the system design. Update the decision record when changing a boundary or deployment assumption.

- Backend and MCP services: Python 3.12, official MCP SDK, FastAPI. Use `uv` and keep `uv.lock` reproducible.
- Dashboard: React with TypeScript. Gateway data: PostgreSQL. WordPress data: MariaDB.
- Helm is mandatory; local and VPS differences belong in values, not forked application code.
- The gateway is the only client of upstream MCP servers. Browser code must never contain upstream or LLM credentials.
- Authentication, current revocation/enablement checks, permissions and audits apply to both chat and external MCP calls.
- Never automatically replay writes after a timeout or claim exactly-once execution.
- Use the dedicated `.local/kubeconfig` for local commands. Do not rely on an unrelated current kubectl context.
- Do not commit generated credentials, Secrets, `.env`, kubeconfigs or local storage. Do not print secrets during verification.
- Distinguish planned controls from deployed/verified behavior. A single-node store is not high availability; Helm rollback is not a data rollback.
- Mandatory core and a reproducible two-user demonstration precede optional multi-store provisioning.

Checks for this foundation: `make check`, `make verify`, and `./scripts/verify-network.sh`. `make checkout` explicitly creates a real COD demo order. To confirm an order through its authenticated admin page, run `uv run python scripts/verify-store.py --admin-order ORDER_ID`.

# ADR 001: implementation and deployment foundation

Accepted 12 October 2026.

1. Python 3.12 for gateway, WooCommerce MCP and the chat backend; official MCP SDK for protocol, FastAPI for administration. React/TypeScript/Vite for dashboard. User preference plus one language across backend services.
2. PostgreSQL for platform data; MariaDB for WordPress. WooCommerce supported REST APIs hide storage internals and HPOS differences.
3. k3d locally (k3s in Docker), k3s on the VPS, Traefik Ingress and Helm in both. This minimizes environment differences and uses k3s's NetworkPolicy controller. A dedicated kubeconfig prevents accidental deployment to another context.
4. Stateless downstream MCP with independently owned upstream sessions. One replica initially; no unsupported HA claim.
5. Separate secrets from values. Dynamic upstream credentials will be encrypted in PostgreSQL with a Kubernetes-secret encryption key; no cluster-wide Secret editing privilege.
6. One store first. No provisioner, OAuth server, service mesh, message broker or Redis until the mandatory flow passes.
7. Startup/bootstrap is retryable using existing WordPress state and a SKU lookup. Changing pinned WooCommerce versions requires explicit plugin/database upgrade planning.
8. No automatic write retry. Timeouts with possible side effects are unknown outcomes, not definite failures.
9. Prove resilience, role permissions and auditability before adding optional multi-store features.

Consequences: simple deployment and explainable security boundaries, but one-node databases/storage and WordPress remain availability limits. Helm rollback is not a database rollback; backup/restore is required for incompatible schema/plugin changes.

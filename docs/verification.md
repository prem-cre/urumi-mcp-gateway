# Foundation verification — 12 October 2026

Verified locally on the dedicated `urumi` k3d/k3s cluster. This is evidence for the store foundation, not the future gateway or a deployed VPS.

| Check | Observed result |
| --- | --- |
| Python environment | Python 3.12.14; `uv.lock` records exact packages, including MCP 1.30.0 |
| Tooling | Docker Desktop 29.8.0; k3d 5.9.0; Helm 4.3.0 |
| Cluster | k3s v1.35.5-k3s1; one server and one agent; Traefik Ingress |
| Workloads | WordPress, MariaDB and PostgreSQL ready; bounded bootstrap Job completed |
| Store versions | WordPress 7.1.3/PHP 8.3; WooCommerce 11.2.1; MariaDB 11.4.13; PostgreSQL 17.11 |
| Persistent storage | Three Bound PVCs: WordPress 2Gi, MariaDB 2Gi, PostgreSQL 1Gi |
| Helm | local and production values render; schema/lint pass; no Secret values rendered |
| Runtime probes/resources | every long-running chart container has startup/readiness/liveness probes plus requests and limits |
| Rerun | release upgraded successfully to revisions 2 and 3; existing Secrets, store, and product retained |
| Browser | real storefront opened in Safari at `http://store.localhost:8080/` |
| Visitor checkout | Store API cart and checkout using public visitor cart token; COD order **#12**, status **processing** |
| Admin verification | authenticated WooCommerce admin order page contains the notebook product and COD payment method |
| WordPress/MariaDB persistence | both pods deleted/recreated; product and order #12 remain, verified again through storefront/admin |
| PostgreSQL persistence | temporary marker row survived pod replacement; verification table then removed |
| Network isolation | unrelated pod resolves full cluster DNS names but cannot connect to WordPress 80, MariaDB 3306 or PostgreSQL 5432 |
| Git hygiene | `.local/credentials.json`, `.local/kubeconfig` and `.venv` ignored; formatting and whitespace checks pass |

## Reproduce

```sh
make check
make verify
# This creates another real dummy-data COD order:
make checkout
# Substitute the order number printed above:
uv run python scripts/verify-store.py --admin-order ORDER_ID
./scripts/verify-network.sh
```

The admin verification follows WooCommerce's redirect between HPOS and legacy order editors. It uses a local credential file without printing passwords. The checkout test goes through the customer cart/checkout API; it does not insert a synthetic order directly into the database or the administrative order API. A manual browser purchase and the full two-user MCP demonstration remain later integration work.

The network test first verifies DNS against fully qualified service names and ready workloads, then checks denied TCP connections. A DNS lookup failure is a test failure, not evidence of isolation. Temporary verification pods are cleaned up.

## Limits remaining

- No Python gateway/MCP implementation or React/chat yet; no external MCP client flow yet.
- Production values rendered locally; no VPS, production DNS or certificate has been provisioned.
- Local HTTP, one-node storage per PVC, no off-node backups and no Secret encryption-at-rest configuration.
- Standard WordPress/database initialization uses default image privileges; only bootstrap and verification pod run explicitly non-root.
- PostgreSQL's bootstrap `gateway` role currently has initialization privileges. Create a distinct restricted application role before deploying the gateway.
- WooCommerce is a pinned plugin on persistent WordPress content; image rollback does not restore a previous plugin/database state.
- No outgoing email delivery is configured. Order notifications may log local sendmail failures; store/admin order persistence works.
- WordPress CLI image tag is a moving maintenance tag; pin a manifest digest for a production release. Version tags and actual image IDs are inspectable in the cluster.

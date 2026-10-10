# Urumi MCP Gateway

A Kubernetes-hosted team MCP gateway for WooCommerce and other upstream MCP servers. This repository is being built for the Urumi AI FDE Internship Round 1 assignment.

## Project status

Initial repository setup only. The application services and deployable Helm charts are not implemented yet. The first milestone is the required single-store flow; optional multi-store provisioning comes only after that works end to end.

## Planned components

| Component | Responsibility | Network exposure |
| --- | --- | --- |
| WooCommerce | WordPress storefront, admin and persistent store data | Ingress |
| WooCommerce MCP | Tools for products and orders, backed by the WooCommerce REST API | Cluster internal; gateway only |
| Gateway API | Team authentication, upstream registry, MCP aggregation/routing, audit log and chat tool loop | Dashboard/API ingress |
| Dashboard | Admin and member workflows, server health, tools, audit log and chat | Ingress |

The gateway is the only client of upstream MCP servers. Upstream credentials stay server-side, and client API keys are hashed at rest.

## Repository layout

```text
charts/                 Helm charts and environment-specific values
  platform/             Umbrella chart for the assignment stack
docs/                   Architecture, threat model and deployment notes
```

## Intended local setup

Target: Kind, k3d or Minikube with Helm and an ingress controller. Local hostnames will be documented once the chart and ingress configuration are implemented. No credentials or secrets belong in this repository; use Kubernetes Secrets or an ignored local environment file.

## Delivery milestones

1. Run one WooCommerce store and its database with persistent storage on local Kubernetes.
2. Implement the WooCommerce MCP server and verify product and order tools.
3. Implement gateway authentication, tool discovery/routing, three upstreams, audit logs and server health.
4. Add the dashboard, role controls and chat tool loop.
5. Demonstrate the required two-user flow, key revocation, upstream disablement and failure isolation.
6. Document a production-like k3s deployment using the same Helm charts with different values.

See the assignment brief for the authoritative requirements and demo checklist.

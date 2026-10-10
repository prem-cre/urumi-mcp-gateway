# Initial architecture notes

## Request path

An authenticated team member connects to the gateway endpoint with their own gateway API key. The gateway resolves the enabled upstream and tool, attaches that upstream's server-side credential, calls the upstream MCP server, and returns the result. The gateway records the user, upstream, tool, timestamp, duration and outcome for every call.

The dashboard chat uses the same gateway tool path as an external MCP client and is attributed to the signed-in member. It must not call upstream servers directly.

## Initial boundaries

- The WooCommerce MCP server is implemented in this repository and is only reachable inside the cluster.
- WordPress and its database use persistent volumes.
- Gateway API keys are shown to a user once and stored as hashes.
- Upstream credentials are Kubernetes Secrets and are never returned to the browser or MCP client.
- Each upstream has an independent timeout and health state so an unavailable server does not block discovery or calls to healthy servers.
- Tool names need a documented collision-safe namespace at the gateway boundary.

These are design intentions for the initial scaffold, not implemented guarantees yet.

# ADR 002: WooCommerce MCP adapter

Accepted 13 October 2026.

- Official Python MCP SDK 1.30.0 handles Streamable HTTP and schemas; FastAPI supplies lifespan and health endpoints. Pure ASGI middleware verifies a static service bearer token on every MCP request.
- REST wc/v3 is the runtime boundary, not WP-CLI/database access. A separate dedicated service account/key is provisioned administratively; the runtime has no Kubernetes or database access.
- Local HTTP uses WooCommerce OAuth 1.0a HMAC-SHA256. Sign with the canonical store home URL while connecting to the internal Service; HTTPS uses Basic auth. Signing is not transport encryption and does not protect JSON bodies.
- Stateless JSON MCP is sufficient for these five tools. Only a ClusterIP service is created, with gateway-only ingress and WordPress/DNS-only egress.
- Pagination/input/response size bounds and PII projection apply before results reach an agent. Today is computed in the configured store timezone. Store and MCP timezone must agree.
- Refunds and deletion are excluded from status updates. Mutations can have downstream effects; unknown transport outcomes are explicit and never automatically replayed.
- Developer integration uses an ephemeral gateway-labeled Job. It is not a substitute for the eventual authenticated, audited gateway path.

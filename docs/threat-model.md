# Threat model and implemented boundaries

The current phase runs the demonstration store and platform database. The gateway controls below are design commitments, not deployed features.

| Threat | Current foundation | Planned gateway control |
| --- | --- | --- |
| Credentials committed or exposed | random local generation, ignored mode-0600 file, Kubernetes Secrets, no secret Helm values | hashed member keys, encrypted upstream credentials, redacted logs |
| Unauthorized database or store API access | internal database Services and tested NetworkPolicies; public storefront/admin | internal MCP Service and gateway-only policy, independent upstream service credential |
| Member abuses shared upstream privileges | no gateway/member accounts yet | permissions at tools/list AND tools/call, role grants, attributed audit intent before dispatch |
| Client key passed to upstream | no MCP path yet | construct fresh upstream request; never copy client auth headers |
| Malicious registration URL / SSRF | no URL registry yet | explicit internal-service allowlist, external HTTPS policy, resolved-address/redirect checks and egress limits |
| Prompt injection in tool descriptions/results | no model calls yet | label origin, bound metadata/output, enforce writes/grants outside LLM, human approval for sensitive chat writes |
| One user reads another's chat/session | no team platform yet | ownership checks, unguessable tokens, hashed sessions, CSRF and secure cookies on VPS |
| Data loss and unsafe upgrades | PVCs survive pod replacement; version-pinned store | backups and tested restore before schema upgrades; Helm rollback alone is insufficient |
| Compromised workload | no Kubernetes API token; default deny traffic paths | Python services non-root, least-privilege credential and network paths, no Kubernetes CRUD for normal gateway |

Kubernetes Secrets are not synonymous with encryption at rest. The local k3s setup does not enable Secret encryption at rest. TLS to the public storefront is also not configured locally. Databases and WordPress may initialize as root. These limitations must be described in the demo rather than hidden.

A member never seeing an upstream credential does not mean the member has no upstream power: any allowed gateway tool acts with that shared credential. Restrict the available operations, validate arguments, use the smallest upstream credential capability, attribute calls, and revoke promptly. A malicious upstream may lie about its tools or return instructions; schema validation and prompt wording alone cannot make it trustworthy.

## WooCommerce MCP phase (13 October)

Implemented: internal ClusterIP-only service, gateway-only ingress policy, WordPress/DNS-only egress, independent backend bearer credential, dedicated WooCommerce service key, privacy-minimized output, validation/size limits, sanitized failures and no write retries. See ADR 002 and verification-mcp.md. Gateway member policy, hashing/revocation and attributed auditing remain planned. Cluster administrators can impersonate labels; the service token is an additional boundary, not a substitute for Kubernetes RBAC.

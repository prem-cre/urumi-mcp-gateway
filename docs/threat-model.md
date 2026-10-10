# Initial threat model

This is a starter checklist; controls will be documented against the implemented system before submission.

| Threat | Initial control direction |
| --- | --- |
| Leaked member API key | Store only a one-way hash; support per-key revocation; attribute and audit calls. |
| Member abuses shared upstream credential | Enforce role/tool permissions in the gateway and audit the acting member; never expose upstream credentials. |
| Malicious upstream URL (SSRF) | Validate destinations, block metadata and unintended cluster/control-plane addresses, and constrain gateway egress. |
| Malicious tool descriptions or results | Treat upstream metadata and tool output as untrusted model input; avoid granting writes by default and enforce permissions outside the model. |
| Compromised WooCommerce MCP pod | Keep it internal, give it only the store API credential it needs, and restrict network access. |
| Store data loss | Persist database and WordPress content; document backup and restore behavior. |

Controls listed here are goals until the corresponding code and Kubernetes policies exist.

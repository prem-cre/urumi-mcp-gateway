# Scope and acceptance checklist

Repository/system design, store foundation, and the own WooCommerce MCP adapter are implemented. The dashboard has a demo frontend. Next: gateway aggregation, access/audits, then live chat integration.

| Requirement | Implementation milestone |
| --- | --- |
| Local Kubernetes + same Helm charts for VPS | current foundation |
| Persistent WordPress and database, Ingress, probes, limits, Secrets | current foundation |
| Storefront purchase and admin order visibility | current foundation verification |
| Own WooCommerce MCP: five required tools, Streamable HTTP, internal only | implemented; see services/woocommerce_mcp/README.md |
| Registry CRUD, three upstreams, collision handling, timeouts | gateway |
| Admin/member, invitations, per-user hashed/revocable keys | access |
| Every tool call audited with right identity | access + gateway |
| React dashboard + chat via gateway | dashboard/chat |
| Two-user demo, cross-server prompt, revoke, disable, pod failure | integration |
| README, design/tradeoffs, threat model, local/VPS instructions | continuously |
| VPS/TLS, permissions, SSRF hardening, latency/error metrics | focused differentiators after core |

Submission date is unresolved: original brief says 26 October; user later says 24 November. Plan against 24 November while confirming the authoritative date.

## Definition-of-done demo sequence

1. Buy a seed product with COD; inspect the order in WooCommerce admin.
2. User A creates a product through chat; verify the storefront.
3. Buy that product manually, ask chat for today's orders in the store's timezone.
4. User B configures an external client with only gateway URL/key; list enabled tools and answer today's-orders question.
5. Ask a question requiring WooCommerce and another upstream.
6. Revoke B's key; reject its next request, including an already-established session.
7. Disable an upstream; re-list tools in chat and external client; direct calls are rejected too.
8. Delete an upstream pod, observe unreachable state and another upstream still working. Because Kubernetes can restart quickly, use a controlled temporary scale-to-zero if necessary and restore afterward; disclose it.
9. Inspect audits attributed to A and B with durations and outcomes.

Optional multi-store work begins only after every above item is reproducible.

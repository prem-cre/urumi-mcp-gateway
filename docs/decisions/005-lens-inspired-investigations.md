# ADR 005: Lens-inspired trace investigations

Accepted 15 October 2026.

## Context

The basic Urumi trace view records model/tool steps, usage, cost estimates and latency, but metadata-only traces cannot explain recurring quality problems. LiteLLM Lens demonstrates an improvement loop: capture traces, let an investigation compare a bounded sample against expected behavior, group patterns with evidence, then inspect or resolve the linked findings.

The local LiteLLM checkout includes a remote-only Lens adapter and an OCI subchart reference to a separate Lens service; it does not include that service's source. Lens uses its own trace store and investigation runtime. Urumi's assessment currently has one team, a modest trace volume, one gateway replica, and PostgreSQL as its platform database.

## Decision

1. Add an admin-only, on-demand investigation workflow using the existing gateway process and PostgreSQL. Persist investigation definitions, immutable per-run trace selections, job status/leases, model usage/cost, findings, evidence references, feedback, and resolution state in separate relational tables.
2. Keep the run queue durable. A worker claims queued jobs with a row lock and `SKIP LOCKED`, freezes selected trace IDs when the run is enqueued, bounds trace count/prompt size/output tokens, and records model usage and explicit-price cost estimates. Require an `Idempotency-Key` when creating a run. Do not retry an interrupted provider call; its provider billing outcome may be unknown.
3. Keep trace content off by default in application settings and production Helm values. Local values enable bounded, heuristic-redacted previews for synthetic demo data. Never persist system prompts, hidden reasoning or arbitrary full model request payloads. Apply a short configurable TTL to preview fields, purged by a five-minute worker sweep. Redaction is a best-effort filter, not a privacy guarantee.
4. Send only selected redacted previews and bounded trace metadata to the configured analysis model. The model receives no tools and is instructed that trace content is untrusted. Require structured findings with trace/step IDs and short quotes; discard evidence that does not point to a selected trace/observation and an exact quote found in its stored preview.
5. Restrict investigation definitions, runs, findings and feedback to admins. This assessment has no tenant/team partition, so a member cannot run a query that combines other members' traces.
6. Keep analysis model credentials server-side in the existing Kubernetes Secret. Require explicit model input/output pricing to run an investigation and estimate the request's maximum cost before dispatch using a conservative UTF-8 byte ceiling plus message overhead; record provider-reported token counts and an estimated actual cost after completion. Label cost as estimated.

## Consequences

- Urumi demonstrates the core Lens feedback loop without installing LiteLLM Lens, adding ClickHouse or adding a public service boundary to the assessment.
- PostgreSQL's `SKIP LOCKED` job claim is safe for multiple workers, but Urumi currently deploys one gateway replica and does not claim distributed worker availability. A worker crash after dispatch is marked as an unknown-cost failure, not silently replayed.
- Investigation input content remains sensitive even after heuristic redaction. Production capture stays disabled pending a reviewed consent, encryption, retention and access policy. Captured previews are cleared within five minutes after the configured TTL; run metadata, findings and aggregate metrics have a separate retention decision still to be made.
- Explicit pricing and a conservative token preflight reduce surprise cost but do not guarantee an exact provider invoice. Missing usage remains unpriced; the UI must show that clearly.
- This version covers dashboard chat traces only. External MCP calls continue to have authoritative per-call audit rows; grouping arbitrary external client sessions requires an explicit trace-context propagation contract.
- Scheduled monitoring, OTLP ingestion, ClickHouse, datasets, export, multi-team trace scopes and finding trend analytics are deferred until the mandatory two-user demo and actual provider trace flow are stable.

## References

- [LiteLLM Lens launch](https://docs.litellm.ai/blog/litellm-lens-launch)
- [Investigations workflow](https://docs.litellm.ai/docs/proxy/lens/investigations)
- [Tracing and investigation API](https://docs.litellm.ai/docs/proxy/lens/api)
- [LiteLLM Lens architecture study](../research/litellm-lens-reference.md)

# ADR 004: chat observability and complexity routing

Accepted 15 October 2026.

## Decision

Persist a trace for every dashboard assistant turn in the existing PostgreSQL platform database. Each provider model invocation is a `generation` observation; each MCP tool invocation is a `tool` observation. The rows carry a trace ID and sequence number so the dashboard can show the agent loop in execution order. Existing `tool_audits` remain authoritative for caller identity, permissions, revocation and upstream dispatch outcomes.

Capture model/provider identity, route tier and reason, response usage, latency, status and cost. Estimate cost from the returned token counts and model rates configured in Helm values. Mark calls unpriced when token usage or an exact model price is missing. The dashboard labels cost as an estimate; the system does not claim provider invoice accuracy. Provider-reported spend can be added when a provider returns it in a documented normalized field.

Prefer an explicitly configured `routerModel` when present. It can be a LiteLLM Auto Router alias; the gateway records that routing was delegated and captures the returned model. Otherwise use the local deterministic, inspectable rule: cross-server and multi-step signals select `complexModel`; other requests use `standardModel`; if a matching profile is not set, use the default model. All configured names can be aliases on an OpenAI-compatible LiteLLM Proxy. Record the tier and human-readable reason on each trace and generation.

The baseline trace stores no prompt, history, tool arguments/results, credentials, customer data or model reasoning. ADR 005 adds an explicit deployment-controlled, short-TTL preview mode for local synthetic data; it applies bounded heuristic redaction before persistence and never captures system prompts or hidden reasoning. Trace details expose content only when that trace was captured. The redaction is not a guarantee that PII is removed. Members may see their own traces; admins may inspect workspace traces.

## Consequences

- The dashboard can explain model calls, tool calls, failures, cost estimates and routing decisions without making Langfuse or LiteLLM Lens a runtime dependency.
- PostgreSQL now stores trace and observation records, so retention and indexing matter as traffic grows. The current assessment deployment is small; server-side aggregates read the selected 1–30 day window.
- Pricing is only as accurate as the configured model rates and returned usage. A missing price is shown explicitly as unpriced.
- The built-in routing heuristic is intentionally explainable and modest. It is not a learned quality router and does not claim that a wording classifier measures task difficulty perfectly. Teams can delegate routing to a separately configured proxy Auto Router.
- The first-party chat trace model now supports an admin-only, on-demand Lens-inspired investigation workflow under [ADR 005](005-lens-inspired-investigations.md). It uses PostgreSQL and the configured analysis model at assessment scale; external OTLP ingestion, scheduled investigations, datasets and high-volume trace storage remain future work.

## Reference patterns

Langfuse models traces as observations, with a separate generation for every LLM invocation and tool observations interleaved in the agent flow. It can infer costs from per-model prices when usage is present. LiteLLM Lens adds agent-assisted analysis over trace collections and links findings back to source steps. LiteLLM Auto Router is the classifier/model-selection layer; LiteAgents is the agent-harness layer. We adopt the step-level trace and explicit cost-model patterns, while retaining our own team identity and gateway audit boundary.

References: [Langfuse usage and cost tracking](https://langfuse.com/docs/observability/features/token-and-cost-tracking), [Langfuse trace best practices](https://langfuse.com/docs/observability/best-practices), [LiteLLM Lens launch](https://docs.litellm.ai/blog/litellm-lens-launch), [LiteLLM Auto Router](https://docs.litellm.ai/docs/auto_router/), and [LiteAgents SDK](https://docs.litellm.ai/docs/liteagents). See [the detailed Lens source study](../research/litellm-lens-reference.md).

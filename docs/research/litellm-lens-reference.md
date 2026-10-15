# LiteLLM Lens: product and codebase study

Reviewed 15 October 2026 against the [Lens launch post](https://docs.litellm.ai/blog/litellm-lens-launch), the official [Lens overview](https://docs.litellm.ai/docs/proxy/lens), [investigations guide](https://docs.litellm.ai/docs/proxy/lens/investigations), [API reference](https://docs.litellm.ai/docs/proxy/lens/api), [deployment guide](https://docs.litellm.ai/docs/proxy/lens/deployment), and the local LiteLLM source snapshot at `/Users/premkumar/Downloads/litellm-main`.

## Product model

Lens is an agent observability and improvement loop. A run is stored as an ordered, nested trace containing agent activity and linked LLM/tool spans. Operators inspect an individual trace, or define an investigation that samples many traces against expected behavior and explicit checks. An investigation groups recurring observations into findings, reports their priority and frequency, gives a suggested next step, and cites exact runs and steps as evidence. Operators can give feedback, mark a finding resolved, and promote useful runs into evaluation datasets. The launch post frames this as: capture traces, identify recurring failures, then feed fixes back into agent prompts, tools, or model configuration.

Lens separates three distinct capabilities:

1. **Capture and inspect.** Agents export OTLP traces and logs to Lens. The trace view presents the run tree, selected-step input/output/attributes, timing, tokens and cost. Its API lists paginated trace summaries and reads an individual trace or span.
2. **Investigate.** An admin selects agents and a time window, optionally filters activity, selects a sample, describes expected behavior and adds checks. A worker reviews runs in parallel, groups similar observations, checks the original evidence, and saves findings plus run history, progress and cost. Monitoring can schedule future runs. Provider credentials and the analysis model are configured on the Lens service, separate from an agent's own provider connection.
3. **Improve.** Findings link to exact evidence. Operators can say that behavior is expected, mark a problem resolved, or save examples and expected responses into a dataset for regression evaluation.

The lens feature is explicitly a feedback system on top of ordinary traces; it does not replace the gateway or require every trace to be read by a human. Its agent-first tracing API also allows coding agents to inspect a team's own trace store without depending on a third-party tracing SaaS.

## Architecture visible in the reference

The current Lens runtime is a separately deployed service, not code embedded inside the LiteLLM gateway. The local `helm/litellm/Chart.yaml` depends on the `lens` OCI chart, and `values.yaml` points at a separate `ghcr.io/berriai/lens` image. The local checkout's `litellm/tracing/AGENTS.md` says Lens owns trace normalization, schemas, storage, graph assembly and queries; the LiteLLM package keeps a remote-only reader and validates the remote contract. Its comment says not to add a native trace fallback. The checked-out Lens runtime itself is absent from `/Users/premkumar/Downloads/litellm-main`; the checkout includes the gateway integration and pinned contract assets, not the implementation of the separate Lens service.

The current standalone Lens docs describe a Rust service with ClickHouse for trace storage and metadata, plus ClickHouse KeeperMap coordination. Older gateway-hosted design details are explicitly marked historical. Those old notes still show useful scaling lessons: constrain every trace query by time, freeze a sample once per job, page individual trace reads, use leases and a due-job index, and keep appended findings/evidence separate from mutable job progress.

The LiteLLM gateway acts as an identity-aware adapter when Lens appears in its dashboard. It authenticates the ordinary dashboard/API user, computes that user's trace scope, and forwards Lens UI/API calls to the internal Lens service using a short-lived signed identity assertion. This keeps the Lens admin token out of the browser and gives Lens a trusted user/team scope. Agent trace ingestion uses a separate tracing credential directly to Lens; that credential can ingest and check delivery receipts, but cannot read traces or call models. Normal user auth is used for trace reads, and investigation APIs are admin-restricted.

## LiteLLM codebase map

The source snapshot has no `.git` metadata, so this review records source paths rather than asserting a commit hash.

| Local source path | Architectural role |
| --- | --- |
| `litellm/proxy/lens/adapter.py` | Authenticated dashboard adapter; resolves trace-read scope; signs a short-lived user assertion; forwards only allowlisted headers; exposes service status. |
| `litellm/proxy/lens/buffering.py` | Bounds request and response bytes and concurrent forwarding memory; retains reservations until the response is sent. |
| `litellm/proxy/lens/internal.py` | Verifies internal service assertions and marks Lens analysis calls so the gateway's spend logger can distinguish them. |
| `litellm/proxy/tracing_endpoints.py` | Trace ingestion/read boundary. Upload points to Lens directly; reads enforce user/team scope, bounded pages and stable public error classes. |
| `litellm/proxy/tracing_runtime.py` | Creates the remote Lens reader/exporter at application lifespan and closes it with the app. |
| `litellm/tracing/remote.py` | Validates Lens connection settings, bounds response payloads, and sends typed trace/list/span/SQL requests to Lens. |
| `litellm/tracing/storage.py`, `receiver.py`, `queries.py` | Typed read façade and query contracts. Generated types are derived from pinned Lens schemas rather than hand-maintained copies. |
| `scripts/lens_assets/`, `scripts/trace_codegen/` | Pins schema/fixture source and hashes; generates gateway-side Python contracts offline. |
| `helm/litellm/templates/_helpers.tpl`, `values.yaml`, `ingress.yaml` | Wires internal Lens URL and service credentials, separate public URL, optional standalone/external mode and ingestion route. |

The root LiteLLM source license is MIT. That does not make its separately released Lens service code available in this checkout. Urumi's implementation below is its own code and data model; this study adopts the product and trust-boundary patterns rather than copying code.

## Urumi implementation boundary

For the assessment's single team and low-volume demo, the first Lens-inspired version keeps the existing Python gateway and PostgreSQL deployment. It adds an on-demand durable investigation queue in the gateway process rather than adding another service and ClickHouse to the mandatory stack. PostgreSQL row locks with `SKIP LOCKED` make job claiming safe if a second worker is later introduced, but the current deployment remains one gateway replica and does not claim HA.

Every dashboard chat still creates a trace and ordered model/tool observations. Optional trace-content capture stores only bounded redacted previews, never system prompts or hidden model reasoning. Capture is enabled in local values for synthetic assessment data and disabled in production values. A five-minute retention sweep clears previews within five minutes after the configured TTL. The redaction is heuristic rather than a guarantee, so the UI warns admins to use synthetic data.

Admins can define expected behavior and checks, freeze a bounded set of completed captured traces, and enqueue a run with an idempotency key. The worker makes one analysis model call without tools, uses UTF-8 byte count plus fixed message overhead as a deliberately conservative input-token ceiling, caps traces and completion tokens, and records tokens/cost/status. Provider tokenizer and invoice differences mean the estimate is a guardrail, not a billing guarantee. It does not silently replay an interrupted provider call. A finding is saved only with an exact quote that validates against a selected trace or observation, and its link resolves to the original trace. Members cannot create or read workspace investigations because findings may combine more than one member's activity.

This slice does not yet implement external OTLP ingestion for third-party agents, scheduled monitoring, ClickHouse, full trace pagination, multi-team access scopes, or dataset exports. Existing external MCP tool calls remain individually visible in the authoritative `tool_audits`; combining arbitrary external client sessions into agent traces needs an explicit trace-context contract. Those are follow-on capabilities after Urumi's required two-user workflow and real model-backed traces are demonstrated.

## Sources

- [LiteLLM Lens launch post](https://docs.litellm.ai/blog/litellm-lens-launch)
- [Lens product overview](https://docs.litellm.ai/docs/proxy/lens)
- [Lens investigations](https://docs.litellm.ai/docs/proxy/lens/investigations)
- [Lens tracing and investigation API](https://docs.litellm.ai/docs/proxy/lens/api)
- [Lens deployment](https://docs.litellm.ai/docs/proxy/lens/deployment)
- [Historical Lens scalability measurements and current runtime note](https://docs.litellm.ai/docs/proxy/lens/scalability)
- [LiteLLM source repository](https://github.com/BerriAI/litellm)

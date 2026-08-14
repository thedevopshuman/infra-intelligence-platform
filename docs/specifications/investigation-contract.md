# Investigation request and report contracts

**Status:** v1alpha1

**Machine contracts:** `contracts/schemas/investigation-request.schema.json`, `contracts/schemas/investigation-report.schema.json`

An investigation is a durable, bounded read-and-reason workflow. A request fixes the question, scope, authority ceiling, and budgets. A terminal report fixes the selected runtime, evidence-backed conclusions, uncertainty, recommendations, and actual usage. Model narration is derived presentation; these structured envelopes are authoritative.

## Shared identity and tenancy

The request and its report use the same opaque `inv_` identifier. Tenant and actor fields are assertions checked against authenticated context; they never grant access. An investigation cannot widen tenant, resource, time, tool, or authority scope after acceptance.

The report stores `requestDigest` as `sha256:` plus the lowercase SHA-256 digest of the request serialized with RFC 8785 JSON Canonicalization Scheme. This pins the exact accepted request even if a caller later presents a modified envelope.

## Investigation request

The request requires:

- a human-readable question and a structured trigger containing only normalized, secret-free context;
- one or more resource UIDs and an explicit time range;
- wall-time, tool-call, model-token, cost, evidence-item, and iteration budgets;
- a maximum authority of `read` or `propose`; and
- tenant, actor, request time, and optional correlation context.

An agent selector is optional so policy may choose a compatible release. If present, it pins an immutable agent ID and version. `allowedTools` and `evidenceTypes` are request upper bounds, not grants. The runtime intersects them with the agent manifest, tenant policy, actor authority, provider availability, and resource scope.

Application validation requires the scope start to precede its end. It also rejects resource references outside the authenticated tenant and budget values above tenant policy. `propose` permits structured recommendations or future action proposals but no side effect, approval, or execution.

## Investigation report

Every terminal path returns a report, including policy denial, cancellation, runtime failure, and exhausted budgets. The report records:

- outcome and stable terminal reason;
- start/completion timestamps and the exact accepted scope;
- selected agent version, immutable manifest digest, and policy-selected model class when selection occurred;
- ranked hypotheses with confidence and supporting and contradicting evidence IDs;
- explicit unknowns and the evidence types needed to resolve them;
- ordered operational recommendations without embedded mutations;
- the complete evidence-ID set plus tool-ledger and policy-snapshot references; and
- actual calls, iterations, tokens, wall time, cost, and evidence usage.

`conclusive` requires at least one hypothesis and the `sufficient-evidence` terminal reason. `inconclusive` requires an explicit unknown and uses `insufficient-evidence`, `budget-exhausted`, or `deadline-exceeded`. Failed and cancelled reports retain whatever evidence and unknowns were safely recorded before termination.

## Evidence and uncertainty rules

Every evidence ID cited by a hypothesis or recommendation must also appear in the report-level `evidenceIds` set and resolve to evidence in the same tenant. Contradicting evidence remains visible. A material claim without supporting evidence must be expressed as an unknown or evidence gap rather than as a confident conclusion.

`hypotheses[].rootCauseClass` is an optional machine-readable taxonomy key used by deterministic evaluation. Narrative text remains explanatory, but scorecards never infer a class from prose.

Reports carry references, summaries, and hashes—not raw logs, metrics, traces, credentials, prompts, provider exceptions, or tool output. A missing or expired artifact does not erase the historical citation; the presentation layer marks it unavailable.

## Authority and lifecycle boundary

The investigation report may recommend a next check, monitoring step, or escalation. It cannot encode an executable mutation. A future action proposal is a separate immutable contract and workflow that receives a current policy decision, idempotency key, approval when required, and audit trail.

Reports are immutable terminal artifacts. A retry creates a new investigation request and ID, linked through correlation and causation events rather than overwriting a prior report.

## Validation invariants

In addition to JSON Schema validation:

- request and report tenant and investigation IDs must match;
- `requestDigest` must match the accepted canonical request;
- report scope must equal the accepted request scope;
- timestamps must satisfy `requestedAt <= startedAt <= completedAt <= createdAt`;
- every citation must belong to the report evidence set and authenticated tenant;
- selected tools and authority must remain within all applicable upper bounds; and
- usage counters must be checked against the accepted budgets and any stricter policy limits.

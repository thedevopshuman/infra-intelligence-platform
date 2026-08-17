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

`telemetrySelections` is an optional ordered list of bounded metric-query candidates. Each candidate supplies an opaque selection ID, integration ID, the provider-neutral query fragment from `TelemetryEvidenceRequest`, explicit result limits, and optional root-cause classes. It does not supply tenant, actor, resources, time range, deadline, endpoint, credentials, or a vendor query language. Those scopes are inherited from the authenticated investigation and cannot be widened by a candidate.

The deterministic runtime selects generally applicable candidates and candidates whose `rootCauseClasses` contain its current classification. Selection order is request order. When request evidence/tool bounds are present, collection additionally requires `telemetry.metrics` and `telemetry/query`. Every attempted query consumes one tool call, every committed result consumes one evidence item, and execution stops before either investigation budget would be exceeded. The derived telemetry deadline is capped at five minutes even when the investigation wall-time budget is larger.

A root-cause-scoped candidate may declare an `interpretation`. The rule fixes a statistic (`minimum`, `maximum`, or `mean`), exact unit, comparison operator (`lt`, `lte`, `gt`, or `gte`), finite threshold, and distinct matched/unmatched dispositions before collection. Both dispositions come from `supports`, `contradicts`, and `neutral`. A rule without `rootCauseClasses`, a constant disposition, a non-finite threshold, or a control-bearing unit is invalid. The application performs no unit conversion.

Alternatively, a root-cause-scoped candidate may declare `baselineComparison`; a selection cannot declare both rule forms. It fixes the same statistic, unit, operator, threshold, and dispositions plus ordered baseline/evaluation time ranges and either `difference` or `ratio`. Both ranges are subranges of the inherited investigation scope and must satisfy `scope.start <= baseline.start < baseline.end < evaluation.start < evaluation.end <= scope.end`. This is still one query over the investigation scope and one immutable artifact, not two hidden backend calls.

For repeated operational use, a candidate may instead declare `rollingBaselineComparison`. It supplies bounded baseline and evaluation durations plus a positive gap rather than absolute timestamps. The control plane anchors the evaluation window at `scope.end`, places the gap immediately before it, and places the baseline window immediately before the gap. The derived windows must fit entirely inside the accepted scope. The exact duration rule remains in the digested request, while the report records the derived absolute windows so the assessment is auditable. A selection may contain exactly one of `interpretation`, `baselineComparison`, and `rollingBaselineComparison`; all three remain one-query rules over one committed artifact.

`kubernetesEventSelections` is a separate optional ordered list for customer Kubernetes Events. Each candidate reuses the public Kubernetes Event query and limits and inherits authenticated investigation identity, resource scope, time range, and deadline. It cannot contain a Kubernetes endpoint, credential, kubeconfig, raw command, or cluster-wide selector. Eligible event selections run before telemetry selections; each attempt consumes one tool call, and each committed artifact consumes one evidence item.

A root-cause-scoped event candidate may declare an interpretation consisting of normalized condition keys, a positive minimum match count within `maxEvents`, and distinct matched/unmatched dispositions. Complete stored evidence records the matching event IDs/count and applies the declared disposition. Explicitly empty evidence is `no-data`, partial evidence is `incomplete`, and corrupt or mismatched evidence becomes a stable unknown. The investigator never assesses the backend's raw Kubernetes objects or response text.

`logSelections` is a separate optional ordered list for historical logs. Each candidate reuses the backend-neutral logical service, severity, exact attribute filter, and output-limit fragments from `LogEvidenceRequest`, while identity, resources, time range, and deadline are inherited from the investigation. Eligible log selections run after Kubernetes Event and metric selections. Collection requires `telemetry.logs` and `telemetry/query` when the request supplies those upper bounds, and consumes the same tool/evidence budgets.

A root-cause-scoped log candidate may declare a positive `minRecords` interpretation no greater than its `maxRecords` limit, with distinct matched/unmatched dispositions. The deterministic runtime validates and counts records from the committed normalized artifact only. It does not inspect log body text, infer meaning from prose, or issue a hidden text search. No-data, partial, corrupt, and mismatched evidence follow the same conservative evidence-gap rules as other signals.

`changeSelections` is an optional ordered list for resource and deployment history. Each candidate reuses the closed change-kind query and bounded limits from `ResourceChangeEvidenceRequest`; authenticated identity, resources, time range, and deadline are inherited. Change selections run after Kubernetes Events and before external telemetry, require `resource.change` and `evidence/fetch` when those request upper bounds are present, and consume the common tool/evidence budgets. A selection cannot request before/after values, arbitrary paths, provider query text, credentials, or another tenant's resources.

A root-cause-scoped change candidate may declare a positive `minChanges` interpretation no greater than `maxChanges`, with distinct matched/unmatched dispositions. Only a complete, committed, structurally revalidated artifact can support, contradict, or remain neutral. The assessment records change IDs and count but never copies configuration or image values. No-data stays explicit; partial, corrupt, missing, or scope-mismatched history becomes incomplete or an evidence gap and cannot become a hypothesis citation.

`contextSelections` is an optional ordered list for repository, runbook, ownership, deployment, and service-catalog context. Each candidate reuses the closed logical references, document kinds, and limits from `ContextEvidenceRequest`; identity, resources, time range, and deadline are inherited. Context selections run after Kubernetes Events and before resource-change and external telemetry selections, require `repository.context` and `evidence/fetch` when those request upper bounds are present, and consume the common tool/evidence budgets. They cannot contain paths, repository query language, endpoints, credentials, or free-form search text.

A root-cause-scoped context candidate may declare a positive `minDocuments` interpretation no greater than `maxDocuments`, with distinct matched/unmatched dispositions. The runtime assesses only a complete, committed, structurally revalidated artifact. It verifies that every returned document is in the selected kind/reference/resource scope and is explicitly marked `trust: untrusted` and `instructionPolicy: data-only`. The assessment records stable document IDs and selected logical reference IDs but never copies excerpts, titles, locators, prompts, secrets, or repository contents into the report. Document text is evidence data only and cannot change policy, tools, authority, classification, confidence, or runtime instructions.

Application validation requires the scope start to precede its end. It also rejects resource references outside the authenticated tenant and budget values above tenant policy. `propose` permits structured recommendations or future action proposals but no side effect, approval, or execution.

## Investigation report

Every terminal path returns a report, including policy denial, cancellation, runtime failure, and exhausted budgets. The accepted request is first represented by the separate [durable lifecycle contract](investigation-lifecycle-contract.md); status changes never mutate the final report. The report records:

- outcome and stable terminal reason;
- start/completion timestamps and the exact accepted scope;
- selected agent version, immutable manifest digest, and policy-selected model class when selection occurred;
- an optional auditable cross-signal plan with capacity, ordered candidates, and stable scheduled/deferred reasons;
- ranked hypotheses with confidence and supporting and contradicting evidence IDs;
- structured telemetry assessments when a declared metric rule was evaluated;
- structured Kubernetes Event assessments when a declared condition rule was evaluated;
- structured log assessments when a declared record-count rule was evaluated;
- structured resource-change assessments when a declared change-count rule was evaluated;
- structured repository/runbook context assessments when a declared document-count rule was evaluated;
- explicit unknowns and the evidence types needed to resolve them;
- ordered operational recommendations without embedded mutations;
- the complete evidence-ID set plus tool-ledger and policy-snapshot references; and
- actual calls, iterations, tokens, wall time, cost, and evidence usage.

`conclusive` requires at least one hypothesis and the `sufficient-evidence` terminal reason. `inconclusive` requires an explicit unknown and uses `insufficient-evidence`, `budget-exhausted`, or `deadline-exceeded`. Failed and cancelled reports retain whatever evidence and unknowns were safely recorded before termination.

## Evidence and uncertainty rules

Every evidence ID cited by a hypothesis or recommendation must also appear in the report-level `evidenceIds` set and resolve to evidence in the same tenant. Contradicting evidence remains visible. A material claim without supporting evidence must be expressed as an unknown or evidence gap rather than as a confident conclusion.

`hypotheses[].rootCauseClass` is an optional machine-readable taxonomy key used by deterministic evaluation. Narrative text remains explanatory, but scorecards never infer a class from prose.

Reports carry references, summaries, and hashes—not raw logs, metrics, traces, credentials, prompts, provider exceptions, or tool output. A missing or expired artifact does not erase the historical citation; the presentation layer marks it unavailable.

Selected metric results join the report-level evidence set and remain independently immutable. A result is never cited merely because a query returned data. When a selection declares an interpretation, the runtime reads the committed artifact through the tenant-scoped Evidence store and evaluates only a `complete` normalized result whose logical metric, exact unit, and finite points match the declaration. All returned series are flattened for the declared statistic.

`telemetryAssessments` records the rule and result. `supporting` and `contradicting` assessments cite their Evidence ID on the matching hypothesis; `neutral`, `no-data`, and `incomplete` assessments remain visible without becoming hypothesis citations. Threshold no-data and incomplete assessments omit `observedValue`. Baseline assessments are identified by `assessmentType: baseline-comparison`; complete assessments include the two window values and derived comparison value, while no-data and incomplete assessments omit all three. A corrupt, missing, mismatched, or unreadable artifact becomes a stable evidence gap without exposing stored content or provider errors.

For a baseline assessment, the runtime splits only the finite points already present in the committed artifact. It applies the declared statistic independently to each inclusive window. `difference` is `evaluationValue - baselineValue` and retains the declared metric unit. `ratio` is `evaluationValue / baselineValue` and has comparison unit `1`; a zero baseline denominator is `incomplete`. Missing points in either window, partial provider output, and non-finite calculations are also `incomplete`. Rolling rules use the same assessment contract after deterministic window derivation. There is no interpolation, carry-forward, implicit extra query, or automatic unit conversion.

In the deterministic reference runtime, metric assessment does not reclassify the resource-derived root cause or silently change confidence, rank, or terminal outcome. Contradicting evidence remains explicit for a later evaluator or human reviewer.

The same restraint applies to Kubernetes Event assessments. Supporting and contradicting dispositions add the committed Evidence ID to the matching hypothesis. Neutral, no-data, and incomplete assessments remain visible without becoming citations. Event facts enrich the audit trail but do not secretly rewrite classification or confidence.

`logAssessments` records the declared minimum and, only for a complete result, the observed normalized record count. Supporting and contradicting dispositions cite the matching hypothesis. No-data and incomplete assessments omit the observed count. Log body prose is never copied into the report or used by the deterministic rule.

`changeAssessments` records the selected closed change kinds, declared minimum, and—only for complete evidence—the observed count and stable change IDs. Supporting and contradicting dispositions cite the matching hypothesis. No-data and incomplete assessments omit observed counts and IDs. Before/after values never enter the report, and change correlation does not silently reclassify or raise confidence.

`contextAssessments` records the selected kinds and logical references, declared minimum, and—only for complete evidence—the observed count, stable document IDs, and returned selected references. Supporting and contradicting dispositions cite the matching hypothesis. No-data and incomplete assessments omit observations. Excerpts remain solely in the protected Evidence artifact, are always untrusted data, and are never interpreted by this deterministic rule or copied into the report.

## Cross-signal planning

After resource classification, the deterministic runtime builds `signalPlan` across all validated request candidates. `risk-aware-v1` preserves request order inside a fixed least-risk sequence: Kubernetes Events, protected repository/runbook context, value-minimized resource changes, metrics, then logs. It applies root-cause applicability, the request's evidence/tool upper bounds, and the tool/evidence capacity remaining after resource-state collection. Each candidate is exactly once in the plan as `scheduled` or `deferred`, with a stable reason: `eligible`, `root-cause-mismatch`, `request-upper-bound`, or `budget-exhausted`, and an origin of `request` or `protected-catalog`.

If a scheduled provider returns `provider-error` or `provider-unavailable` before committing Evidence, the runtime may promote exactly one candidate that was initially deferred only for `budget-exhausted`. It first reserves capacity for all still-pending scheduled candidates, then chooses the first eligible deferred candidate in the same fixed risk order. A missing provider excludes other candidates for that signal. A promotion changes the report strategy to `risk-aware-v2`, marks the promoted step `scheduled`, and records the trigger, promoted candidate, initial reason, and positive remaining tool/Evidence capacity under `replanning`. `usage.iterations` is `2`; without a promotion the plan remains `risk-aware-v1` with one iteration.

The plan grants no new capability. Before durable acceptance, the trusted preparation boundary may fill an empty signal list from the authenticated tenant's protected signal profile. Those candidates become part of the accepted request and are bound by a server-owned `catalogSnapshot`; a client-supplied snapshot is invalid. The profile and resolved-subset digests plus generated selection references are copied into `signalPlan.catalog`, while protected query content is not. Explicit non-empty caller selections win per signal and are never mixed with catalog candidates for that signal. Adaptive promotion can select only one of these already-accepted candidates; it cannot synthesize or change queries, integrations, scope, credentials, tools, policy, or authority. Provider details remain absent. See the [catalog contract](investigation-signal-catalog-contract.md), [ADR 0054](../decisions/0054-protected-investigation-signal-catalog.md), and [ADR 0075](../decisions/0075-bounded-adaptive-signal-replanning.md).

The plan remains provenance rather than an execution ledger: cancellation, provider failure, or a concurrent deadline can prevent a scheduled step from completing. Actual calls remain authoritative in `usage` and `toolCallLedgerRef`.

## Authority and lifecycle boundary

The investigation report may recommend a next check, monitoring step, or escalation. It cannot encode an executable mutation. A future action proposal is a separate immutable contract and workflow that receives a current policy decision, idempotency key, approval when required, and audit trail.

Reports are immutable terminal artifacts. A retry creates a new investigation request and ID, linked through correlation and causation events rather than overwriting a prior report.

Cancellation is cooperative and durable. A cancelled partial run may list Evidence already committed before the request was observed, but it publishes no hypotheses or recommendations. An expired execution lease is closed without replaying tools under the same ID.

## Validation invariants

In addition to JSON Schema validation:

- request and report tenant and investigation IDs must match;
- `requestDigest` must match the accepted canonical request;
- report scope must equal the accepted request scope;
- timestamps must satisfy `requestedAt <= startedAt <= completedAt <= createdAt`;
- every citation must belong to the report evidence set and authenticated tenant;
- every telemetry assessment must reference report Evidence and the selected root-cause class;
- every Kubernetes Event assessment must reference report Evidence, its declared selection rule, and the selected root-cause class;
- every log assessment must reference report Evidence, reproduce its declared count rule, and match the selected root-cause class;
- every change assessment must reference report Evidence, reproduce its declared kinds/count rule, and match the selected root-cause class;
- every context assessment must reference report Evidence, reproduce its declared kinds/references/count rule, and match the selected root-cause class;
- signal-plan steps must account for request candidates exactly once in deterministic order, use contiguous positions, and have counts matching their decisions;
- a `risk-aware-v2` plan must record exactly one later risk-ordered accepted candidate promoted from `budget-exhausted`, a recognized provider-gap trigger, positive remaining capacity, and two iterations;
- baseline and evaluation windows must be ordered, non-overlapping subranges of the request scope, and a reported comparison must match its declared calculation and unit;
- selected tools and authority must remain within all applicable upper bounds; and
- usage counters must be checked against the accepted budgets and any stricter policy limits; and
- `wallTimeSeconds` is the measured non-negative interval from `startedAt` to `completedAt`, capped at the accepted wall-time budget for conservative recovery reporting.

The durable report is authoritative. Optional platform telemetry may project its bounded terminal timing, outcome, terminal reason, tool count, and evidence count as an OTLP span, but exporter success is never report success and trace data cannot replace the report. Identity attributes are disabled by default; prompts, evidence content, hypotheses, provider errors, and credentials are never projected.

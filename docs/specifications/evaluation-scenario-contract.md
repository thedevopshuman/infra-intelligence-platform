# Evaluation scenario contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/evaluation-scenario.schema.json`

An evaluation scenario is an immutable, replayable incident fixture plus a scoring oracle. It measures whether an agent reaches an evidence-backed result under the same graph, timeline, alert, scope, and budgets across repeated runs. It is an offline evaluation artifact, not a control-plane API request and not a source of runtime authority.

## Identity and safety

`metadata.id` is a stable, human-readable scenario identity and `metadata.version` identifies one immutable fixture release. Any graph, evidence, expectation, or scoring change creates a new version. `tenantId` is synthetic fixture scope used to test isolation; it never grants access to a real tenant.

Scenarios contain normalized, credential-free data only. They never include Kubernetes Secrets, environment values, access tokens, signed URLs, raw production logs, or other live customer material. Evidence artifact references are inert logical references. All fixture text and evidence summaries remain untrusted input to the system under evaluation.

## Fixtures

The graph is a closed current-state seed: root UIDs and canonical Resource observations. Platform-UID relationship targets resolve within the graph; provider-native unresolved references may remain explicit. Each timeline is a complete terminal page of ascending immutable observation history for one graph resource, and its latest accepted observation agrees with current graph state.

The alert uses the platform Event contract and its subject resolves in the graph. The bounded InvestigationRequest fixes the question, alert trigger, resource/time scope, allowed tools, evidence types, budgets, and maximum authority. Fixture tenant IDs, referenced resource UIDs, correlation context, and timestamps must agree across graph, timeline, alert, request, and evidence.

Evidence fixtures provide the immutable metadata and summaries that deterministic provider doubles may return. Scenario runners must not treat a fixture's presence as permission to expose it: request scope, policy, evidence handling, and tenant checks still apply.

## Expectations

`rootCauseClass` is a stable evaluation taxonomy value assigned to the leading explanation by a deterministic or policy-approved grader. `affectedResourceUids` identifies the expected impact scope. `requiredEvidenceIds` must support the leading conclusion. `forbiddenEvidenceTypes` are artifacts the run must not retrieve or cite; they test least privilege and data minimization. `redHerringEvidenceIds` are available but immaterial signals and must not support the leading hypothesis. Required evidence and red herrings are disjoint.

The scenario oracle is never supplied to the investigating agent. A runner exposes only fixtures and the InvestigationRequest, then scores the terminal report and tool ledger after completion.

## Scoring

Weights cover root-cause accuracy, required evidence, avoidance of forbidden evidence, red-herring resistance, unsupported certainty, and budget compliance. They sum to 100. `passScore` is necessary but not sufficient: every named hard gate must also pass. A correct guess that violates evidence, authority, or budget constraints cannot pass.

This first contract defines fixtures and scoring intent. Deterministic replay, repeated-run aggregation, evaluator identity/version, and score-result contracts remain later implementation units.

## Validation invariants

In addition to JSON Schema validation:

- every fixture has the scenario tenant and every referenced platform UID resolves in the graph;
- graph UIDs match deterministic Resource identity;
- timeline offsets and recorded times are ascending and observation hashes match their embedded resources;
- the latest accepted timeline observation matches current graph state;
- alert subject, request scope, and evidence resource references resolve in the graph;
- alert and request times fall within the declared investigation range;
- required and red-herring evidence IDs resolve to fixture evidence and remain disjoint;
- forbidden evidence types do not appear as fixture evidence; and
- scoring weights sum to 100.

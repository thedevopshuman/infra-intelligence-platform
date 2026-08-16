# ADR 0024: Select and assess committed log evidence conservatively

**Status:** Accepted

**Date:** 2026-08-16

## Context

ADR 0023 defines backend-neutral historical log evidence but deliberately leaves investigation planning unresolved. Logs are higher-volume and more privacy- and prompt-injection-sensitive than normalized resource state, Kubernetes Events, or numeric metrics. Letting an investigator invent text searches or reason directly over backend output would bypass the existing scope, budget, redaction, and immutable-evidence boundaries.

## Decision

- Add optional ordered `logSelections` to `InvestigationRequest`. Each candidate reuses the closed logical service, severity, exact attribute filter, and output-limit fragments from `LogEvidenceRequest`.
- Inherit authenticated tenant/actor identity, resources, time range, and deadline from the investigation. A candidate cannot provide an endpoint, credential, vendor query language, body search, or wider scope.
- Require `telemetry.logs` within the request evidence upper bound and the existing `telemetry/query` tool capability when those bounds are present.
- Run eligible log selections after Kubernetes Event and metric selections. Each attempt consumes one tool call and each committed result consumes one evidence item.
- Permit a root-cause-scoped candidate to declare only a positive `minRecords` rule with distinct matched and unmatched dispositions. The threshold cannot exceed the candidate's `maxRecords` limit.
- Assess only the committed, tenant-scoped `LogEvidenceResult`. Revalidate its identity, request digest, scope, status, ordering, normalized selectors, counts, and limits before applying the rule.
- Never inspect or classify log body prose in this deterministic slice. Bodies remain untrusted confidential text even after redaction.
- Treat complete evidence as countable, explicit no-data as `no-data`, partial evidence as `incomplete`, and missing, corrupt, or mismatched artifacts as a stable evidence gap.
- Add supporting or contradicting Evidence citations without silently changing the resource-derived root-cause class, confidence, rank, or terminal outcome.

## Consequences

- Investigations can gather and correlate portable historical log evidence without importing LogQL, an OTLP storage assumption, or provider SDK types into the application runtime.
- Scarce budgets favor structured events and numeric metrics before higher-risk log material.
- A record-count rule is intentionally conservative: it proves only that a predeclared structured query met its threshold, not that arbitrary log prose is true.
- The honest no-data backend remains the default; production usefulness still requires a selected historical log adapter and design-partner privacy policy.

## Revisit triggers

Revisit when evaluation scenarios justify structured log templates, trace/span joins, rate or baseline comparisons, confidence updates, prompt-injection-safe semantic analysis, dynamic planning, or a different multi-signal budget order.

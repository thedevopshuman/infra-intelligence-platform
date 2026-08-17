# ADR 0075: Replan investigations only into released bounded capacity

**Status:** Accepted

**Date:** 2026-08-17

## Context

The initial cross-signal plan reserves scarce tool-call and Evidence capacity before provider execution. If a scheduled provider is unavailable or fails before committing Evidence, that reserved Evidence slot can remain unused while a lower-priority candidate—already present in the accepted request—stays deferred for `budget-exhausted`. Leaving that capacity idle reduces investigation usefulness, but unconstrained replanning could synthesize queries, retry ambiguous work, change risk ordering, or widen authority.

## Decision

- Keep `risk-aware-v1` as the fixed initial plan. Replanning may start only after a scheduled candidate returns the stable outcome `provider-error` or `provider-unavailable` without committing an Evidence artifact.
- Reserve capacity for every still-pending scheduled candidate before considering a promotion. Promote only when at least one tool call and one Evidence item remain inside the accepted request budgets.
- Promote at most one candidate per investigation. Select the first `budget-exhausted` candidate in the existing deterministic risk order; do not promote candidates deferred for root-cause mismatch or request upper bounds.
- The promoted candidate must already exist exactly in the accepted request, including candidates frozen from a protected tenant catalog. Replanning cannot create or alter a query, integration, resource, time range, credential request, tool, provider, policy decision, or authority.
- When the provider adapter for an entire signal is absent, do not promote another candidate for that signal. A request-scoped provider error may still allow a later candidate, because its separately authorized integration or selector can have a different outcome.
- Emit `risk-aware-v2` only when a promotion occurs. The final plan marks the candidate `scheduled` and records one bounded `replanning` entry with the triggering selection and stable outcome, promoted selection, original `budget-exhausted` reason, and remaining aggregate capacity. Provider exception text and response details never enter the report.
- Count the adaptive pass as the second investigation iteration. Actual tool calls, Evidence artifacts, and the tool ledger remain the execution authority; the plan remains provenance, not a receipt.

## Consequences

- Recoverable provider gaps can yield one more useful signal without exceeding accepted budgets or inventing authority.
- Identical accepted requests, provider outcomes, and committed Evidence produce the same promotion and report provenance.
- The single-promotion bound avoids cascades and makes evaluation, privacy review, and operator explanation tractable.
- A promoted step may still fail, be cancelled, or miss the deadline. No further promotion occurs in that investigation.

## Revisit triggers

Revisit after design-partner evidence shows a need for more than one promotion, when provider outcomes can prove integration-specific retry safety, or when a learned planner can be evaluated without weakening deterministic authority and provenance.

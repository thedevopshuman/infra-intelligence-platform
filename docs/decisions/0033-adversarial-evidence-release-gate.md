# ADR 0033: Gate releases on adversarial evidence containment

**Status:** Accepted

**Date:** 2026-08-17

## Context

Runbooks, repository files, logs, Kubernetes messages, plugin output, and future model context can contain text that resembles instructions. Marking that text untrusted is necessary but does not demonstrate that a release preserves the boundary when the text is present.

## Decision

- Let an evaluation scenario identify `adversarialEvidenceIds` and case-insensitive `prohibitedOutputFragments` that actually occur in those fixture summaries.
- Add `instructionBoundary` as a weighted component and `instruction-boundary` as an available hard gate.
- Fail the component if any prohibited fragment appears anywhere in the structured terminal report.
- Retain independent root-cause, required/forbidden evidence, red-herring, unsupported-certainty, and budget checks. Avoiding text echo alone cannot hide behavioral diversion.
- Keep the scoring oracle outside the investigation request and runtime. It cannot become an instruction source or grant authority.
- Test live repository-context and log paths with instruction-shaped text, verifying that protected Evidence may retain redacted data while report classifications and output remain unaffected.

## Consequences

- A release can be rejected for a concrete instruction-boundary regression even when its narrative looks plausible.
- The first gate is deterministic and model-neutral, so it applies now and can wrap future model-backed agents.
- Exact fragments deliberately favor auditability over semantic coverage; paraphrase and multilingual adversarial suites remain additional work.

## Revisit triggers

Revisit when a model provider is selected, when multi-turn tools are introduced, or when design-partner threat modeling supplies broader multilingual and indirect-injection corpora.

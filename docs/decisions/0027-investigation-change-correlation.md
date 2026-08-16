# 0027 — Investigations assess committed, value-minimized changes

**Status:** Accepted

## Context

Operators need to know whether a rollout, scale, relationship, configuration, or status change preceded an incident. Resource history can contain sensitive configuration and image values, and a truncated history scan cannot prove that no relevant change occurred.

## Decision

Investigation requests may declare bounded `changeSelections` using the public resource-change query and limit fragments. The investigator inherits tenant, actor, resources, time range, and deadline from the accepted investigation; callers cannot widen them inside a selection.

The deterministic runtime evaluates only the committed `ResourceChangeEvidenceResult`. It revalidates request identity, digest, scope, ordering, summaries, hashes, source cursor shape, and output limits before applying the predeclared count rule. Complete evidence may support, contradict, or remain neutral. No-data remains explicit. Partial evidence is always incomplete, and corrupt or mismatched evidence becomes an unknown. Only change IDs and counts enter the report; before/after values do not.

## Consequences

- Change correlation is reproducible and independently auditable.
- Truncation cannot be misrepresented as absence.
- An adapter or stored artifact cannot inject a conclusion through raw configuration text.
- Change evidence enriches citations but does not silently alter the resource-derived classification or confidence.

# ADR 0126: Expose AI savings findings through a bounded tenant read model

**Status:** Accepted
**Date:** 2026-09-06

## Context

The AI savings engine already commits immutable, evidence-backed context-growth,
retry-amplification, and qualified expensive-model findings. Grafana can show
their aggregate projection, but the product console and SDKs have no public way
to read the owning records. Recomputing findings during a request would couple
collection, price calculation, and intelligence, while copying evidence data
into the response would weaken privacy and authority boundaries.

## Decision

1. Add a newest-first `AiSavingsFindingPage` over committed finding records.
2. Require a canonical half-open `evaluatedAt` interval of at most 31 days and
   a page size of at most 100.
3. Bind opaque pagination cursors to the authenticated tenant and exact
   interval, then revalidate every stored item and its ordering before return.
4. Reuse `ai-economics:read` for the allocation report and savings page because
   both expose the same bounded AI economics metadata class. Tenant identity is
   never accepted from the query.
5. Return opaque evidence identifiers without dereferencing their records.
   Evidence reads retain their own authority checks.
6. Preserve all monetary states and the mandatory `requiresValidation: true`
   recommendation. The read surface cannot propose, approve, or execute work.

## Consequences

The console and SDKs can show an explainable potential saving from the durable
source record without a provider call, content access, or inline rule
execution. Cross-tenant and cross-window cursor reuse fails. A client must
continue to distinguish calculated estimates from unpriced or unresolved
findings, and must obtain separate authority to inspect referenced evidence or
perform any action.

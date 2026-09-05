# ADR 0112: Minimized AI price-catalog qualification evidence

**Status:** Accepted
**Date:** 2026-09-06

## Context

The AI cost engine already separates immutable usage from data-driven pricing
and leaves unmatched or overlapping prices unresolved. Production operators
still need evidence that one exact protected catalog is recent enough and
covers the commercial scopes they intend to operate. Embedding rates or the
complete target list in a report would unnecessarily expose negotiated
commercial data.

Selecting an authoritative AWS, OpenAI, reseller, or private-contract source
is customer-specific and remains unresolved. The platform must not hide that
decision behind an automatic scraper.

## Decision

Add a protected, content-addressed
`AiPriceCatalogQualificationPolicy` and an immutable
`AiPriceCatalogQualificationReport`.

The deterministic qualifier accepts an exact catalog, policy, time, and one
closed qualification level. It checks the source profile, freshness through
the report validity window, publication order, global entry overlap, and exact
coverage of every required scope. Verification reconstructs the complete
report and requires the exact catalog and policy digests.

The report is minimized: it contains no prices, model identifiers, source
locator, credentials, or required-scope details. Fixture pricing can qualify
only at `offline-static`; `production-catalog` rejects it. Qualification makes
no invoice, source-authority, or organizational approval claim.

## Consequences

- Catalog review becomes executable and tamper-evident without importing a
  provider SDK into the kernel.
- Missing and ambiguous production coverage are visible before cost
  calculation rather than discovered only from downstream records.
- A report can be shared with release or deployment evidence without exposing
  negotiated rates.
- Customers must still choose and approve their authoritative source and rate
  material.
- Runtime enforcement of a current production report remains a separate,
  explicit promotion boundary.

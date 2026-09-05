# ADR 0114: Exact AWS Bedrock public price import

**Status:** Accepted
**Date:** 2026-09-06

## Context

The cost engine and promotion gate accept provider-neutral catalogs, but
manually transcribing public prices leaves their provider source and decimal
conversion outside executable evidence. Scraping the human pricing page is
fragile. Inferring model identity or billing meaning from descriptions would
silently couple provider wording to cost. Embedding AWS pricing calls in the
worker would also mix collection, pricing, network availability, and serving
authority.

## Decision

Add an offline `AmazonBedrock` adapter for the AWS Price List Bulk API JSON
offer. A protected content-addressed policy maps every output scope and all five
canonical token rates to exact SKU, term, dimension, unit, and expected product
attributes. The adapter does not interpret descriptions or guess absent
commercial dimensions.

The optional acquisition command is restricted to the official pricing host
and offer path, uses verified HTTPS without redirects or environment proxies,
and retains bounded raw bytes. Import exact-matches every policy reference and
converts decimal USD rates to integer subunits without floating point or
rounding. It emits an immutable provider-neutral catalog and a minimized
content-addressed report. Re-verification repeats the transformation from the
retained bytes and exact-compares both outputs.

Import, qualification, organizational approval, and runtime use remain four
separate boundaries. The import report proves transformation; ADR 0112 proves
freshness/non-overlap/scope coverage; customer change control records approval;
ADR 0113 enforces the resulting exact qualification at runtime. The adapter
stays outside domain and application packages, and no AWS SDK enters the
kernel.

## Consequences

- The first executable authoritative public-price source is bounded to the
  `AmazonBedrock` bulk offer and USD token dimensions.
- Provider catalog changes fail closed until a reviewer updates the exact
  mapping and produces a new policy ID, catalog version, and evidence.
- Rates remain protected configuration. The import report exposes only
  digests, timestamps, versions, and aggregate counts.
- The source can be reproduced offline after acquisition, so worker health and
  model requests never depend on AWS pricing availability.
- The report does not establish private contract rates, discounts, invoice
  agreement, or organizational approval. Those remain explicit separate work.

# AI price-catalog qualification contracts

**Status:** v1alpha1
**Machine contracts:**

- `contracts/schemas/ai-price-catalog-qualification-policy.schema.json`
- `contracts/schemas/ai-price-catalog-qualification-report.schema.json`

These contracts provide minimized, independently reproducible evidence that an
exact `AiPriceCatalog` is fresh, non-overlapping, and complete for the
commercial scopes a tenant has explicitly declared. They do not select a
provider pricing source, fetch pricing, approve negotiated rates, or turn a
calculated estimate into an invoice fact.

## Policy

`AiPriceCatalogQualificationPolicy` is protected tenant configuration. Its
content-derived identifier binds its version and complete specification. The
policy declares:

- the maximum age of source material through the report's `validUntil` time;
- the report validity duration and accepted future clock skew;
- every exact provider, model, region, service tier, routing mode, purchase
  mode, and effective time that must resolve to exactly one catalog entry.

Required scopes are unique and lexically sorted. They contain no prices,
credentials, endpoints, arbitrary labels, or wildcard authority. Changing a
scope or threshold creates a different policy identifier.

## Report

`AiPriceCatalogQualificationReport` binds the catalog and policy by canonical
SHA-256 digest. It exposes only catalog identity, source kind/hash/timestamps,
currency, counts, fixed checks, and an aggregate status. It deliberately omits
rates, model IDs, source locators, credentials, and the policy's required
scope list.

The five ordered checks are:

1. `source-profile`: the source locator is an absolute credential-free HTTPS
   or URN reference; production rejects fixtures and requires HTTPS for a
   provider-published source;
2. `source-freshness`: source material remains inside the policy age limit for
   the complete report validity window;
3. `publication-order`: retrieval precedes catalog publication, and neither
   publication nor retrieval is unreasonably in the future;
4. `non-overlapping-entries`: no two entries overlap across intersecting
   provider/model/commercial dimensions and effective time;
5. `required-scope-coverage`: every protected required scope matches exactly
   one entry.

`offline-static` may qualify an explicitly marked fixture for deterministic
testing. It is not production evidence. `production-catalog` always rejects a
fixture. A failed check produces an immutable `unqualified` report with a
stable error code and aggregate measurement; it never silently drops a scope
or selects one of multiple prices.

The report identifier is the first 32 hexadecimal characters of the canonical
report content digest with the `apq_` prefix. Verification recalculates the
entire report from the supplied exact catalog and policy, validates the
content-derived identifier, and can require that the evaluation time falls in
`[generatedAt, validUntil)`.

## Runtime promotion binding

The cost worker can require production qualification as a protected deployment
gate. Its closed wrapper contains exactly `policies` and `reports` arrays, with
one policy and one report for every configured catalog tenant. Startup
recalculates each report against the exact catalog and policy, requires
`qualified` status at the `production-catalog` level, and rejects fixture mode,
missing tenants, duplicates, or unused evidence.

Every cost pass recalculates the report before catalog registration and requires
the worker clock to fall in `[generatedAt, validUntil)`. A stale or altered
report therefore stops new pricing work without requiring a restart. This gate
is required by the current `production-ai-finops-v1` Helm profile; disabling it is a
development-only posture and cannot pass that preflight. See
[ADR 0113](../decisions/0113-runtime-ai-price-catalog-promotion.md).

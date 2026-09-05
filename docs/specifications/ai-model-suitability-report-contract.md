# AI model suitability report contract

**Status:** Accepted v1alpha1 contract

**Machine contract:** `contracts/schemas/ai-model-suitability-report.schema.json`

`AiModelSuitabilityReport` is a protected, immutable attestation that one
candidate model passed workload-specific comparison gates against one reference
model. It is an input to deterministic savings evaluation, not a provider claim
and not permission to change a model.

## Boundary

The report fixes the tenant, provider, candidate and reference models, region,
service, deployment environment, workload profile, evaluator version, source
digest, evaluation time, and expiry. The exact ordered gates are quality,
latency, safety, and compliance. Every gate must be `passed` and must include a
bounded sample count and a digest of its external result.

The source artifact and gate results remain customer-controlled. IIP validates
the closed report and its digests but does not claim to reproduce an external
evaluation system. `operator-attested` therefore means a protected tenant
administrator accepted the source; `test-fixture` is non-production evidence.

## Privacy and safety

The report contains no prompts, responses, tool arguments, retrieved documents,
or evaluation examples. Its content flags are fixed to `false`. Source locators
must be absolute `https` or `urn` identifiers without user information, query
parameters, or fragments, and only hashes, counts, identifiers, and pass/fail
outcomes enter the platform ledger.

A report is valid for at most 90 days. The candidate and reference model must be
different. `metadata.id` is `ams_` plus the first 32 hexadecimal characters of
the canonical SHA-256 digest of the complete report after removing only
`metadata.id`.

## Use by the savings engine

An expensive-model profile embeds exactly one report supplied through the
protected savings configuration. The worker registers it immutably before
evaluation. A finding may be emitted only while the report is valid and only
when its tenant and complete scope match the profile. Persistence revalidates
the stored report and exact usage/cost cohort before committing the finding.

The resulting recommendation remains advisory and requires validation. A stale,
missing, conflicting, test-only in production, or scope-mismatched report
produces no finding and no monetary claim.

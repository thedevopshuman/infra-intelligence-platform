# ADR 0115: Tenant-bound additive evidence redaction

**Status:** Accepted
**Date:** 2026-09-06

## Context

The evidence pipeline already removes common credentials before hashing and
persistence, but its fixed global behavior cannot express a customer's choice
to remove additional personal or network identifiers from selected evidence
types. Allowing arbitrary regular expressions would introduce denial-of-service
risk, ambiguous behavior, accidental secret custody in configuration, and
difficult-to-reproduce evidence. A configurable policy must also not provide a
switch that weakens the mandatory secret baseline.

## Decision

Add a protected, content-addressed `EvidenceRedactionPolicy` for one exact
tenant. Each rule selects exact evidence types and a closed set of bounded
built-in value classes. The first classes are email address and validated IPv4
address. Rules are additive: credential-field, secret-assignment, Bearer, and
private-key inspection always runs and cannot be disabled.

Accept at most one policy per tenant at process startup. Reject unordered,
duplicate, overlapping, unknown, or malformed configuration before serving.
Pass the authenticated tenant—not a payload label—to the redactor, and use no
cross-tenant default. Record only policy ID and version in Evidence handling
metadata, then hash and persist the final redacted bytes through the existing
atomic evidence boundary.

Distribute the policy set to every evidence-producing workload through one
existing Kubernetes Secret reference. API, workflow worker, and isolated OTLP
receiver use the same immutable policy generation. No policy body, matched
value, or detector expression is exposed over OpenAPI.

## Consequences

- Customers gain deterministic extra minimization without a new provider SDK,
  proxy, content store, or inference-path dependency.
- Built-in credential redaction remains active for configured, unconfigured,
  and unmatched tenants and evidence types.
- Exact policy provenance is visible on new Evidence without mutating history.
- Rotation requires a new version/content ID and workload rollout; hot reload
  is intentionally deferred until an authenticated distribution mechanism is
  selected.
- Custom regex, arbitrary literal replacement, IPv6, phone-number, free-text
  entity recognition, and model-based classification remain outside this
  bounded first profile.

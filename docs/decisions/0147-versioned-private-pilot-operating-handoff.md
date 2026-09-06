# ADR 0147: Ship a versioned private-pilot operating handoff

**Status:** Accepted

**Date:** 2026-09-06

## Context

The release bundle already carries deployable images, a Helm chart, public
contracts, SDKs, instrumentation, checksums, and supply-chain attestations.
Customer qualification is spread across intentionally independent runbooks so
each tool retains only its owning authority. A design partner should not have
to infer their order or mistake a technical readiness aggregate for a staffed
support commitment or customer approval.

Support contacts, security recipients, response objectives, customer
identifiers, and protected configuration cannot safely be hard-coded into a
portable open-source artifact. The repository also must not add product
analytics or a vendor feedback endpoint merely to operate a pilot.

## Decision

1. Define one accepted first private-pilot technical scope and one ordered
   onboarding/operation/decommissioning guide while preserving every owning
   qualification runbook.
2. Add root support and security policies that prohibit public sensitive-data
   disclosure and require named external owners and private channels before a
   customer pilot starts. Do not invent contact details or service levels.
3. Use the existing privacy-bounded OpenTelemetry export and minimized reports
   for a customer-owned pilot scorecard. Send no telemetry or feedback to the
   project automatically.
4. Package `SECURITY.md`, `SUPPORT.md`, and the complete documentation tree as
   a versioned `private-pilot-operating-handoff` artifact in every current
   release bundle.
5. Bind the archive by manifest size and digest plus `SHA256SUMS`. Inspect its
   structure during finalization and transport verification: require the pilot,
   support, feedback, diagnostic, readiness, release, and deployment documents;
   reject unbounded content, path traversal, duplicates, links, special files,
   and the wrong version prefix.
6. Keep the role optional in the `v1alpha1` schema so release manifests before
   application `0.84.0` remain representable. Require it in the repository
   verifier for `0.84.0` and later; the current builder always emits it.

## Consequences

- A transported candidate contains the exact revision's operating and policy
  documentation, and verification detects substitution or an incomplete
  handoff.
- The handoff makes the pilot procedure navigable without combining provider,
  traffic, disruption, installation, signing, or approval authority.
- No customer contact, support promise, feedback payload, protected profile,
  or live environment evidence enters the bundle.
- Actual partner operation and acceptance, established staffed channels,
  response objectives, representative traffic, production certification, and
  public license/legal/brand decisions remain external gates.

## Alternatives considered

- Linking only to mutable online documentation was rejected because it would
  not bind the operating instructions to the installed candidate.
- Embedding customer contacts and response objectives was rejected because
  they are customer- and organization-specific sensitive commitments.
- Adding a project analytics collector was rejected because it would violate
  the privacy-first, customer-controlled OpenTelemetry boundary.

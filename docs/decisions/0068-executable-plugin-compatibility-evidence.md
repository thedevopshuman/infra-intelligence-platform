# ADR 0068: executable plugin compatibility evidence

**Status:** Accepted
**Date:** 2026-08-17

## Context

A plugin semantic version, protocol string, and successful image build do not
show which host profile actually ran. The signed runner now has distinct
offline and host-mediated read paths, and customer operators need evidence that
binds those paths to exact plugin, bridge, SDK, application, source, Docker, and
architecture identities. A manually edited compatibility table would drift and
could overstate live provider or action support.

## Decision

Generate a versioned `PluginCompatibilityReport` from the real Docker
conformance runner. Execute the example plugin once without mediation and once
with the invocation-local mediated-read boundary, validate both outputs against
the public result contract and golden resource result, and record only closed
check identifiers and immutable digests.

Run this matrix in a separate Docker-enabled CI job. The repository runner
removes stale output first, emits a positive report only after every check
passes, and exits nonzero otherwise. The contract can represent failed checks
with stable error codes for broader certification tooling. Record dirty source
state explicitly and never include credentials, endpoints, customer identity,
or provider payloads. The matrix is conformance evidence for the exact reported
host; it is not a universal support or interoperability claim.

## Consequences

- Plugin authors and operators receive a machine-readable answer about the
  exact profile that was exercised.
- Offline and mediated execution cannot be conflated in one generic pass flag.
- CI detects drift in signing, sandboxing, socket mediation, SDK framing, and
  output contracts through the same runnable example.
- Live customer provider/credential-broker qualification, multi-host release
  certification, action mediation, and support-policy publication remain
  separate gates.

# ADR 0069: manifest-bound plugin signatures

**Status:** Accepted
**Date:** 2026-08-17

## Context

The first signed runner payload bound plugin identity, protocol, and immutable
image digest, but not the rest of the manifest. A copied valid signature could
therefore accompany modified permission, capability, interface, entrypoint, or
configuration declarations. Session digests detected changes after a session
was issued, but the installation trust root did not authenticate the declaration
used to open that session. Building separate least-authority compatibility
profiles made this gap directly observable.

## Decision

Require `iip.plugin-signature/v2` for executable plugins. Remove the complete
`spec.artifact.signature` property, canonicalize the remaining manifest, and
record its SHA-256 as `signature.manifestDigest`. Sign a closed payload containing
that digest plus the plugin identity, protocol, and immutable artifact
descriptor. The runner recomputes the unsigned-manifest digest before it accepts
publisher trust or evaluates permissions.

Keep the legacy image-only descriptor schema-valid for migration and inventory
readers, but reject it in application `0.42.0` and later execution. Publishers
must re-sign; a `v1` signature cannot be translated because it never
authenticated the missing declaration fields.

## Consequences

- Permission, capability, interface, entrypoint, publisher, and configuration
  changes invalidate publisher trust before execution.
- Offline and mediated conformance rows can safely use distinct signed
  least-authority manifests for one immutable image.
- The migration is intentionally execution-breaking for legacy signatures and
  has explicit re-signing guidance.
- Publisher-key governance, revocation, registry promotion, and external
  transparency policy remain production gates.

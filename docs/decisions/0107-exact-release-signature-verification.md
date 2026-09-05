# ADR 0107: Verify published release indexes against exact organizational trust

**Status:** Accepted
**Date:** 2026-09-06

## Context

The release bundle proves checksums, OCI graph integrity, platform membership,
SBOMs, provenance, packaged installation, and one supported N-1 transition. It
is deliberately unsigned and therefore does not prove who published the OCI
indexes. A tag, a successful `cosign verify` exit code without an exact signer
constraint, or a caller-supplied digest would not close that identity gap.

Repository hosting and the organizational release identity are not yet
accepted. The neutral project must provide the verification boundary without
inventing either value or weakening production verification to make a local
profile pass.

## Decision

1. Define an operational `ReleaseSignaturePolicy` for exactly the control-plane
   and plugin-mediation bridge repositories. The production profile binds each
   repository to one or more exact certificate identities and exact OIDC
   issuers. The checked example contains visible placeholders and is rejected
   by the promotion command until an organization replaces them.
2. Verify only `repository@sha256:indexDigest` references derived from an
   already verified release manifest. Tags and caller-selected artifact digests
   are not verification inputs.
3. Use Cosign's ordinary claim checking. Production keyless verification
   requires transparency verification and never uses
   `--check-claims=false`, an insecure registry option, or a transparency-log
   bypass.
4. Pin one exact supported Cosign version in the policy. The executable local
   compatibility profile additionally pins the official Cosign container by
   multi-platform digest and proves cryptographic verification plus tamper
   rejection in an isolated, no-network container.
5. Permit an exact digest-bound public key only in the explicit
   `local-public-key-v1` profile. Its transparency bypass is visible in the
   report, its terminal status is `local-only`, and it can never satisfy
   `--require-promotable`.
6. Emit a content-identified `ReleaseSignatureVerificationReport` bound to the
   clean source revision, verified manifest digest, policy ID/generation/digest,
   exact OCI index digests, and exact verifier version. Retain only trust IDs,
   counts, stable results, and stable error codes; repository names,
   certificate identities, issuers, public-key paths, credentials, and raw
   provider output do not enter the report.
7. Keep the policy and report outside server, OpenAPI, and SDK boundaries. The
   report is environment-specific promotion evidence stored beside the
   immutable release bundle, not inserted into it after checksums are finalized.

## Consequences

- production promotion can fail closed on the publisher as well as artifact
  integrity;
- the control plane and trusted plugin bridge cannot be signed or substituted
  independently without both exact checks succeeding;
- a local public-key exercise cannot be presented as organizational identity
  evidence;
- changing repository ownership, workflow identity, issuer, signing system, or
  Cosign version requires a reviewed policy generation;
- the organization must still accept its repository, release workflow identity,
  registry permissions, signing ceremony, and trusted distribution channel.

## References

- [Sigstore: verifying signatures](https://docs.sigstore.dev/cosign/verifying/verify/)
- [Cosign releases](https://github.com/sigstore/cosign/releases)

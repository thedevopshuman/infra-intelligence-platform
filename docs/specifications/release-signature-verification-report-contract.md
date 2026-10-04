# Release signature verification report contract

**Status:** v1alpha1 executable promotion evidence

`ReleaseSignatureVerificationReport` is minimized evidence that both published
OCI indexes from one verified release manifest were checked against one exact
release trust policy. It is written beside the release bundle after registry
publication and signing; it never modifies the bundle or its checksum set.

The report binds:

- its content-derived `rsv_` identifier and generation timestamp;
- the clean checkout revision and matching release revision;
- release version and recomputed manifest digest;
- policy ID, generation, canonical digest, and configured Cosign version;
- observed platform, Python version, and exact Cosign version; and
- the two ordered artifact roles, exact manifest index digests, selected
  non-secret trust IDs, signature counts, and results.

Nine ordered checks cover bundle integrity, policy binding, immutable
references, tool version, both signatures, exact trust, transparency, and
output minimization. Summary counts and overall status are derived from those
checks. Report validation rejects reordered or invented checks, mismatched
source identity, inconsistent tool versions, signature/check disagreement,
incorrect transparency semantics, summary tampering, and an invalid
content-derived ID.

`verified` means both keyless signatures, exact trust, and transparency checks
passed. `local-only` means both signatures passed under the explicit local
public-key profile while transparency was intentionally not run. Any failed
check produces `rejected`. Only `verified` may pass the production promotion
gate, and that gate additionally requires a clean current checkout.

When the promotion gate rejects a written report, the CLI prints only the
failed check IDs and stable error codes before `report.not-promotable`.
The manual signature-only recheck emits no report of this kind: it has no
verified bundle and cannot supply promotion evidence.

The report does not retain repository names, certificate identities, OIDC
issuers, public-key paths, credentials, raw Cosign output, or provider error
text. It proves the stated signature observation at one point in time; registry
availability, revocation after verification, vulnerability policy, customer
installation, and trusted digest distribution remain separate controls.

See [ADR 0107](../decisions/0107-exact-release-signature-verification.md) and
the [release procedure](../operations/release-artifacts.md).

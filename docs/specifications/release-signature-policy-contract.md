# Release signature policy contract

**Status:** v1alpha1 operational release contract

`ReleaseSignaturePolicy` is the protected trust input used to qualify the two
published OCI indexes in a verified release bundle. It is not a runtime tenant
contract, API resource, or SDK type.

The `sigstore-keyless-v1` profile contains:

- an exact supported `cosignVersion`;
- `transparencyMode: required`;
- exactly two ordered artifacts: `control-plane-image` followed by
  `plugin-mediation-bridge-image`;
- one repository without a tag or digest for each artifact; and
- one to four exact certificate identity and OIDC issuer candidates, each with
  a non-secret stable trust ID.

The verifier obtains the artifact digest only from the already verified
`release-manifest.json` and constructs `repository@sha256:...`. A policy cannot
select another digest, use a tag, or redirect one artifact role to the other
repository. Candidate identities are alternatives for a controlled signing
transition; accepting any candidate still requires the exact identity and
issuer pair.

The checked example deliberately uses `replace-me` values. It is schema-valid
documentation but the production command rejects placeholder repositories and
identities. The accepted organization must commit a new policy generation with
its real repository and signer identity before promotion.

`local-public-key-v1` exists only for the executable cryptographic compatibility
profile. Every public-key file is absolute and content-bound by SHA-256. This
profile requires `transparencyMode: disabled-local-only`; it is never
promotable, even when both signatures verify.

The policy contains no private key, credential, token, certificate private
material, registry password, or customer data. A policy generation is immutable
release-governance input. Repository, signer, issuer, key, or verifier changes
create a new generation and must be reviewed before use.

See [ADR 0107](../decisions/0107-exact-release-signature-verification.md) for
the authority and failure semantics.

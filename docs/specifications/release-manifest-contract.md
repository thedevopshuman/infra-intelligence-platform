# Release manifest contract

**Status:** v1alpha1 local release evidence

The release manifest binds one committed source revision to the
customer-installable control-plane image, trusted plugin-mediation bridge
image, Helm chart, public contract bundle, Python and TypeScript SDK packages,
the separately versioned Bedrock OpenTelemetry instrumentation source, and a
private-pilot operating handoff containing the exact revision's documentation
plus support and security policies, and a persistent community installation
source kit.
Every artifact records its exact filename, role, media type, byte length, and
SHA-256 digest. `SHA256SUMS` separately covers all artifacts and the manifest
itself so transport corruption or substitution fails verification.

The `image` and `pluginMediationBridgeImage` entries independently record each OCI index and Linux platform manifest by digest. Every platform of both images must carry an in-toto SPDX SBOM attestation and SLSA v1 provenance attestation inside its OCI layout. Both images must expose the same declared platform set. The local verifier reads both OCI descriptor graphs and attestation statements; filenames or successful image builds alone are not sufficient evidence.

`pluginMediationBridgeImage` and its `plugin-mediation-bridge-image` artifact role are additive in `v1alpha1`, so manifests produced before application `0.41.0` remain schema-valid. The repository verifier requires both fields for `0.41.0` and later bundles. A current bundle cannot silently omit the trusted relay used by the no-network runner.

`sourceDate` is the committed revision's timestamp rather than bundle wall-clock time. The chart `appVersion` must equal the application version before packaging, and source archives are produced from the same clean Git revision. These controls make a bundle traceable and make non-image artifacts reproducible from the revision; they do not promise byte-for-byte reproducibility of a network-resolved container build.

`bedrockInstrumentationVersion` binds the adapter archive filename and its
internal package version. The archive is not installed in the IIP control-plane
image; it is an optional exact-version dependency for customer Python
applications using the qualified Bedrock path.

`private-pilot-operating-handoff` is an additive portable artifact role. The
current builder archives `SECURITY.md`, `SUPPORT.md`, and the complete `docs/`
tree under one versioned prefix. Finalization and transport verification reject
an oversized archive, path traversal, duplicate entries, links or special
files, a wrong prefix, or omission of the pilot scope, onboarding, feedback,
diagnostic, readiness, release, Helm, support, or security documents. The role
is optional at the `v1alpha1` schema level so manifests before application
`0.84.0` remain schema-valid. The repository verifier requires it for `0.84.0`
and later, and current builds always include and checksum it.

The handoff describes the technical process but does not establish a staffed
support/security channel, customer approval, response objective, or production
claim. Those values remain environment-specific and must not be embedded in a
portable bundle.

`community-installation-source` is an additive portable artifact role with
filename `infra-intelligence-community-<version>.tar.gz`. Current builds must
include it; finalization and transport verification inspect its bounded,
link-free archive, required installer/runtime/deployment/operating files,
license/notice material, and matching packaged application version. It contains
committed source only, not operator state, credentials or a preinitialized
database. See [ADR 0162](../decisions/0162-versioned-community-installation-kit.md).
The role remains optional when verifying historical manifests, including
previously generated `0.84.0` bundles. The archive does not turn an older
published learning release into a persistent installation release.

The kit enables operation outside a Git checkout. It still requires the
documented host Python dependencies, trusted local Docker/Compose, and reviewed
configuration. It is not a binary-only installer or an offline dependency
mirror. An operator-selected image must be independently authenticated;
packaging source is not publication, a fresh-host qualification, or a supported
upgrade. The archive role is consumed by release tooling, not serving API or
SDK methods, so it adds no runtime authority or OpenAPI operation.

The reference bundle is deliberately marked `unsigned`. Checksums, SBOM, and provenance describe integrity and construction but do not authenticate a publisher. A production release must publish and sign both OCI indexes using the organization's accepted release identity, verify each signature and its transparency evidence, and distribute the verified digests through a trusted channel. The neutral repository cannot select or claim that external identity before hosting and governance decisions are accepted.

Executable install and upgrade observations are recorded in a separate
[release qualification report](release-qualification-report-contract.md). That
report binds back to this manifest by a recomputed digest but remains outside
the finalized bundle because its claims describe one measured environment.

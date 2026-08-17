# Release manifest contract

**Status:** v1alpha1 local release evidence

The release manifest binds one committed source revision to the customer-installable control-plane image, trusted plugin-mediation bridge image, Helm chart, public contract bundle, and Python and TypeScript SDK packages. Every artifact records its exact filename, role, media type, byte length, and SHA-256 digest. `SHA256SUMS` separately covers all artifacts and the manifest itself so transport corruption or substitution fails verification.

The `image` and `pluginMediationBridgeImage` entries independently record each OCI index and Linux platform manifest by digest. Every platform of both images must carry an in-toto SPDX SBOM attestation and SLSA v1 provenance attestation inside its OCI layout. Both images must expose the same declared platform set. The local verifier reads both OCI descriptor graphs and attestation statements; filenames or successful image builds alone are not sufficient evidence.

`pluginMediationBridgeImage` and its `plugin-mediation-bridge-image` artifact role are additive in `v1alpha1`, so manifests produced before application `0.41.0` remain schema-valid. The repository verifier requires both fields for `0.41.0` and later bundles. A current bundle cannot silently omit the trusted relay used by the no-network runner.

`sourceDate` is the committed revision's timestamp rather than bundle wall-clock time. The chart `appVersion` must equal the application version before packaging, and source archives are produced from the same clean Git revision. These controls make a bundle traceable and make non-image artifacts reproducible from the revision; they do not promise byte-for-byte reproducibility of a network-resolved container build.

The reference bundle is deliberately marked `unsigned`. Checksums, SBOM, and provenance describe integrity and construction but do not authenticate a publisher. A production release must publish and sign both OCI indexes using the organization's accepted release identity, verify each signature and its transparency evidence, and distribute the verified digests through a trusted channel. The neutral repository cannot select or claim that external identity before hosting and governance decisions are accepted.

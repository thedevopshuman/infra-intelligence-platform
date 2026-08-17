# Release manifest contract

**Status:** v1alpha1 local release evidence

The release manifest binds one committed source revision to the customer-installable control-plane image, Helm chart, public contract bundle, and Python and TypeScript SDK packages. Every artifact records its exact filename, role, media type, byte length, and SHA-256 digest. `SHA256SUMS` separately covers all artifacts and the manifest itself so transport corruption or substitution fails verification.

The image entry records the OCI index and each Linux platform manifest by digest. Every platform must carry both an in-toto SPDX SBOM attestation and SLSA v1 provenance attestation inside the OCI layout. The local verifier reads the OCI descriptor graph and attestation statements; filenames or a successful image build alone are not sufficient evidence.

`sourceDate` is the committed revision's timestamp rather than bundle wall-clock time. The chart `appVersion` must equal the application version before packaging, and source archives are produced from the same clean Git revision. These controls make a bundle traceable and make non-image artifacts reproducible from the revision; they do not promise byte-for-byte reproducibility of a network-resolved container build.

The reference bundle is deliberately marked `unsigned`. Checksums, SBOM, and provenance describe integrity and construction but do not authenticate a publisher. A production release must publish the OCI index to the chosen registry, sign the immutable digest with the organization's accepted release identity, verify that signature and its transparency evidence, and distribute the verified digest through a trusted channel. The neutral repository cannot select or claim that external identity before hosting and governance decisions are accepted.

# Release artifacts and supply-chain evidence

**Status:** Executable unsigned release-candidate path

## Build from one revision

The release builder refuses a dirty worktree. With Docker Desktop, Buildx, Helm, Node/npm, and Python available, run:

```bash
make release-bundle PYTHON=.venv/bin/python
```

The default build produces `linux/amd64` and `linux/arm64` image manifests. BuildKit attaches an in-toto SPDX SBOM and SLSA v1 provenance statement to each platform inside the OCI layout. The base-image index and SBOM generator are digest pinned; dependency updates must intentionally update those pins and pass the normal verification gates.

The build also writes the exact committed source revision into the OCI image label and the process environment. Once deployed, authenticated users can compare `GET /v1/system/version` with the manifest revision; Helm deployments additionally report the configured chart version and immutable OCI digest. A development build explicitly reports `development` and does not claim unverifiable release identity.

The directory under `dist/iip-<version>-<revision>/` contains:

- the multi-platform control-plane OCI image archive;
- the Helm chart, including its strict values schema;
- public JSON Schemas, examples, specifications, and OpenAPI documents;
- a Python SDK source package and a compiled npm package;
- `release-manifest.json` with revision, size, digest, platform, SBOM, and provenance evidence;
- `SHA256SUMS`, covering every artifact and the manifest.

For a quick local development exercise only, `IIP_RELEASE_PLATFORMS=linux/arm64` or `linux/amd64` can limit the image build. A customer release should retain both default platforms unless its published support matrix says otherwise.

## Verify after transport

Run the repository verifier against an unpacked bundle:

```bash
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-0.34.0-0123456789ab \
  make verify-release-bundle PYTHON=.venv/bin/python
```

Verification does not trust the manifest by itself. It recalculates each byte length and SHA-256 digest, compares the exact checksum file, traverses content-addressed OCI descriptors, matches declared platform digests, and requires both accepted attestation predicates per platform. A modified artifact, omitted platform, missing SBOM, missing provenance statement, or rewritten manifest fails closed with a stable release error.

## Production promotion boundary

The local bundle is explicitly unsigned. It proves artifact integrity and build evidence, not who published it. Once repository hosting and organizational release identity are accepted, production promotion must:

1. rerun all local, PostgreSQL, kind, and customer workflow gates against the release revision;
2. publish the OCI index and Helm chart to immutable registry digests;
3. sign the OCI digest with the accepted organizational identity;
4. verify the signature, identity, issuer, and transparency evidence under a checked-in policy;
5. scan the attached SBOM under a documented vulnerability exception policy;
6. distribute only the verified digest and matching manifest/checksums through a trusted release channel.

Deploy that verified OCI index as `image.repository@image.digest` through the Helm values contract. The chart resolves the same immutable reference for its API, worker, OTLP receiver, and schema-migration Job; a mutable tag is only a local-development fallback.

Repository CI pins all third-party Actions by commit SHA, pins its PostgreSQL service by OCI digest, and runs with read-only contents permission and no persisted checkout credential. Dependabot may propose reviewed identity updates. This narrows the build boundary but does not replace release signing or a hermetic organizational runner.

Docker documents the [SBOM attestation](https://docs.docker.com/build/metadata/attestations/sbom/) and [SLSA provenance](https://docs.docker.com/build/metadata/attestations/slsa-provenance/) formats used by the local BuildKit path. The repository does not claim a production signature until the external identity gate is real and independently verifiable.

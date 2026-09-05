# Release artifacts and supply-chain evidence

**Status:** Executable unsigned release-candidate path

## Build from one revision

The release builder refuses a dirty worktree. With Docker Desktop, Buildx, Helm, Node/npm, and Python available, run:

```bash
make verify PYTHON=.venv/bin/python
make test-capacity PYTHON=.venv/bin/python
make test-credential-broker PYTHON=.venv/bin/python
make test-oidc PYTHON=.venv/bin/python
make test-policy-engine PYTHON=.venv/bin/python
make test-otlp-receiver PYTHON=.venv/bin/python
make release-bundle PYTHON=.venv/bin/python
```

Retain the clean-revision capacity, credential-broker, OIDC-issuer,
policy-engine, and OTLP-receiver compatibility reports under `dist/` beside the
release candidate as environment-specific evidence. They are deliberately not
embedded in the portable bundle because their PostgreSQL, host,
container-runtime, PKI fixture, and Collector measurements describe the
certification environment, not every installation target.

The default build produces `linux/amd64` and `linux/arm64` manifests for both the control plane and the trusted plugin-mediation bridge. BuildKit attaches an in-toto SPDX SBOM and SLSA v1 provenance statement to every platform inside each OCI layout. The base-image index and SBOM generator are digest pinned; dependency updates must intentionally update those pins and pass the normal verification gates.

The build also writes the exact committed source revision into the OCI image label and the process environment. Once deployed, authenticated users can compare `GET /v1/system/version` with the manifest revision; Helm deployments additionally report the configured chart version and immutable OCI digest. A development build explicitly reports `development` and does not claim unverifiable release identity.

The directory under `dist/iip-<version>-<revision>/` contains:

- the multi-platform control-plane OCI image archive;
- the separately multi-platform, no-network plugin-mediation bridge OCI image archive;
- the Helm chart, including its strict values schema;
- public JSON Schemas, examples, specifications, and OpenAPI documents;
- a Python SDK source package and a compiled npm package;
- `release-manifest.json` with revision, size, digest, platform, SBOM, and provenance evidence;
- `SHA256SUMS`, covering every artifact and the manifest.

For a quick local development exercise only, `IIP_RELEASE_PLATFORMS=linux/arm64` or `linux/amd64` can limit the image build. A customer release should retain both default platforms unless its published support matrix says otherwise.

## Verify after transport

Run the repository verifier against an unpacked bundle:

```bash
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-0.68.0-0123456789ab \
  make verify-release-bundle PYTHON=.venv/bin/python
```

Verification does not trust the manifest by itself. It recalculates each byte length and SHA-256 digest, compares the exact checksum file, traverses both content-addressed OCI descriptor graphs, matches declared platform digests, requires the same platform set, and requires both accepted attestation predicates per platform. A current bundle with a missing bridge, modified artifact, omitted platform, missing SBOM, missing provenance statement, or rewritten manifest fails closed with a stable release error.

## Install the packaged candidate locally

After verification, prove that the packaged chart and OCI image—not checkout
copies—install on the explicit local Kind cluster:

```bash
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-0.68.0-0123456789ab \
  make test-release-install PYTHON=.venv/bin/python
```

The gate verifies the bundle again, requires its source revision to match the
checked-out gate, loads the host-platform image from the OCI archive, and
deploys the packaged chart by immutable release index digest. The authenticated
runtime report must identify release mode, the exact source revision, chart
version, image digest, and latest migration. It then repeats the two-revision
Helm, TLS ingress, backup, checksum, and isolated restore checks used by the
source-install gate. It refuses non-Kind contexts and removes its disposable
namespace unless explicitly retained for debugging.

## Prove the supported N-1 upgrade locally

For every target release, run the packaged target against the explicitly
selected supported prior revision. The target may retain the same latest
migration or add a newer one; migration regression is rejected:

```bash
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-0.68.0-0123456789ab \
IIP_UPGRADE_FROM_REVISION=22b2e66 \
  make test-release-upgrade PYTHON=.venv/bin/python
```

The gate builds the exact ancestor image and chart, installs them by immutable
digest, and writes a tenant Resource through the authenticated public API. It
then upgrades with the verified target bundle, rolls only the application back
while retaining the forward migration, and upgrades to the target again. Every
stage must report the expected runtime identity and return the same tenant data;
the final database must contain every target migration exactly once. An
independent least-authority pod continuously reads both release identity and the
seeded Resource through the Kubernetes Service; any failed request or unknown
revision fails the gate. This proves bounded in-cluster availability for the
selected N-1 pair, not arbitrary-version compatibility, customer-ingress
behavior, production load, or failure-injected availability.

The target must also complete a deliberately blocked authenticated Resource
read after Kubernetes begins terminating its exact serving pod. This checks the
API's signal handling and active-handler join independently of the Service's
other ready replica. It is one bounded drain case, not a claim about every
customer request duration, streaming protocol, ingress, or load balancer.

## Retain one complete qualification report

Run both packaged profiles in sequence and require their machine-readable
evidence to agree on the candidate and local environment:

```bash
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-0.68.0-0123456789ab \
IIP_UPGRADE_FROM_REVISION=<supported-ancestor> \
  make qualify-release PYTHON=.venv/bin/python
```

The default report is written adjacent to the bundle as
`<bundle>.qualification.json`. Set the absolute
`IIP_RELEASE_QUALIFICATION_REPORT` path to retain it elsewhere. The install gate
starts a new report and the N-1 gate adds its profile only when candidate,
platform, Kubernetes, and Docker identity match. Verify a transported report
and bundle with:

```bash
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-0.68.0-0123456789ab \
IIP_RELEASE_QUALIFICATION_REPORT=/absolute/path/to/report.json \
  make verify-release-qualification PYTHON=.venv/bin/python
```

The verifier recomputes the release-manifest digest from the already verified
bundle and derives the closed-profile summary. It rejects a dirty source,
candidate substitution, invented or reordered check, inconsistent availability
total, migration regression, failed request, or incomplete report. The report
stays outside the bundle so recording an environment observation cannot mutate
the finalized artifacts or `SHA256SUMS`. See the [qualification
contract](../specifications/release-qualification-report-contract.md) and
[ADR 0100](../decisions/0100-environment-scoped-release-qualification-evidence.md).

## Production promotion boundary

The local bundle is explicitly unsigned. It proves artifact integrity and build evidence, not who published it. Once repository hosting and organizational release identity are accepted, production promotion must:

1. rerun all local, PostgreSQL, kind, and customer workflow gates against the release revision;
2. publish both OCI indexes and the Helm chart to immutable registry digests;
3. sign both OCI digests with the accepted organizational identity;
4. verify both signatures, identities, issuers, and transparency evidence under a checked-in policy;
5. scan the attached SBOM under a documented vulnerability exception policy;
6. distribute only the verified digest and matching manifest/checksums through a trusted release channel.

Deploy that verified OCI index as `image.repository@image.digest` through the Helm values contract. The chart resolves the same immutable reference for its API, worker, OTLP receiver, and schema-migration Job; a mutable tag is only a local-development fallback.

Repository CI pins all third-party Actions by commit SHA, pins its PostgreSQL service by OCI digest, and runs with read-only contents permission and no persisted checkout credential. Dependabot may propose reviewed identity updates. This narrows the build boundary but does not replace release signing or a hermetic organizational runner.

Docker documents the [SBOM attestation](https://docs.docker.com/build/metadata/attestations/sbom/) and [SLSA provenance](https://docs.docker.com/build/metadata/attestations/slsa-provenance/) formats used by the local BuildKit path. The repository does not claim a production signature until the external identity gate is real and independently verifiable.

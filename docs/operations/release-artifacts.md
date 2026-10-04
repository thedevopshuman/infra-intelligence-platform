# Release artifacts and supply-chain evidence

**Status:** Executable unsigned candidate with signature and SBOM promotion gates

## Build from one revision

The release builder refuses a dirty worktree. With Docker Desktop, Buildx,
Kind, kubectl, Helm, Node/npm, Cosign, and Python available, the preferred
entry point is one fail-closed command. Select the supported predecessor
explicitly; the workflow never guesses compatibility from Git history:

```bash
IIP_UPGRADE_FROM_REVISION=<supported-ancestor> \
  make qualify-local-release PYTHON=.venv/bin/python
```

It runs source quality, local integration/recovery/availability evidence,
Prometheus parsing of both operational-alert profiles, multi-platform
packaging, packaged install, the selected N-1
upgrade/rollback/re-upgrade, exact-SBOM vulnerability qualification, and the
19-evidence readiness aggregate. The final paths include the candidate's
12-character revision so evidence from different commits cannot silently
share a release identity. It refuses an existing candidate directory rather
than deleting or overwriting prior artifacts. It also removes inherited
IIP/provider/credential variables before setting the exact local toolchain and
report paths, so an unrelated shell credential cannot silently select a live
profile or enter a child process.

The equivalent individual commands remain available for diagnosis and
selective development:

```bash
make verify PYTHON=.venv/bin/python
make test-capacity PYTHON=.venv/bin/python
make test-credential-broker PYTHON=.venv/bin/python
make test-oidc PYTHON=.venv/bin/python
make test-policy-engine PYTHON=.venv/bin/python
make test-external-secrets PYTHON=.venv/bin/python
make test-operational-alerts
make test-customer-operational-alert-qualification PYTHON=.venv/bin/python
make test-otlp-receiver PYTHON=.venv/bin/python
make test-bedrock-instrumentation PYTHON=.venv/bin/python
make test-openai-instrumentation PYTHON=.venv/bin/python
make test-ai-finops PYTHON=.venv/bin/python
make verify-ai-finops-runtime-report PYTHON=.venv/bin/python
make test-backup-restore PYTHON=.venv/bin/python
make test-postgres-continuity PYTHON=.venv/bin/python
make test-deployment-preflight PYTHON=.venv/bin/python
make test-github-context PYTHON=.venv/bin/python
make test-release-signatures PYTHON=.venv/bin/python
make test-release-vulnerabilities PYTHON=.venv/bin/python
make test-release-readiness PYTHON=.venv/bin/python
make release-bundle PYTHON=.venv/bin/python
```

The standalone sustained AI FinOps load profile is intentionally not in that
list of release inputs. Operators and CI environments can exercise and retain
it separately with:

```bash
make test-ai-finops-sustained-load PYTHON=.venv/bin/python
make verify-ai-finops-sustained-load-report PYTHON=.venv/bin/python
```

It proves one bounded fixed-rate synthetic workload through a single local
Docker host, including paced commit-bound replay, exact fixture/engine-scoped
database measurements, inspected running image identities, and exclusion of
concurrent durable local usage from Prometheus convergence. The current
19-input `ReleaseReadinessReport` remains unchanged; the sustained report
cannot satisfy or extend it. Promotion to a mandatory input requires
calibration on supported `linux/amd64` and `linux/arm64` environments plus a
future versioned readiness contract. See the
[standalone qualification runbook](ai-finops-sustained-load-qualification.md).

The one-command workflow also runs the plugin compatibility and three-node
Kubernetes planned-disruption gate required by the aggregate. Its v2 profile
proves API access, durable non-empty receiver intake, and worker completion in
the fully drained state. Generated reports remain outside Git and outside the immutable bundle. See
[ADR 0122](../decisions/0122-one-command-local-release-qualification.md).

Retain the clean-revision capacity, credential-broker, OIDC-issuer,
OIDC-browser, policy-engine, OTLP-receiver, Bedrock
`Converse`/`ConverseStream`, OpenAI compatibility, complete local AI FinOps
runtime, protected GitHub-context, PostgreSQL logical-recovery, and PostgreSQL physical-continuity
qualification reports under `dist/` beside the release candidate as
environment-specific evidence. Verify both recovery
reports against the clean checkout with `make verify-backup-restore-report` and
`make verify-postgres-continuity-report`. These reports are deliberately not
embedded in the portable bundle because their PostgreSQL, host,
container-runtime, PKI fixture, Collector, and database-topology measurements describe the
certification environment, not every installation target.
If the standalone AI FinOps sustained-load profile is run, retain its report
beside these artifacts while preserving its
`single-host-docker-synthetic-ai-economics-load` boundary. It is regression
evidence only, not release-readiness, customer traffic, provider, billing,
failure, backend-lifecycle, long-window SLO, or HA evidence.
The external-secret profile emits no report or secret material; retain its
terminal pass/fail result in the release workflow log. It proves the local
Kubernetes provider handoff, not the selected customer secret backend.
The static deployment-preflight reports prove the shipped non-secret core,
protected GitHub context, and AI FinOps configuration profiles. Before customer installation, generate an
`install-ready` report from the clean release checkout against an explicit
customer context and the exact protected values generation, verify it with
`make verify-deployment-preflight-report`, and retain it beside—not inside—the
release bundle. That report proves prerequisite presence, not external-system
or workload qualification.

After the customer monitoring owner drives the isolated synthetic rule through
firing and recovery, run `make qualify-customer-operational-alerts` and retain
its minimized report outside the bundle. This is customer-environment evidence
for one evaluator and notification route, not local release evidence or a
claim about other routes, human response, or monitoring HA.
For a private AI FinOps pilot, run it after customer deployment qualification
with `ai-finops-v0`; the v2 pilot-readiness assessor requires this exact report
as its tenth source and rejects stale, expired, or crossed environment
evidence.

After installation, run `make qualify-ingress-availability` from the same clean
checkout against the external HTTPS URL and immutable image digest, then run
`make verify-ingress-availability-report`. Retain the aggregate report beside
the preflight and release qualification evidence. It proves one bounded
external path and exact runtime identity, not continuous or regional
availability.

The default build produces `linux/amd64` and `linux/arm64` manifests for both the control plane and the trusted plugin-mediation bridge. BuildKit attaches an in-toto SPDX SBOM and SLSA v1 provenance statement to every platform inside each OCI layout. The base-image index and SBOM generator are digest pinned; dependency updates must intentionally update those pins and pass the normal verification gates.

The build also writes the exact committed source revision into the OCI image label and the process environment. Once deployed, authenticated users can compare `GET /v1/system/version` with the manifest revision; Helm deployments additionally report the configured chart version and immutable OCI digest. A development build explicitly reports `development` and does not claim unverifiable release identity.

The directory under `dist/iip-<version>-<revision>/` contains:

- the multi-platform control-plane OCI image archive;
- the separately multi-platform, no-network plugin-mediation bridge OCI image archive;
- the Helm chart, including its strict values schema and value-free external-secret example;
- public JSON Schemas, examples, specifications, and OpenAPI documents;
- a Python SDK source package and a compiled npm package;
- the separately versioned Bedrock OpenTelemetry usage-adapter source package;
- a versioned persistent community installation source kit, including runtime,
  Compose assets, lifecycle tools, pinned requirements and operating guides;
- a versioned private-pilot operating handoff containing `SECURITY.md`,
  `SUPPORT.md`, and the exact revision's complete documentation tree;
- `release-manifest.json` with revision, size, digest, platform, SBOM, and provenance evidence;
- `SHA256SUMS`, covering every artifact and the manifest.

The chart, public-contract and operating-handoff archives include the same
committed root `LICENSE` and `NOTICE`; a downloaded artifact must not depend on
the recipient separately finding the repository license. The community kit
also carries both files. See the [installation-kit guide](community-installation-kit.md)
for its content checks and non-Git installation path. Older verified manifests
can legitimately omit the additive kit role, so select the
`community-installation-source` artifact explicitly instead of assuming every
historical bundle contains it. No current source change adds assets to the
already published `learning-v0.84.0` release.

For a quick local development exercise only, `IIP_RELEASE_PLATFORMS=linux/arm64` or `linux/amd64` can limit the image build. A customer release should retain both default platforms unless its published support matrix says otherwise.

## Verify after transport

Run the repository verifier against an unpacked bundle:

```bash
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-0.84.2-0123456789ab \
  make verify-release-bundle PYTHON=.venv/bin/python
```

Verification does not trust the manifest by itself. It recalculates each byte
length and SHA-256 digest, compares the exact checksum file, traverses both
content-addressed OCI descriptor graphs, matches declared platform digests,
requires the same platform set, and requires both accepted attestation
predicates per platform. It also inspects the declared pilot handoff without
extracting it and rejects missing required operating documents, a wrong
version prefix, traversal, duplicate entries, links, special files, or
unbounded content. A current bundle with a missing bridge or handoff, modified
artifact, omitted platform, missing SBOM, missing provenance statement, or
rewritten manifest fails closed with a stable release error.

The handoff is portable product guidance, not customer evidence. Named owners,
private support/security channels, response objectives, protected profiles,
live reports, and customer acceptance remain outside the immutable bundle; see
the [private-pilot onboarding guide](private-pilot-onboarding.md).

## Install the packaged candidate locally

After verification, prove that the packaged chart and OCI image—not checkout
copies—install on the explicit local Kind cluster:

```bash
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-0.84.2-0123456789ab \
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
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-0.84.2-0123456789ab \
IIP_UPGRADE_FROM_REVISION=48f2168 \
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
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-0.84.2-0123456789ab \
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
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-0.84.2-0123456789ab \
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

## Aggregate the local candidate evidence

After generating the complete clean-revision evidence set listed at the start
of this runbook, create one minimized readiness inventory:

```bash
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-0.84.2-0123456789ab \
IIP_RELEASE_QUALIFICATION_REPORT=/absolute/path/to/release-qualification.json \
IIP_RELEASE_VULNERABILITY_REPORT=/absolute/path/to/release-vulnerabilities.json \
IIP_RELEASE_EVIDENCE_DIR=/absolute/path/to/evidence \
IIP_RELEASE_READINESS_REPORT=/absolute/path/to/release-readiness.json \
  make assess-release-readiness PYTHON=.venv/bin/python
```

The command verifies the bundle and requires all 19 source-bound local reports
to match its exact revision and successful profile. Recompute the inventory
after transport with `make verify-release-readiness-report` and the same five
paths. A successful result is `locally-qualified`, never production-ready or
promotable. The report always lists the eight customer, organizational, live
provider, design-partner, and legal/brand gates that remain external. See the
[contract](../specifications/release-readiness-report-contract.md) and
[ADR 0120](../decisions/0120-aggregate-local-release-readiness-evidence.md).

For a customer cluster, the separate [customer deployment qualification
workflow](customer-deployment-qualification.md) binds the live preflight,
post-continuity health, external ingress, customer OIDC, policy, and
credential-broker prerequisites, API/worker/receiver Eviction evidence, and
PostgreSQL promotion evidence to the same exact release and current namespace
UID/server. That additive report can support its narrow customer prerequisite
gates, but its fixed limitations do not satisfy artifact trust, lifecycle/HA,
live provider, regional, pilot, or governance gates.

## Production promotion boundary

The local bundle is explicitly unsigned. It proves artifact integrity and build evidence, not who published it. Once repository hosting and organizational release identity are accepted, production promotion must:

1. rerun all local, PostgreSQL, kind, and customer workflow gates against the release revision;
2. retain a clean, `install-ready` deployment-preflight report for the exact
   customer values generation and explicit Kubernetes context;
3. qualify the protected GitHub or GitHub Enterprise repository profile when
   it is enabled and retain the clean-revision compatibility report;
4. publish both OCI indexes to immutable registry digests and distribute the
   verified Helm chart inside the signed release bundle;
5. sign both OCI digests with the accepted organizational identity;
6. verify both signatures, identities, issuers, and transparency evidence under a checked-in policy;
7. qualify every exact attached SPDX SBOM under the checked vulnerability
   policy and retain the minimized report;
8. distribute only the verified digest and matching manifest/checksums through a trusted release channel.

The exact-index publication boundary is executable independently of the final
registry namespace. It verifies the bundle, requires the exact version tag and
two distinct repositories on one registry host, copies each OCI layout without
rebuilding, and verifies both the immutable digest and convenience tag after
transport:

```bash
PYTHONPATH=src:sdks/python/src python scripts/release_publication.py publish \
  /absolute/path/to/iip-0.84.2-0123456789ab \
  --control-plane-repository registry.example.test/team/control-plane \
  --bridge-repository registry.example.test/team/plugin-mediation-bridge \
  --tag v0.84.2 \
  --report /absolute/path/to/release-publication.json
```

The report remains explicitly `published-unsigned`; use its immutable
references for signing and installation. `make test-release-publication`
proves digest-preserving transfer and conflicting-tag rejection against an
isolated local registry without using customer credentials. Registry-side tag
immutability and protected release permissions remain required because no
client-side preflight can eliminate a remote race.

### Protected GitHub release path

`.github/workflows/release.yml` turns an accepted `v<application-version>` tag
into a release without rebuilding during registry publication. It requires the
tag commit to equal both the clean checkout and fetched `main` tip, runs
`make verify-workflows` and `make verify`, builds the attested bundle, publishes
both exact OCI indexes to
`docker.io/thedevopshuman/iip` and `docker.io/thedevopshuman/iip-bridge`,
signs their immutable references with GitHub OIDC,
generates the matching exact-identity policy, and then runs the signature and
fresh-database vulnerability gates. Only after those gates pass does it sign
the complete customer archive and create the GitHub release with the manifest,
checksums, Helm chart, and minimized qualification reports. The small
`infra-intelligence-community-<version>.tar.gz` kit is also a direct release
asset, independently signed with `community-kit.sigstore.json`. It is the same
kit already contained in the verified bundle, not a second build.

The pinned Cosign wrapper sets `TUF_ROOT=/tmp/sigstore`, using Cosign's
[supported TUF cache setting](https://github.com/sigstore/cosign/blob/v3.1.2/pkg/cosign/env/env.go).
This keeps trust-metadata writes inside the container's bounded ephemeral
`/tmp` mount while preserving its read-only root filesystem. It does not
replace the embedded trust root, select another mirror, disable signature or
transparency verification, change `HOME`, or mount the operator's home directory.
Each invocation bootstraps its own cache; no host trust cache is reused.

`make verify-workflows` runs the official pinned `actionlint v1.7.12` through
Go and validates every checked workflow, including expression contexts. Go
and access to its public module distribution are maintainer/CI prerequisites,
not installation prerequisites. The ordinary CI verification job runs this
gate before `make verify`; the release job runs it before registry login.
Optional ShellCheck/Pyflakes integrations are disabled in this syntax gate.

The immutable `v0.84.0` tag points to the failed initial publishing attempt:
GitHub rejected job-level `runner.temp` before any release job or image push.
`v0.84.1` corrected that context and uploaded both exact image indexes, but
signing failed when Cosign tried to create `/.sigstore` on its read-only root.
No signed GitHub release was created. Do not use those unsigned registry tags
as accepted installation artifacts, and do not move or overwrite them.
`v0.84.2` carries the signer-cache correction and chart `0.87.2`
(`appVersion: 0.84.2`). It uploaded and signed both indexes, then failed
qualification because its parser rejected Cosign's equivalent `index.docker.io`
claim for the policy's `docker.io` repository. The adapter correction recognizes
only those exact hostnames, without changing the path, digest, identity,
issuer or transparency checks. SDK and Bedrock instrumentation versions are
unchanged. Passing image signatures alone does not prove full publication
qualification or anonymous installation; the protected release must complete.

### Recheck only existing image signatures

Use the manual **release signature recheck** workflow with the original release
tag and both exact published index digests, or run:

```bash
.venv/bin/python scripts/recheck_release_signatures.py \
  --tag v0.84.2 \
  --control-plane-digest sha256:13d15527b3ed7c65ba34ed2b102f15bfbe29a33106ef0ec6953d9bad3361bcfc \
  --bridge-digest sha256:57fad1274d105348fd9ee2ec9ff8177b62ddc5d56688943d31c56b940e2b5565 \
  --cosign scripts/cosign_container.sh
```

These are the already-published `v0.84.2` indexes, not approved install
instructions. The diagnostic requires pinned Cosign 3.1.2, the exact original
tag-workflow identity, GitHub's issuer and transparency verification. Its
GitHub job runs anonymously with read-only permissions and no environment
secrets. It performs no full test suite, image build, image push, signing,
installation, vulnerability scan or GitHub release creation. It does not
produce a `ReleaseSignatureVerificationReport` because it has no verified
bundle; a green recheck is not promotion evidence.

GitHub reruns jobs, not individual steps. The failed release used one job and
uploaded no intermediate bundle/checkpoint. Rerunning it would repeat its
tests and builds and use its unchanged old verifier. Do not use that route
when only a failed-step diagnostic is requested. Completing this release
without rebuilding images requires recovering both complete OCI graphs,
repackaging the original tag's non-image assets and generating a new verified
bundle before the still-required vulnerability and asset-signing steps.
Neither old archive checksums nor original blob-signing identity may be
invented for reconstructed artifacts. See
[ADR 0165](../decisions/0165-exact-docker-hub-signature-alias.md).

### Publication credentials

The job is restricted to `thedevopshuman/infra-intelligence-platform`. In that
repository, open **Settings → Environments → release → Environment secrets**
and configure:

| Secret | Value |
| --- | --- |
| `DOCKERHUB_USERNAME` | `thedevopshuman` |
| `DOCKERHUB_TOKEN` | An expiring Docker Hub Read & Write personal access token, without Delete permission |

Environment scope is recommended, not an extra requirement enforced by the
workflow: GitHub also resolves an existing repository-level secret through
the same `secrets` context. The release job still requires environment approval,
but a repository-level token may be referenced by other authorized workflows
without that approval. Prefer migrating it to environment scope for narrower
access; do not claim the broader copy is protected by the release environment.
GitHub cannot reveal or move an encrypted value: migration requires the owner
to enter the value again. See [GitHub secret scopes](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

Create the token through Docker Account settings → Personal access tokens;
use a 30-day initial expiry and rotate before expiry. A personal token follows
the account's permissions; it is not restricted to just these two repositories.
Enter it directly in GitHub, never in chat, source, a local `.env`, logs or
release assets. See [Docker's PAT instructions](https://docs.docker.com/security/access-tokens/personal-access-tokens/).
The workflow rejects a missing token or wrong username before the build,
authenticates over stdin, isolates Docker configuration in the runner's private
temporary directory and always attempts logout. It does not need GitHub
Packages write permission or a paid Docker plan.

The Ubuntu release runner explicitly prepares ARM64 emulation and a
`docker-container` Buildx builder before packaging both `linux/amd64` and
`linux/arm64` images. The setup Actions use commit pins, binfmt and BuildKit
use digest pins, and Buildx has an explicit version. A bootstrapped builder
must report both platforms before the build starts; setting a desired platform
list is not treated as detected support. Emulation setup is confined to the
ephemeral GitHub runner, not an operator's installed host. See Docker's
[multi-platform workflow](https://docs.docker.com/build/ci/github-actions/multi-platform/)
and [builder configuration](https://docs.docker.com/build/ci/github-actions/configure-builder/).

Before pushing the first release tag, repository owners must:

1. protect the `release` environment with required reviewers and restrict it to
   protected tags;
2. require an exact-version tag ruleset and ensure releases originate at the
   current protected `main` tip;
3. configure both approved Docker Hub repositories as public with immutable
   released version tags and verify the publishing token's scope and expiry;
4. review the checked vulnerability policy, the generated GitHub workflow
   identity, retention, and release-asset visibility; and
5. require the ordinary CI and applicable customer qualification evidence
   before authorizing the tag.

The release job intentionally has no manual-dispatch path. A retry is
idempotent when both version tags still name the verified digests and fails if
it observes a conflicting tag. The workflow does not turn local Kind, fixture,
or offline-provider evidence into customer-environment qualification.

The `release` environment was created with `thedevopshuman` as its required
reviewer, administrator bypass disabled and tag deployment policies for `v*`
and `helm-v*`. The latter was added for the
[independent core chart workflow](helm-development-and-release.md). The
single maintainer may approve their own triggered run; this is explicit human
approval, not independent two-person review. Review these live settings before
each release. Environment tag filtering does not itself protect Git tags from
updates or deletions. No token is required for ordinary anonymous public pulls.

The initial registry/repository protection setup is now applied:

- GitHub ruleset `Immutable IIP release tags` rejects updates and deletion of
  `refs/tags/v*` and `refs/tags/helm-v*`, without bypass actors. New version tags
  are still permitted. The chart preparer additionally requires the exact
  fetched `origin/main` commit; a tag filter is not a semantic-version validator.
- GitHub ruleset `Preserve main history` rejects force-pushes and deletion of
  `refs/heads/main`; ordinary fast-forward development remains permitted. This
  does not enforce a PR review or make a passing CI run optional for release.
- Both Docker Hub repositories use **Specific tags are immutable**, matching
  `^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$`. This protects the release
  version tags without locking unrelated signature/attestation storage tags.

Recheck live protections before publication; administrators can change these
settings. Do not move or replace a failed/withdrawn version tag to another
commit or digest. Correct the source and publish a new version. These settings
are not a substitute for exact-digest signature or runtime verification.

Verify the downloaded archive before extracting it. Substitute the accepted
repository and version in the certificate identity:

```bash
cosign verify-blob \
  --bundle iip-0.84.2-0123456789ab.tar.gz.sigstore.json \
  --certificate-identity \
    https://github.com/OWNER/REPOSITORY/.github/workflows/release.yml@refs/tags/v0.84.2 \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  iip-0.84.2-0123456789ab.tar.gz
```

Then verify `SHA256SUMS` inside the extracted directory and install the chart
with the control-plane `repository@sha256:...` reference from
`release-manifest.json`; never derive installation authority from the version
tag.

The repository implements step 6 without selecting the organization. Copy the
example policy to a reviewed location, replace every placeholder with the
accepted registry repositories and exact release-workflow certificate identity,
increment its generation when trust changes, and run from the same clean
release checkout:

```bash
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-0.84.2-0123456789ab \
IIP_RELEASE_SIGNATURE_POLICY=/absolute/protected/release-signature-policy.json \
IIP_RELEASE_SIGNATURE_REPORT=/absolute/path/to/release-signatures.json \
COSIGN=/absolute/path/to/cosign \
  make qualify-release-signatures PYTHON=.venv/bin/python
```

The command verifies the bundle first, derives both exact index digests from
its manifest, and invokes the policy-pinned Cosign version against
`repository@sha256:...`. The production profile requires exact certificate
identity, exact OIDC issuer, ordinary Cosign claims, and transparency evidence.
It rejects a dirty source, placeholder policy, local key profile, version
mismatch, missing or substituted signature, and inconsistent Cosign output.
It never accepts a caller-provided tag or digest.

The minimized report can be checked for schema, content identity, source
binding, and derived semantic consistency with:

```bash
IIP_RELEASE_SIGNATURE_REPORT=/absolute/path/to/release-signatures.json \
  make verify-release-signature-report PYTHON=.venv/bin/python
```

That second command validates retained evidence; it does not contact the
registry and is not a substitute for rerunning `qualify-release-signatures` at
the production promotion boundary. Keep the policy in reviewed source or an
equivalently protected configuration repository and keep the report in the
release evidence channel. The checked example is intentionally non-promotable.
See the [trust-policy contract](../specifications/release-signature-policy-contract.md),
[report contract](../specifications/release-signature-verification-report-contract.md),
and [ADR 0107](../decisions/0107-exact-release-signature-verification.md).

Step 7 is implemented by the separate vulnerability qualification gate. It
verifies the bundle again, binds every platform subject and SPDX layer digest,
fetches the policy-pinned scanner database with constrained network authority,
and then scans offline:

```bash
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-0.84.2-0123456789ab \
IIP_RELEASE_VULNERABILITY_POLICY=/absolute/protected/release-vulnerability-policy.json \
IIP_RELEASE_VULNERABILITY_REPORT=/absolute/path/to/release-vulnerabilities.json \
  make qualify-release-vulnerabilities PYTHON=.venv/bin/python
```

The production command requires clean source and a `qualified` result. Its
report contains only release/policy/scanner/database identities, platform and
SBOM digests, aggregate severity counts, and used exception IDs. It excludes
raw dependency and vulnerability inventory. Verify retained evidence with
`make verify-release-vulnerability-report`; that check does not rescan or
replace the fresh-database qualification. See the [runbook](release-vulnerability-qualification.md),
[policy contract](../specifications/release-vulnerability-policy-contract.md),
[report contract](../specifications/release-vulnerability-qualification-report-contract.md),
and [ADR 0108](../decisions/0108-sbom-vulnerability-policy-and-qualification.md).

Deploy that verified OCI index as `image.repository@image.digest` through the Helm values contract. The chart resolves the same immutable reference for its API, worker, OTLP receiver, and schema-migration Job; a mutable tag is only a local-development fallback.

Repository CI pins all third-party Actions by commit SHA, pins its PostgreSQL service by OCI digest, and runs with read-only contents permission and no persisted checkout credential. Dependabot may propose reviewed identity updates. This narrows the build boundary but does not replace release signing or a hermetic organizational runner.

Docker documents the [SBOM attestation](https://docs.docker.com/build/metadata/attestations/sbom/) and [SLSA provenance](https://docs.docker.com/build/metadata/attestations/slsa-provenance/) formats used by the local BuildKit path. Sigstore documents [exact identity and issuer verification](https://docs.sigstore.dev/cosign/verifying/verify/). The repository does not claim a production signature until the external identity policy is real and the production qualification command returns `verified`.

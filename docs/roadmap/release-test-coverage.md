# Release test coverage ledger

**Updated:** 2026-10-05

Keep deferred tests visible without running every expensive environment on
every edit. An implementation, an example and an executed test are different
states. Attach exact source/artifact identity and a run reference when closing
an item; old evidence does not automatically qualify new artifacts.

## Checks being implemented now

| ID | Owning boundary | Trigger and required result | State |
| --- | --- | --- | --- |
| REL-001 | CI selection | Unit cases for chart/docs, runtime, mixed, missing/invalid Git base, manual scope; action syntax and pinned actions | Local tests and workflow syntax passed |
| REL-002 | Chart packaging | Exact tag/version/source/main-tip and clean tree, licensed committed archive, immutable canonical image pin, no app-version coupling, no image build; reject collision | Local tests including real Helm packaging passed; image qualification remains a separate release prerequisite |
| K8S-001 | Local core Helm | Explicit local Kind identity, new owned namespace, TLS PostgreSQL, migration, readiness, correct/incorrect auth, exact application/image/chart identity and same-version upgrade | Local arm64 install/upgrade passed; fixture namespace removed |
| REL-007 | Exact-source recovery | Reject mismatched source/tag, dirty source, ignored SDK output contamination, unsafe archive members, incomplete/tampered OCI graphs, extra bundle files and write-capable adapter commands | 40 focused tests passed; actual complete public layouts reconstructed and reverified locally |
| REL-008 | Security correction and publication order | Fixed PyJWT pin across runtime and three existing locks, authentication tests under the updated dependency, vulnerability gate before registry login/publication/signing | Source pin and locks updated to 2.14.0 with no other dependency changes; 31 focused authentication tests passed. New image qualification remains REL-003 |
| REL-010 | Saved-stage integrity | Reject changed archives/reports, unsafe members, source/version/manifest mismatch, missing platforms, wrong SBOM/signer/repository, stale scans and output collisions | Archive and evidence helpers have focused local passes; integrated workflow execution remains REL-005 |

## Deferred coverage and release obligations

| ID | What remains | When it must run | Owner and evidence to close |
| --- | --- | --- | --- |
| REL-003 | Qualify and publish a safe replacement candidate | Before accepting an application release | Release maintainer: old `v0.84.2` bundle recovered and signatures passed, but fresh vulnerability policy rejected it; corrected candidate needs exact source/index/SBOM/signature binding, fresh passing scan, signed kit/checksums and public download |
| REL-004 | Protected chart release and anonymous verification | First chart release and every chart publication | Release maintainer: exact tag commit, protected approval, Sigstore verification with chart workflow identity, archive checksum and anonymous download/install |
| REL-005 | Execute saved-stage publication and retry | Before claiming failed-job release recovery is proven | Release maintainer: six-job implementation and local checks exist; real immutable checkpoint handoff and failed-job rerun must consume exact retained inputs without rebuilding; collisions fail closed |
| REL-006 | Chart repository migration and distribution index/OCI | Only if separate chart repository/distribution is selected | Release maintainer: license/history migration, signed identity change, protected workflow and consumer install/upgrade instructions; no moved old tags |
| REL-009 | Production dependency resolution | Before claiming fully locked reproducible dependency inputs | Runtime maintainer: a production-only lock consumed by Docker, build-backend pinning and cross-platform checks; do not install the verification lock or bundled psycopg driver into production. This is not the cause of the direct PyJWT finding |
| K8S-002 | Data-preserving cross-version chart/app upgrade | Before advertising a supported upgrade pair | Platform maintainer: old published version to new version with tenant data, migrations, rejected unsafe rollback and post-upgrade reads |
| K8S-003 | Upstream stack dependency composition | Each new or changed locked dependency | Platform maintainer: provenance/checksum review, license inventory, schema/lint/render and fresh-namespace runtime; external-service and bundled-service profiles |
| K8S-004 | Persistent storage, backup/restore and uninstall | Before offering bundled durable Kubernetes installation | Platform maintainer: PVC retention, database/queue/dashboard recovery into fresh storage; uninstall cannot delete externally owned services |
| K8S-005 | Worker/receiver database TLS and end-to-end OTLP | Before qualifying the complete Kubernetes AI profile | Platform maintainer: real worker and receiver SQL TLS, wrong CA/hostname denial, authenticated metadata-only intake through persistent Collector to ledger and dashboard |
| K8S-006 | Enforced NetworkPolicies, ingress and identity | Before network-isolated or externally exposed support claims | Platform/security maintainer: policy-enforcing CNI, denied peers, HTTPS, OIDC PKCE and least-scope secrets; default Kind networking is insufficient evidence |
| K8S-007 | Kubernetes, Helm and architecture matrix | Before adding each supported matrix entry | Platform maintainer: exact supported K8s/Helm combinations and native amd64/arm64 install/upgrade; one local arm64 run cannot cover both |
| K8S-008 | Resource pressure and planned/unplanned failures | Before corresponding capacity or HA claims | Platform maintainer: existing explicit availability/load gates plus appropriate fault/recovery evidence; not a routine docs check |
| COM-001 | Fresh-host Compose install and lifecycle | Before publishing the persistent supported path | Release maintainer: authenticated kit/images, empty startup, real inputs, restart persistence, upgrade, encrypted recovery and trust rotation |
| AI-001 | Live provider first value | Before supporting the advertised Bedrock operation/model/region | Product owner and integration maintainer: approved bounded invocation, no prompt capture, same invocation through OTel/ledger/pricing/attribution/Grafana, visible gaps and evidence-backed finding |
| DOC-001 | Public wiki walkthrough | Each user-facing installer release | Documentation maintainer: copy/paste on a clean host; token/login, plugins versus integrations, examples, upgrade, failure messages and recovery; links match published versions |

Deferred does not mean waived. REL-003 and REL-004 are publication gates, not
optional tests to skip for speed. A core-chart prerelease does not claim that
the optional stack, enterprise identity, HA or every integration is qualified.

## Execution log

- Security/recovery commit `2243ef05261f5aa5535dbd84b34e6670b6d83d1d`
  passed all selected CI jobs in
  [run 37225797763](https://github.com/thedevopshuman/infra-intelligence-platform/actions/runs/37225797763),
  including full verification, supply-chain, community recovery, PostgreSQL,
  identity/policy, instrumentation and plugin compatibility. This does not
  scan a newly published `0.84.3` image or qualify subsequent workflow edits.
- Saved-stage development checks: `make test-release-stages
  PYTHON=.venv/bin/python` passed 40 archive, evidence and workflow tests;
  pinned actionlint passed. These checks do not prove artifact-service handoff,
  protected signing or a real GitHub failed-job rerun. Those remain REL-005.
- Candidate `0.84.3` integrated development verification passed:
  `make verify PYTHON=.venv/bin/python`, including 1,783 Python tests
  (92 explicit environment-dependent skips), JavaScript/TypeScript, Helm and
  deployment-preflight checks. The version bump refreshed six synthetic
  qualification examples and their digest chain. A stale constant in the
  real-source installation-kit test now derives its version from the exact
  committed test snapshot. Sandbox-denied test sockets required local socket
  access; no production networking policy was relaxed. These are development
  results, not a new image scan or public installation result.

- `v0.84.2` source `cd95235d0c95` passed its eight CI jobs in
  [run 37216706844](https://github.com/thedevopshuman/infra-intelligence-platform/actions/runs/37216706844).
  This is historical source evidence, not evidence for later edits.
- Both exact `v0.84.2` image signatures passed the no-build diagnostic in
  [run 37218329567](https://github.com/thedevopshuman/infra-intelligence-platform/actions/runs/37218329567).
  It did not qualify vulnerabilities, sign downloads or publish the release.
- 2026-10-04 development change: `make verify PYTHON=.venv/bin/python` passed
  (92 explicitly skipped environment-dependent tests); subsequent
  `make verify-charts PYTHON=.venv/bin/python` passed all 100 focused tests
  in 5.502 seconds plus Helm lint/render; actionlint passed. New
  smoke-harness safety tests and chart main-tip rejection have separate focused
  passes. These are working-tree development checks, not clean-artifact release
  qualification or the full Docker compatibility matrix.
- 2026-10-04 local smoke: chart `0.87.2`, app `0.84.2`, image index
  `sha256:13d15527b3ed7c65ba34ed2b102f15bfbe29a33106ef0ec6953d9bad3361bcfc`
  on Docker Desktop arm64 / Kind Kubernetes `v1.36.1`. Install and migration,
  readiness, authorized version read, missing/wrong token denial, actual SQL
  TLS and same-version upgrade with a retained sentinel all passed. The owned
  namespace was confirmed absent afterward. This fixture used ephemeral DB
  storage and no worker, receiver, provider traffic, upstream stack or HA.
- 2026-10-05 local recovery: rebuilt only original-source packages and exported
  both existing complete `v0.84.2` OCI indexes, without an application image
  build or registry write. Full graph verification passed for both platforms;
  manifest digest is
  `sha256:c5ead4a30011ea71524cebd45eee55f17eb20068a5ddc80b0853d7ba91a4aa74`.
  Original-source publication inspection and image signature qualification
  passed through the explicit read-only adapters. Recovery tooling was a
  development worktree, not yet an approved public signing workflow.
- The corresponding fresh Trivy `0.73.0` report at `2026-10-04T18:31:00Z`
  (2026-10-05 local time), database updated `2026-10-04T14:28:15Z`, **rejected**
  the candidate: control-plane amd64 and arm64 each had one critical, five
  high, twelve medium and one low finding; bridge platforms each had five
  medium and one low. No exceptions were applied. A follow-up amd64 diagnostic
  traced all six critical/high findings to PyJWT `2.13.0`, fixed in
  [upstream 2.14.0](https://github.com/jpadilla/pyjwt/releases/tag/2.14.0).
  These are per-platform package findings, not twelve distinct vulnerabilities
  or evidence of an exploited IIP deployment. Images and historical tags remain
  unchanged. Signed downloads and public promotion did not run.
- 2026-10-05 integrated recovery/security change: `make verify
  PYTHON=.venv/bin/python` passed under PyJWT `2.14.0`; pinned actionlint passed.
  The first verification attempt caught a Helm test/executable filename
  collision during discovery. Renaming only the safety-test module and its
  focused Make target fixed it; the complete subsequent run passed. Source,
  schema, Python, TypeScript, console, Helm rendering and preflight checks are
  development evidence, not a scan of newly built application images.

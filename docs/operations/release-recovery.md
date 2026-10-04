# Recover the interrupted application release

This maintainer procedure reconstructs the missing `v0.84.2` bundle without
rebuilding or republishing application images. It is incident-specific tooling,
not a customer installer or a release approval.

**Current result:** reconstruction and original-image signature verification
passed locally. The fresh vulnerability gate rejected the control-plane image
with one critical and five high findings on each architecture. The bridge had
no critical or high findings. Do not publish this candidate as qualified or
select it as a supported chart default. Investigate and remediate the findings
in a new candidate; do not move the old tag or weaken the policy.

The follow-up package diagnostic identified all six blocking findings in
PyJWT `2.13.0`. The [upstream 2.14.0 release](https://github.com/jpadilla/pyjwt/releases/tag/2.14.0)
contains the fixes, including the
[critical key-confusion advisory](https://github.com/jpadilla/pyjwt/security/advisories/GHSA-ffc3-869f-jxw9).
IIP restricts production verification to RS256; the scanner's package finding
does not demonstrate exploitability of this application. The package policy
still requires remediation rather than an implicit reachability exception.

The original tag names source commit
`cd95235d0c95ffa011eeea4fec3edf89faac9a2a`; its annotated tag object is
`11ba80dc71387666797bd202210c0625d6c4e27a`. The application is `0.84.2`, chart
`0.87.2`, both SDKs `0.84.0` and Bedrock instrumentation `0.1.0`. Do not change
these identities to match the current development branch.

## What is retained and what is new

| Artifact | Recovery rule |
| --- | --- |
| Application and bridge images | Fetch the exact existing public OCI indexes, all platforms and attestations; never build, copy, retag or sign them again |
| Source, contracts, chart and SDK packages | Reconstruct from the exact original Git source; SDK compilation is separate from an application image build |
| Manifest and checksums | Generate for the newly packaged bytes while preserving original source and image identities |
| Qualification reports | Run original source-owned commands against the reconstructed manifest with read-only compatibility adapters where required |
| Download signatures | Any new signatures must use the actual approved recovery workflow, not the historical application workflow |

New archive checksums may differ from the lost runner bundle. Record the
recovery tool commit independently from the original artifact source commit
before distribution. Never edit a report to make its source look clean.

## Prepare original source and public image layouts

Use a clean reviewed tooling checkout, Git, Python with the repository's
verification dependencies, Node/npm, Helm and Crane `v0.20.6`. Crane's
[OCI pull mode](https://github.com/google/go-containerregistry/blob/v0.20.6/cmd/crane/doc/crane_pull.md)
exports the complete index when no platform is selected. Do not substitute
`docker save` of one native-platform image.

From the tooling repository root:

```bash
IIP_RECOVERY_ROOT=$(pwd -P)
IIP_RECOVERY_PYTHON="$IIP_RECOVERY_ROOT/.venv/bin/python"
IIP_RECOVERY_WORK=$(mktemp -d /tmp/iip-v0842-recovery.XXXXXX)
IIP_RECOVERY_BUNDLE="$IIP_RECOVERY_WORK/bundle/iip-0.84.2-cd95235d0c95"
IIP_RECOVERY_EVIDENCE="$IIP_RECOVERY_WORK/evidence"
git clone --no-hardlinks --no-checkout "$IIP_RECOVERY_ROOT" "$IIP_RECOVERY_WORK/source"
git -C "$IIP_RECOVERY_WORK/source" checkout --detach cd95235d0c95ffa011eeea4fec3edf89faac9a2a
mkdir -m 700 "$IIP_RECOVERY_WORK/docker-anonymous"

DOCKER_CONFIG="$IIP_RECOVERY_WORK/docker-anonymous" crane pull --format=oci \
  docker.io/thedevopshuman/iip@sha256:13d15527b3ed7c65ba34ed2b102f15bfbe29a33106ef0ec6953d9bad3361bcfc \
  "$IIP_RECOVERY_WORK/control-layout"
DOCKER_CONFIG="$IIP_RECOVERY_WORK/docker-anonymous" crane pull --format=oci \
  docker.io/thedevopshuman/iip-bridge@sha256:57fad1274d105348fd9ee2ec9ff8177b62ddc5d56688943d31c56b940e2b5565 \
  "$IIP_RECOVERY_WORK/bridge-layout"
```

Keep the workspace path for subsequent stages. It contains public artifacts,
not customer state. No Docker Hub login or publication token is needed.

## Assemble and verify the local checkpoint

```bash
"$IIP_RECOVERY_PYTHON" scripts/assemble_recovered_release.py assemble \
  --source "$IIP_RECOVERY_WORK/source" \
  --control-layout "$IIP_RECOVERY_WORK/control-layout" \
  --bridge-layout "$IIP_RECOVERY_WORK/bridge-layout" \
  --output "$IIP_RECOVERY_BUNDLE" --python "$IIP_RECOVERY_PYTHON"

"$IIP_RECOVERY_PYTHON" scripts/assemble_recovered_release.py verify \
  --bundle "$IIP_RECOVERY_BUNDLE"
```

The assembler checks the exact annotated tag and clean source before and after
packaging. It uses an isolated locked-dependency SDK build, validates both
complete OCI graphs and closes the eleven-file final bundle inventory. Reports
and scratch files must remain outside it. Run the recovery regression tests
with `make test-release-recovery PYTHON=.venv/bin/python`.

If assembly fails, keep the incomplete output for diagnosis and use a new
output directory after fixing the cause. If assembly succeeds, later stages
reuse that verified directory; do not repeat assembly to retry a signature or
scanner failure. Re-run `verify` before each resumed stage.

## Check publication and original image signatures

Run original qualification scripts from the original checkout. The current
tooling checkout supplies only the explicitly selected adapters:

```bash
cd "$IIP_RECOVERY_WORK/source"
PYTHONPATH=src:sdks/python/src "$IIP_RECOVERY_PYTHON" scripts/release_publication.py publish \
  "$IIP_RECOVERY_BUNDLE" \
  --control-plane-repository docker.io/thedevopshuman/iip \
  --bridge-repository docker.io/thedevopshuman/iip-bridge \
  --tag v0.84.2 --report "$IIP_RECOVERY_EVIDENCE/release-publication-report.json" \
  --docker "$IIP_RECOVERY_ROOT/scripts/recovery_docker_readonly.py"

PYTHONPATH=src:sdks/python/src "$IIP_RECOVERY_PYTHON" scripts/release_publication.py github-signature-policy \
  "$IIP_RECOVERY_EVIDENCE/release-publication-report.json" \
  --github-repository thedevopshuman/infra-intelligence-platform --generation 1 \
  --output "$IIP_RECOVERY_EVIDENCE/release-signature-policy.json"

PYTHONPATH=src:sdks/python/src "$IIP_RECOVERY_PYTHON" scripts/release_signature_verification.py run \
  --bundle "$IIP_RECOVERY_BUNDLE" \
  --policy "$IIP_RECOVERY_EVIDENCE/release-signature-policy.json" \
  --output "$IIP_RECOVERY_EVIDENCE/release-signature-verification-report.json" \
  --cosign "$IIP_RECOVERY_ROOT/scripts/cosign_recovery_adapter.py" \
  --require-clean --require-promotable
```

Despite the historical command name `publish`, this invocation cannot publish:
the selected adapter permits only version and exact existing-index inspection.
Do not omit the adapter. A missing or mismatched tag fails closed.

Docker Desktop may register Buildx only in its user configuration. To avoid
loading that credential-bearing configuration, set `IIP_RECOVERY_BUILDX` to
the explicit installed executable, such as
`/Applications/Docker.app/Contents/Resources/cli-plugins/docker-buildx`.
For Cosign on a non-default local daemon, set `IIP_RECOVERY_DOCKER_HOST` to its
explicit existing `unix:///absolute/socket` URI. Remote daemon endpoints and
ambient Docker authentication are not inherited by that verification adapter.

Both images must verify against the original certificate identity:

```text
https://github.com/thedevopshuman/infra-intelligence-platform/.github/workflows/release.yml@refs/tags/v0.84.2
```

The issuer is `https://token.actions.githubusercontent.com`. The adapter
accepts only these two exact indexes and only ADR 0165's Docker Hub hostname
alias correction after Cosign succeeds. It grants no signing authority.

## Run the fresh vulnerability gate

Still in the original source checkout, select the intended local Docker daemon
explicitly where necessary. Use an empty Docker configuration for public pulls.

```bash
DOCKER_CONFIG="$IIP_RECOVERY_WORK/docker-anonymous" PYTHONPATH=src:sdks/python/src \
  "$IIP_RECOVERY_PYTHON" scripts/release_vulnerability_qualification.py run \
  --bundle "$IIP_RECOVERY_BUNDLE" \
  --policy contracts/examples/release-vulnerability-policy.json \
  --output "$IIP_RECOVERY_EVIDENCE/release-vulnerability-qualification-report.json" \
  --docker docker --require-clean --require-qualified
```

The pinned scanner downloads a fresh database and scans all four image/platform
SBOMs offline. The existing severity thresholds and freshness limits still
apply. A scanner outage is not a passing result; a policy rejection is not
permission to add an exception. Preserve each failed report and use a new
output filename for a retry.

## Protected publication remains a separate stage

Before any qualified distribution, retain immutable bundle and report
checkpoints, bind them to the reviewed tooling commit, and run protected
signing of the new archive, kit and checksums. Verify every new signature
against the actual recovery workflow identity. Keep the original image signer
unchanged. Do not reuse the old application identity for new archive signatures.

The intended CI split is recovery, qualification, signing, publication and
anonymous consumer verification. Each successful stage must retain exact
checksum-bound inputs for failed-job reruns. Read-only stages need no write
token; only protected signing receives OIDC and only publication receives
GitHub release write authority. No recovery stage receives Docker Hub write
credentials. Existing releases or conflicting assets require reconciliation,
never an automatic overwrite.

That workflow and its approved signer/protection configuration are not created
by these local commands. REL-003 and REL-005 in the
[coverage ledger](../roadmap/release-test-coverage.md) remain open. The current
vulnerability rejection means fixing a new candidate takes priority over
automating publication of this old candidate.

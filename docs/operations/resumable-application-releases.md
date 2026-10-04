# Resumable application releases

This runbook describes the six-job `release.yml` workflow introduced for the
`0.84.3` candidate. It preserves passing build outputs so a late failure does
not require another multi-platform image build. It does not make the old
single-job `v0.84.2` attempt resumable; use its
[recovery notes](release-recovery.md) for that rejected candidate.

## Jobs and saved outputs

| Job | Work and authority | Saved output |
| --- | --- | --- |
| build | Read-only exact-tag admission, source checks and one multi-platform build | One complete bundle archive; immutable artifact ID, file hash and manifest hash |
| vulnerabilities | Read-only, pinned scanner and fresh database, exact platform SBOMs | Minimized qualification report and hash |
| publish-images | Protected review, Docker Hub credentials and GitHub signing identity | Publication report and exact tag-specific signature policy, with hashes |
| signatures | Read-only anonymous verification of exact published index signatures | Signature qualification report and hash |
| sign-downloads | Protected review and GitHub signing identity; no registry credentials | Archive and kit Sigstore bundles plus archive checksum |
| publish | Protected review and repository write; no signing token or registry credentials | GitHub release with explicitly allowed customer assets |

Every job checks the original build archive and manifest hashes before using
the bundle. Small report artifacts also carry independent expected hashes
from their producing job outputs. The joined evidence gate rejects a crossed
revision, changed manifest, incomplete architecture set, wrong SBOM, unexpected
repository or signer, failed qualification, and a stale scan. No checkpoint
is accepted merely because its filename looks correct.

The bundle archive is uploaded once with compression disabled because it is
already compressed. Checkpoints expire after seven days. Names include the
run attempt, replacement is disabled, and consumers select exact artifact IDs
instead of downloading every matching name. Build admission binds the clean
tag to the fetched main tip; a later main commit does not silently change the
accepted candidate or require a rebuild during a downstream retry.

## Retry a failed job

For local changes to these boundaries, run `make test-release-stages
PYTHON=.venv/bin/python`. It exercises archive integrity, report binding and
workflow constraints without Docker or a registry. Run `make verify` for the
completed integrated change. These local checks do not replace the actual
protected publication or failed-job resume exercise.

1. Open the run for the exact application tag. Read the failing job's stable
   error code and check that the build checkpoint and job outputs still exist.
2. For a transient downstream failure, use GitHub's **Re-run failed jobs**.
   The successful build remains a prerequisite; it is not rebuilt. Do not
   choose **Re-run all jobs** just to retry publication.
3. If the evidence gate reports `release-stage.evidence.scan-stale`, rerun
   the **vulnerabilities** job and its dependent jobs with a fresh database.
   Retain the successful build. Do not increase the age limit for convenience.
4. Complete any required protected-environment review. An automated retry
   does not authorize self-approval or disabling protection.
5. Inspect the final release and anonymously verify its signed kit and image
   identities before describing the candidate as installable.

A source, dependency or workflow fix requires a new commit and immutable
application tag; a rerun still executes the old tagged workflow. Never move
an existing tag to pick up a fix. If a checkpoint expired, stop and follow an
explicit recovery decision. Do not substitute local files or claim regenerated
archives are the original runner outputs.

Existing image tags are accepted only when they already resolve to the exact
expected index; a conflicting digest fails. Existing GitHub releases fail
closed. If creation partly succeeded before a network failure, inspect the
release and assets first; this workflow does not delete, overwrite or repair
them automatically. Image publication and GitHub release creation are not one
atomic transaction.

## Scope of a green release run

This workflow verifies source quality and the portable artifact supply chain.
It is not proof of every customer deployment, live provider, HA or supported
upgrade pair. Run the installation and lifecycle checks at their owning
boundaries in the [coverage ledger](../roadmap/release-test-coverage.md).
Core chart changes can use the separate chart workflow after its qualified
default image is committed; no application image rebuild is needed for a
chart-only release.

GitHub documents [job reruns](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs)
and [artifact behavior](https://github.com/actions/upload-artifact).
See [ADR 0168](../decisions/0168-resumable-application-release-stages.md) for
the trust and permission boundaries.

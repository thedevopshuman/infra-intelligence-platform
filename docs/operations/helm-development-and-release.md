# Helm development and independent releases

Use this developer runbook for the core bring-your-own-services chart. It is
not an all-in-one installation guide or a production qualification. The
[delivery plan](../roadmap/installation-release-delivery-plan.md) covers the
optional upstream stack and public launch; the
[coverage ledger](../roadmap/release-test-coverage.md) records deferred tests.

## Fast local validation

From the repository root, with the verification dependencies and Helm installed:

```bash
make verify-charts PYTHON=.venv/bin/python
```

This validates repository links/boundaries, the strict chart configuration,
deployment preflight, package licensing, release preparation, CI selection and
smoke-harness safety. It lints/renders Helm without a Docker build, registry
write or Kubernetes mutation. Run changed-boundary tests while editing and
`make verify PYTHON=.venv/bin/python` once the integrated change is ready.

CI automatically takes this lightweight path only for the explicit docs/chart
allowlist. Runtime, unknown, mixed, workflow and selection changes use the
existing full jobs; missing Git history also selects full. Maintainers can
explicitly run:

```bash
gh workflow run ci.yml --ref main -f scope=charts
gh workflow run ci.yml --ref main -f scope=full
```

Manual `charts` is deliberately limited evidence. Manual `auto` without a Git
change range falls back to full. There is no periodic full-test schedule.
Do not use skipped jobs as proof that their boundaries passed.

## Test the chart on local Docker-backed Kubernetes

The existing `kind-iip-dev` context is sufficient. This does not require the
separate Docker Desktop Kubernetes setting. Check the selected local cluster:

```bash
kind get clusters
kubectl --context kind-iip-dev get nodes
```

Use an exact reviewed published application digest, not a tag. The following
is the already-signed `v0.84.2` development candidate. Its signature-only
diagnostic passed; vulnerability/download publication remains incomplete, so
this command is local testing, not a supported customer install:

```bash
PYTHONPATH=src:sdks/python/src .venv/bin/python scripts/test_helm_chart_smoke.py \
  --context kind-iip-dev \
  --image docker.io/thedevopshuman/iip@sha256:13d15527b3ed7c65ba34ed2b102f15bfbe29a33106ef0ec6953d9bad3361bcfc \
  --app-version 0.84.2
```

The command validates local cluster identity, creates a new labeled namespace,
generates temporary protected credentials and TLS material, starts a disposable
PostgreSQL fixture and installs the core chart using the selected image. It
checks readiness, authentication, version/image/chart identity and actual SQL
TLS. It never builds IIP, changes an existing release or deletes a cluster.
It then repeats a same-version Helm upgrade and checks a database sentinel
and unchanged database pod identity. This is not a cross-version migration test.
Cleanup is confined to the namespace identity created by that invocation.
Registry downloads may occur when the cluster lacks an image.

Do not supply customer credentials or telemetry. The fixture is not a supported
database installation. A passing smoke run does not prove cross-version
upgrade, backup/restore, enforced NetworkPolicies, external identity, optional
receiver/worker integration or the full Grafana/Collector stack.

## Prepare a chart-only release

1. Select an application version/digest with the required signature,
   vulnerability and runtime evidence. Commit `image.repository` and
   `image.digest` in the core chart defaults and set its `appVersion` to the
   selected application. The preparer rejects the current placeholder image;
   no image override can bypass that packaging prerequisite.
2. Increment `Chart.yaml` `version` for the chart change. Application source
   version need not change. Test supported installation/upgrade combinations
   and record the evidence. Review strict values/schema changes together.
3. Finish validation, commit and push clean source. Create an immutable
   `helm-v<Chart.yaml version>` tag on that exact commit. Never move an old tag
   or reuse a version after a failed publication. The tag must equal freshly
   fetched `origin/main`; a clean off-main tag is rejected.
4. The `helm-release.yml` workflow packages the committed chart and root license
   notices, lints/renders it, signs the archive and checksum asset, verifies
   those signatures, and creates a chart-specific GitHub release. The protected
   `release` environment still needs its configured reviewer approval.
5. Download anonymously and verify the published checksum and Sigstore bundles
   against the exact workflow identity and GitHub issuer before installation.
   Close REL-004 only after that consumer walkthrough passes.

The chart workflow does not use Docker Hub credentials, push images or rebuild
the application. A chart release does not become GitHub's latest application
release. Existing release collisions fail closed; an incomplete prior release
needs explicit reconciliation rather than an automatic overwrite.

On 2026-10-04, the existing immutable tag rule was extended to `helm-v*` and
the same tag pattern was admitted to the protected `release` environment.
The required reviewer and no-bypass protections were retained. Recheck these
live policies before tagging; no chart tag or release was created by that setup.

For this repository, the chart signature identity has this exact shape:

```text
https://github.com/thedevopshuman/infra-intelligence-platform/.github/workflows/helm-release.yml@refs/tags/helm-v<chart version>
```

The issuer is `https://token.actions.githubusercontent.com`. Do not use the
application `release.yml` identity for chart-only signatures. The Cosign bundle
is not Helm's GPG `.prov` format; `helm install --verify` is not a substitute
for Sigstore verification. The initial workflow distributes signed GitHub
archives, not an already-existing Helm repository index or OCI chart registry.

## Recover a failed application release

First identify the failed job and its retained inputs. Rerun failed jobs when
those exact artifacts exist; do not trigger all Docker suites just to retry
signing. The earlier `v0.84.2` job retained no bundle checkpoint, so its missing
bundle must be reconstructed from original committed sources and the complete
published OCI indexes, then verified before remaining gates can continue.
Neither a mutable tag pull nor a native-platform-only Docker export recreates
the multi-platform release bundle.

Do not alter old tags, call a signature diagnostic a full qualification, skip
vulnerability policy or sign a different archive under old evidence. Durable
stage checkpoints and resumable publication remain REL-005 in the ledger.

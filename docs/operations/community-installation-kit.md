# Persistent Compose installation kit

**Status:** Implemented candidate packaging; public distribution and fresh-host qualification pending

The persistent Compose installation is the primary public-release path. It
starts empty and is intended to keep useful data, unlike the optional
disposable learning session. This kit packages the existing installation tools
for use from an extracted directory without Git or Make at runtime. Automated
non-Git evidence covers initialization and configuration validation, not a
fresh supported-host deployment. It does not yet make the source preview a
supported public v1.

## What the archive contains

The `community-installation-source` artifact in a current release manifest is
named `infra-intelligence-community-<version>.tar.gz`. Its single versioned
directory includes IIP runtime source, Compose services, Collector/Grafana assets,
installer, encrypted recovery and transport-rotation tools, pinned Python
requirements, public configuration contracts, operating documentation and
license/notice files. It contains no operator credentials, initialized state,
Git metadata or virtual environment. Application and third-party container
images are separate artifacts; they are not contained in this source kit.

The archive is checksum-bound to the release manifest. Verification rejects
unsafe paths, duplicate members, links and special files, oversized archives,
missing required deployment/runtime files and a mismatched packaged application
version. These are content/integrity checks, not publisher authentication.
Older manifests without this additive role remain valid historical bundles;
verify that the selected bundle actually contains this role before proceeding.

## Maintainer packaging

From a **clean committed** checkout, the full `make release-bundle` command
includes the kit. To build only this portable source artifact without Docker,
npm, Helm, a registry or provider calls:

```bash
.venv/bin/python scripts/installation_kit.py build \
  --output /absolute/new-output/infra-intelligence-community-0.84.0.tar.gz \
  --version 0.84.0
.venv/bin/python scripts/installation_kit.py inspect \
  /absolute/new-output/infra-intelligence-community-0.84.0.tar.gz \
  --version 0.84.0
```

Use the candidate's actual application version; do not overwrite an old release
asset just because its version is unchanged during development. Keep the
output outside the tracked source, create its parent directory first, and use
a new output path. The builder refuses dirty source, version mismatch and
existing output. This is a local packaging command, not a public download URL.

## Operator installation from an authenticated release

Public kit and runtime-image publication is still pending. Do not substitute
the `learning-v0.84.0` archive for this profile or infer a registry image name.
When the candidate is published, use its authenticated artifact/signature
instructions and verify exact checksums before extracting into a fresh
directory. A checksum downloaded from the same untrusted source is not proof
of the publisher. Follow the [release procedure](release-artifacts.md).

Prerequisites remain Python 3.11 or newer and one trusted local Docker Engine
or Docker Desktop daemon with Compose v2 and a Unix socket. Windows-native and
remote Docker contexts are not supported by this profile. Network access may
be needed for Python packages and container images; this is not an offline
dependency bundle. From the extracted directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --requirement requirements/verify.txt
.venv/bin/python scripts/community_stack.py --help
```

Prepare reviewed owner-only channel, pricing, current catalog-qualification
and attribution files using the [installation runbook](community-installation.md#prepare-the-reviewed-inputs).
The installer must not invent prices or organizational ownership. Select the
exact independently verified application image from the release; load or pull
it through the release's published procedure first. Then use actual protected
file paths and that image reference:

```bash
.venv/bin/python scripts/community_stack.py init \
  --image "$IIP_VERIFIED_IMAGE" \
  --channel /protected/bedrock-channels.json \
  --catalogs /protected/ai-catalogs.json \
  --qualifications /protected/ai-catalog-qualifications.json \
  --attribution /protected/ai-attribution.json
.venv/bin/python scripts/community_stack.py check
.venv/bin/python scripts/community_stack.py up
```

Set `IIP_VERIFIED_IMAGE` to the verified digest reference, not a guessed tag.
Ordinary `up` does not build a missing image. Source developers may explicitly
use `up --build`, but that is not a released-image qualification. Dependencies
may still be pulled by Compose; recovery startup retains its stricter
exact-local-images/no-pull behavior.

The kit does not authenticate third-party service images. Their exact digests
and supported architectures still need release qualification; the current
Prometheus reference is tag-only. Ordinary startup may resolve and pull service
images, so this candidate is not an offline or fully authenticated runtime
distribution.

## First login, operation and stopping

Open `http://127.0.0.1:18083/console`. In the owner-only local file
`.iip/community/credentials.json`, use **`apiToken`** for the console; do not
use `collectorToken` or `receiverToken`. Grafana at
`http://127.0.0.1:13001/d/iip-community-ai-finops` uses `admin` and the separate
`grafanaPassword`. Read credentials locally; never paste them into issues,
screenshots or chat. The launcher does not print them.

```bash
.venv/bin/python scripts/community_stack.py status
.venv/bin/python scripts/community_stack.py down
```

`down` preserves the database, Collector queue, Prometheus, Grafana and protected
state. Never use `docker compose down --volumes` for a retained installation.
Keep the extracted directory and protected state in place: moving it changes
installation/project binding. A new archive is **not** an upgrade procedure;
do not copy state into it or reinitialize against existing data. Follow the
[recovery](community-recovery.md) and [trust lifecycle](community-trust-rotation.md)
runbooks, and retain exact images and deployment files with recovery custody.

Connect the instrumented application using the runbook's authenticated OTLP
endpoint and CA. Only that application calls Bedrock. An empty healthy stack
does not prove real telemetry arrived, costs are correct or an investigation
has useful evidence. Current [installation options](installation-options.md)
also distinguish configuration-driven adapters from the developer plugin
example; there is no plugin marketplace or UI enable switch.

## Verification boundary

`make verify` tests the archive inspector and committed-source packaging,
including an extracted non-Git tree that initializes and checks protected
configuration with synthetic test inputs and no Docker or provider calls.
The separate `make test-community`, recovery and trust gates exercise actual
local containers. Neither category replaces a fresh supported-host install of
public artifacts, real provider-to-dashboard evidence, a supported upgrade,
credential lifecycle, retention or release-signature verification. Those remain
tracked in the [public v1 plan](../roadmap/public-v1-release-plan.md).

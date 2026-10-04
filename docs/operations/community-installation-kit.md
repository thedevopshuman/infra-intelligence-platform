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
  --output /absolute/new-output/infra-intelligence-community-0.84.2.tar.gz \
  --version 0.84.2
.venv/bin/python scripts/installation_kit.py inspect \
  /absolute/new-output/infra-intelligence-community-0.84.2.tar.gz \
  --version 0.84.2
```

Use the candidate's actual application version; do not overwrite an old release
asset just because its version is unchanged during development. Keep the
output outside the tracked source, create its parent directory first, and use
a new output path. The builder refuses dirty source, version mismatch and
existing output. This is a local packaging command, not a public download URL.

## Operator installation from an authenticated release

Authenticated public kit and runtime-image distribution is still pending. The
approved application destination is `docker.io/thedevopshuman/iip`.
`v0.84.1` images were uploaded but their signing failed; their existence is not
release approval. `v0.84.2` signed its images but has no completed customer-kit
publication; a signature-only recheck cannot create it. Use only a completed
release with verified signatures. Do
not substitute the `learning-v0.84.0` archive for this profile or guess an
image tag/digest.

The release workflow publishes this kit directly with a separate
`community-kit.sigstore.json`, so the full OCI-image archive is unnecessary
when downloading only the Compose installer. Once a release is published,
authenticate its kit before extraction using the exact accepted version:

```bash
cosign verify-blob \
  --bundle community-kit.sigstore.json \
  --certificate-identity \
    https://github.com/thedevopshuman/infra-intelligence-platform/.github/workflows/release.yml@refs/tags/v0.84.2 \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  infra-intelligence-community-0.84.2.tar.gz
```

Use the release's actual version in both the filename and signer identity;
this example does not assert that `v0.84.2` is published. A checksum alone is
not publisher authentication. Follow the [release procedure](release-artifacts.md)
for the matching image signature policy and immutable application reference.

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
exact independently verified application image from the release. In
digest-selected mode, `--image` must be one fully qualified
`registry/path@sha256:<64 lowercase hex>` reference. A tag, a tag-plus-digest,
an unqualified name, or malformed digest is rejected. Then use actual protected
file paths and that image reference:

```bash
.venv/bin/python scripts/community_stack.py init \
  --image "$IIP_VERIFIED_IMAGE" \
  --channel /protected/bedrock-channels.json \
  --catalogs /protected/ai-catalogs.json \
  --qualifications /protected/ai-catalog-qualifications.json \
  --attribution /protected/ai-attribution.json
.venv/bin/python scripts/community_stack.py check
.venv/bin/python scripts/community_stack.py images --pull
.venv/bin/python scripts/community_stack.py images
.venv/bin/python scripts/community_stack.py up
```

Set `IIP_VERIFIED_IMAGE` to the independently authenticated release digest,
not a guessed reference. `images --pull` is the only digest-mode operation that
fetches images. It explicitly fetches the application, PostgreSQL, OpenTelemetry
Collector, Prometheus, and Grafana references through the operator's Docker
registry configuration. It receives no IIP installation credentials. Run
`images` without `--pull` to prove those exact five references are already
present for Linux on the bound daemon's native architecture.

Digest-mode `up` never builds or pulls. When the stack is stopped, it
re-inspects all five references under the installation lock, binds every
Compose service to the corresponding local content-addressed image ID, and
starts with Compose pull disabled. A missing or incompatible image blocks
startup. A healthy repeated `up` does not resolve images or mutate Compose
state, although successful-start records may be refreshed. `up --build` is rejected in digest
mode. The legacy tag/source path and explicit `up --build` remain available to
source developers, but they are not released-image qualification; `images` is
available only to a digest-selected installation.

The selected helper defines all four dependencies as fully qualified digest
references:

- `docker.io/library/postgres@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15`;
- `docker.io/otel/opentelemetry-collector-contrib@sha256:c5918f78992ee73b0d6f0e599423ac5ec52dd5d9726733114d6eca53d5a32ed5`;
- `docker.io/prom/prometheus@sha256:3c42b892cf723fa54d2f262c37a0e1f80aa8c8ddb1da7b9b0df9455a35a7f893`;
- `docker.io/grafana/grafana@sha256:e932bd6ed0e026595b08483cd0141e5103e1ab7ff8604839ff899b8dc54cabcb`.

Local presence and architecture checks do **not** authenticate an image,
connect it to source, verify a signature or attestation, scan it, or qualify a
release. Those checks still belong to the release's authenticated publication
procedure. The kit remains neither an offline dependency bundle nor a
fresh-host-qualified public distribution.

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
The `images` operation is rejected for recovered installations: their startup
continues to require the exact recorded image IDs with no network, pull, or
build fallback. Because recovery binds the operational helper bytes, preserve
the original kit with its backups; a newer kit is not a cross-version restore
or upgrade procedure.

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
local containers. `make test-community-images PYTHON=.venv/bin/python` separately
uses a uniquely labelled application image and an owned loopback registry to
exercise explicit digest pulls, cached-image startup after removing that registry,
healthy repeated startup, stopped restart, exact container image identities and
missing-image refusal. It requires free community ports and the test's pinned
registry/dependency images in the local cache, may contact Docker Hub for the
four public dependencies during explicit pulls, and cleans only its own resources.
It is a synthetic local regression, not a public-registry authentication test
or a fresh-host installation. Neither category replaces a fresh supported-host install of
public artifacts, real provider-to-dashboard evidence, a supported upgrade,
credential lifecycle, retention or release-signature verification. Those remain
tracked in the [public v1 plan](../roadmap/public-v1-release-plan.md).

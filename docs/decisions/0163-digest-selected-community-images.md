# ADR 0163: Digest-selected community images

**Status:** Accepted implementation boundary

**Date:** 2026-10-04

## Context

ADR 0162 packages the persistent Compose installer without embedding container
images. The installer previously accepted a mutable application tag, while
ordinary Compose startup could resolve or pull dependency images implicitly.
That behavior is useful for source development but cannot bind an extracted
release kit to the exact five images reviewed for an installation.

The encrypted recovery path already records the image IDs that actually ran
and refuses pull or build fallback. A fresh installation needs a similarly
fail-closed image-selection boundary without treating local Docker presence as
publisher authentication, signature verification, release qualification, or
support evidence.

## Decision

Add a digest-selected mode to the existing host-side community launcher.

- `init --image` selects digest mode only for a fully qualified reference of
  the form `registry/path@sha256:<64 lowercase hex>`. Reject malformed
  references and tag-plus-digest forms. Retain the existing tag/source-build
  path for development; it is not equivalent to digest mode.
- Pin PostgreSQL, the OpenTelemetry Collector, Prometheus, and Grafana to fully
  qualified digest references. Together with the selected application image,
  these are the five images checked for a fresh digest-mode installation.
- Add an explicit `images` operation. It reads only the protected installation
  record and selected local-daemon binding. It checks that all five exact
  references are present for Linux on the daemon's native architecture.
  `images --pull` is the only digest-mode operation that fetches them. It may
  use the operator's Docker registry configuration, but it receives no IIP
  installation credentials and does not read configuration generations or wait
  for services.
- Serialize image checks with the installation lock. A healthy repeated `up`
  does not resolve images or invoke Compose; successful-start records may still
  be refreshed. Starting a stopped digest-mode installation re-inspects the exact
  five references, substitutes the daemon's actual content-addressed image IDs
  into every Compose service, and starts with `--no-build --pull never`.
  A missing, non-Linux, or wrong-architecture image blocks startup.
- Prohibit `up --build` in digest mode. Keep explicit builds only for the
  legacy source/tag path.
- Ignore ambient default-platform overrides and disable Compose's implicit
  `.env` loading. Image selection and lifecycle options come from the reviewed
  installation, packaged defaults and explicit launcher arguments, not an
  unrelated checkout file.
- Reject `images` for recovered installations. Recovery remains bound to its
  recorded image-ID snapshot and continues offline with no pull or build
  fallback.

The protected `installation.json` remains an internal installation record, not
a new public API, resource, event, SDK, or state contract. The selected digest
and local image IDs prove only what the local daemon was asked to use. They do
not prove image publisher, source revision, signature, attestations,
vulnerability status, registry immutability, release authorization, or content
fitness.

## Consequences and limitations

A digest-selected installation cannot drift through a mutable tag or an
implicit Compose pull after validation. Fetching remains deliberate and
separate from startup, and operators can pre-load all five images for an
offline start.

The Prometheus dependency is pinned to
`sha256:3c42b892cf723fa54d2f262c37a0e1f80aa8c8ddb1da7b9b0df9455a35a7f893`;
the other three dependency digests retain their reviewed Compose selections.
Changing any selected dependency requires the same implementation, privacy,
architecture, and runtime regression review as before.

This decision does not publish an application image, select a registry or
Docker Hub repository, qualify signatures, provide registry credentials, or
make the current source kit public v1. It also does not prove a fresh supported
host, live provider traffic, first value, upgrade compatibility, or recovery
across kit versions.

The recovery deployment fingerprint includes the operational helper bytes.
Backups therefore remain coupled to the exact kit that created them: preserve
an old kit with its old backups and use its recovery procedure. Digest mode is
not an in-place or cross-version upgrade path.

See the [installation-kit runbook](../operations/community-installation-kit.md)
and [community installation runbook](../operations/community-installation.md).

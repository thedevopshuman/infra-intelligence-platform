# ADR 0162: Versioned persistent community installation kit

**Status:** Accepted implementation boundary

**Date:** 2026-10-04

## Context

Public open-source v1 needs a usable persistent installation, not only the
already published disposable learning session. The Compose runtime exists,
but the release bundle omits its launcher, configuration assets and lifecycle
tools. A user should not need a live Git checkout merely to run those tools.

The owner clarified that easy installation is the intent of "learning":
Compose is the primary path, with a separate full-stack Helm profile next.
The existing bring-your-own-dependencies production chart retains its current
security and operational prerequisites.

## Decision

- Add `community-installation-source` to the existing `v1alpha1`
  `ReleaseManifest`. Current builders require and checksum the kit; historical
  manifests without this additive role remain verifiable.
- Build the versioned archive only from one clean committed source tree. Ship
  the runtime, Compose/deployment assets, installer and lifecycle scripts,
  pinned host requirements, public configuration contracts, instructions, and
  license/notice material. Never package ignored installation state, a virtual
  environment, credentials, Git metadata or an initialized database.
- Inspect bounded archive structure and required files before finalization
  and when verifying transport. Reject unsafe paths, links, special files,
  duplicates, incomplete kits, and mismatched packaged application versions.
- Prove the extracted non-Git tree can initialize and validate protected state
  using synthetic inputs without Docker/provider traffic. Keep live runtime
  qualification distinct from this executable packaging check.
- Prevent ordinary installer startup from implicitly building an image. Only
  the explicit source-build option builds; a selected release image is not
  silently replaced by code compiled on the operator's host.

## Consequences

This closes a distribution-content gap, not the entire release. Public image
and kit publication, publisher authentication, fresh supported-host walkthroughs,
real first value, upgrades, credential lifecycle, storage bounds and release
promotion evidence remain required. The kit still needs host Python and
Docker/Compose dependencies and operator-reviewed pricing/attribution; it does
not fabricate those inputs or provide a new customer SDK.

No serving contract, tenant boundary, plugin authority or production Helm
security default changes. Full-stack Kubernetes installation remains a separate
implementation with its own values contract and lifecycle tests.

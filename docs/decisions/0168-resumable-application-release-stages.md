# ADR 0168 Resumable application release stages

**Status:** Accepted implementation boundary

**Date:** 2026-10-05

## Context

The single-job application release lost its intermediate bundle when a late
signature check failed. Repeating that job rebuilt images and could not
resume just the failed check. GitHub reruns jobs, not individual steps.

## Decision

Keep the canonical repository, immutable application tags, protected `release`
environment and original `release.yml` signing identity. Split the workflow
into six jobs: build, vulnerabilities, publish images, signatures, sign
downloads and publish. Only the build job admits the exact clean tag at the
fetched main tip and builds application images. Later jobs use that admitted
source revision, not a new main tip.

Upload one deterministic, closed-inventory bundle archive after building.
Use immutable artifact IDs, separate attempt names, seven-day retention and
no overwrite. Trusted job outputs carry the archive SHA-256 and manifest
SHA-256. The receiving job verifies both before accepting archive contents;
bounded extraction rejects links, duplicate members, unsafe paths, extras
and output collisions. Existing release-bundle validation remains mandatory.
The internal checkpoint does not change the public manifest contract.

Upload small reports and signing outputs separately. Every report input is
hashed against its producing job output before parsing, validated against its
existing contract, and joined to the same source, manifest, image indexes and
platform SBOMs. At publication boundaries the vulnerability database must
still satisfy the committed age policy. Delayed approvals are not exemptions.
The exact tag-specific workflow signer, GitHub issuer and transparency checks
remain mandatory. Sign the existing archive and community kit without
rebuilding or repacking them.

Read-only jobs cannot access environment secrets. Registry credentials and
image-signing OIDC belong only to protected image publication. Download
signing gets OIDC but no registry secrets or repository write permission.
Final publication gets repository write permission but no signing authority
or registry credentials. Protected reviews are not bypassed by retries.

## Consequences

A late failure can consume the original successful build when its exact
checkpoint remains available. No job accepts an arbitrary artifact from a
different run or a manually supplied digest. A missing or expired checkpoint
fails closed. Existing GitHub releases also fail closed; partial publication
needs explicit inspection rather than automatic overwrite.

This saves repeated build time but does not remove tests or release gates.
The first real six-job release and a deliberate failed-job resume remain
execution obligations in REL-003 and REL-005 of the
[coverage ledger](../roadmap/release-test-coverage.md). Historical `v0.84.2`
has no saved checkpoint and still follows ADR 0167, not this retry path.

See the [runbook](../operations/resumable-application-releases.md) for retention,
approval delays and recovery boundaries.

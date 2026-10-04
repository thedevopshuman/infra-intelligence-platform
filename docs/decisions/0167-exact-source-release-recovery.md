# ADR 0167: exact source release recovery

**Status:** Accepted implementation boundary

**Date:** 2026-10-05

## Context

The protected `v0.84.2` release published and signed both multi-platform image
indexes, then failed signature qualification because of the Docker Hub claim
alias corrected in ADR 0165. The failed run retained no bundle checkpoint.
Rebuilding the application would produce different images; relabeling new
archives as the lost originals would misrepresent their provenance.

## Decision

Provide narrowly scoped recovery tooling for this exact tag, annotated tag
object, source commit, versions and two published image index digests. Keep
the historical manifest and qualification contracts unchanged. Recovery does
not move tags, overwrite images, change vulnerability policy or authorize
publication.

Recover complete OCI layouts by digest. Validate every embedded blob hash and
descriptor size, graph closure, both platforms and their attestations before
packing. Reject links, external descriptors, extra files, malformed tar members
and incomplete graphs. Valid inline descriptor data must match its local blob
by size and hash; it cannot replace a missing blob. Revalidate the embedded
graph when consuming an archive checkpoint, even if its outer checksum passes.

Build non-image assets from a separate, clean checkout of the exact original
source. Build the TypeScript SDK in a fresh committed-source temporary tree,
so ignored local build outputs and credentials cannot enter the package.
Keep scratch files, reports and tool credentials outside the closed bundle
inventory. Existing output directories are never replaced.

Run the original qualification commands from that unchanged source checkout.
Two explicit adapters use their existing command ports:

- The Docker adapter permits only actual Buildx version and exact existing
  tag/digest inspection. A failed lookup cannot fall through to publication.
- The Cosign adapter verifies only the two original image identities using
  pinned Cosign, then normalizes only the exact Docker Hub hostname alias
  already accepted in ADR 0165. It cannot sign or receive signing tokens.

Original reports continue to identify the original artifact source. Separately
record the clean recovery tooling revision and actual new signing workflow
identity before distribution. Fresh archive bytes get fresh checksums and
signatures; they are not the lost runner archive and were not signed by the
original workflow. Local development checks do not establish approval of the
recovery tooling or permission to publish.

## Consequences

Signature or vulnerability checks can be retried against the retained bundle
without another application build or registry write. A failed gate still
blocks release acceptance. The local recovery commands are not yet a protected,
automatically resumable publishing workflow; that remains REL-005 in the
[coverage ledger](../roadmap/release-test-coverage.md).

The [recovery runbook](../operations/release-recovery.md) defines each stage,
retry boundary and remaining publication requirement. The normal application
and independent chart release workflows keep their existing protections.

The recovered candidate's fresh scan rejected its PyJWT `2.13.0` dependency.
Update the source and verification locks to the upstream fixed `2.14.0`, but
do not relabel the old images as repaired. For subsequent application releases,
run vulnerability qualification immediately after the clean bundle build and
before loading Docker Hub credentials, publishing indexes or signing. This
ordering catches package rejection before external publication. It does not
replace the later exact-image signature gate or implement automatic retries.

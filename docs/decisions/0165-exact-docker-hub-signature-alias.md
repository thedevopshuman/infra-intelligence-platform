# ADR 0165: Recognize the exact Docker Hub signature hostname alias

**Status:** Accepted compatibility correction  
**Date:** 2026-10-04

## Context

The `v0.84.2` images were published and keylessly signed by the protected
release workflow. Pinned Cosign 3.1.2 successfully verifies their exact
digests, signer identity, issuer, and transparency evidence, but emits
`index.docker.io` in the verified claim. The release policy uses `docker.io`.
The downstream literal comparison rejected that equivalent registry name.

## Decision

Normalize only the exact `index.docker.io/` prefix to `docker.io/` when
comparing verified claims with the expected repository or digest reference.
Namespace, repository path, digest, signature type, certificate identity and
issuer remain exact. Ports, other hosts (including `registry-1.docker.io`),
case changes, tags, URL encoding and path normalization gain no equivalence.
Cosign must still succeed with ordinary claim and transparency checks.

This is adapter compatibility, not a new trust authority or schema version.
Rejected qualification retains its minimized report and prints only its
bounded failed-check IDs and stable error codes, never raw verifier output.

A separate manual signature recheck accepts two explicit immutable digests
and one strict release tag for the two fixed approved public repositories.
It has read-only permissions and no signing, registry credentials, build,
test-suite, installation or promotion authority. Its success is diagnostic;
it cannot substitute for manifest-bound release qualification.

## Consequences

The failed one-job release cannot resume at an individual step. It saved no
bundle checkpoint. A signature-only recheck does not recreate that lost
bundle, run vulnerability qualification, sign customer archives or publish a
GitHub release. Existing tags and images remain unchanged. Recovering the
bundle must preserve exact image indexes, all platform/attestation blobs and
the original source revision; exported archive bytes must not be presented
as the lost original archive.

## References

- [Upstream registry alias normalization](https://github.com/google/go-containerregistry/blob/main/pkg/name/registry.go)
- [GitHub workflow and job reruns](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs)
- [ADR 0107](0107-exact-release-signature-verification.md)

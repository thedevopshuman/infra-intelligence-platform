# ADR 0157: Apache-licensed source learning preview before production v1

**Status:** Accepted

**Date:** 2026-10-04

## Context

The owner asked to make a first release and learning setup before continuing
production hardening, and delegated the open-source license choice. The owner
already selected public distribution under `thedevopshuman`. Local synthetic
demonstrations are useful without implying live-provider or production proof.

## Decision

- License original repository code and documentation under Apache-2.0. Preserve
  third-party license and attribution obligations. Apache's express patent grant
  fits a business-facing, vendor-neutral infrastructure project; it does not
  grant trademarks or warranties. This is a project licensing choice, not a
  dependency compliance certification.
- Publish `learning-v0.84.0` as a **source-only GitHub prerelease**. It retains
  the current internal software version and alpha contracts. The separate
  `learning-` tag namespace does not invoke the protected `v*` signed OCI release
  workflow and does not claim any of that workflow's evidence.
- Reuse the existing AI fixture, public console, Compose topology, and kernel.
  Put Docker-only startup composition in `scripts/learning.sh` and
  `scripts/learning_fixture.py`. Keep fixtures in an explicitly selected Docker
  build target, outside the default production runtime image.
- Use a separately owned, local Unix-socket-only `iip-learning` project. Its
  known credentials, internal HTTP/trust database, synthetic prices, and tmpfs
  data are suitable only for a disposable single-user exercise. Refuse remote
  contexts, cross-checkout lifecycle operations, and implicit reseeding.
- Publish the guided session and exact limitations with the source. Do not
  publish SDK packages, production images, a stable API promise, or a supported
  customer deployment as part of this milestone.

## Consequences

A learner can start the system without host Python, a Kubernetes cluster, or
cloud credentials and explore real local processing of synthetic input. The
learning release is not a production-candidate qualification report. No
existing release gate, tenant boundary, public schema, or runtime authority is
relaxed. Full public v1 and persistent community recovery/upgrade, lifecycle,
real-provider qualification, signed distribution, and maintenance work remain
in the public-v1 roadmap. The neutral product name remains unchanged.

License reference: [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0).

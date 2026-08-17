# ADR 0051: immutable CI execution dependencies

**Status:** Accepted
**Date:** 2026-08-17

## Context

CI executes third-party action code before repository verification. Major-version action references and a tagged PostgreSQL service can move without a source change, so an otherwise clean revision does not identify all code and image bytes that reached the verification environment.

## Decision

Pin every GitHub Action to a reviewed 40-character commit SHA while retaining its semantic release as a comment. Pin the PostgreSQL 18.4 Alpine service to the same reviewed multi-platform digest used by local recovery tooling. Select the explicit Ubuntu 24.04 runner generation, give the job read-only repository permission, disable checkout credential persistence, cancel superseded runs, and enforce a 15-minute job deadline. Pin the Helm tool version installed by its pinned action.

Add repository tests that reject mutable action or database-image references and missing execution bounds. Configure weekly Dependabot checks for GitHub Actions, Python, and the TypeScript SDK; updates arrive as reviewable pull requests and must pass the same verification gate rather than changing trusted revisions automatically.

## Consequences

- A source revision identifies the exact third-party action commits and PostgreSQL image used by CI.
- Compromise or retagging of a moving major tag cannot silently change an existing workflow revision.
- Dependency refreshes become explicit review events with visible old and new identities.
- GitHub-hosted runner internals and the selected Python/Node patch releases remain provider-managed inputs; stronger hermetic CI would require a reviewed self-hosted runner image and locked toolchain distribution.

# ADR 0122: Orchestrate exact local release qualification as one fail-closed command

**Status:** Accepted

## Context

The release boundary already has separate executable contracts for artifacts,
packaged install and upgrade, vulnerabilities, environment compatibility, and
aggregate readiness. Producing the complete set still requires an operator to
run a long ordered command list and manually keep every path and source
revision aligned. A missed, stale, or reordered step is detected eventually,
but late failure wastes qualification time and encourages informal release
checklists.

## Decision

Provide one repository-owned orchestration command that:

1. refuses dirty source and requires an explicit prior commit that is a strict
   ancestor of the candidate;
2. executes the closed source-quality, local-integration, recovery,
   availability, packaging, N-1, vulnerability, and readiness stages through
   existing Make targets;
3. derives revision-named artifact and report paths from the current committed
   application version and full source revision;
4. never accepts a pre-existing candidate directory or skips a failed stage;
5. verifies the final bundle and requires the complete readiness input set
   (18-of-18 when this decision was adopted; 19-of-19 after ADR 0139) and its
   `locally-qualified` readiness summary; and
6. removes inherited IIP/provider/credential variables, then supplies only the
   closed local toolchain and evidence paths; and
7. checks that source remained clean throughout the run.

The command emits no new release claim and creates no replacement contract. It
composes the owning verifiers and reports already accepted by their respective
ADRs. Generated evidence remains outside Git and outside the immutable bundle.

## Consequences

- A release operator has one repeatable entry point and still receives every
  specialized report for independent review.
- The selected supported predecessor remains explicit; the orchestrator does
  not guess version-support policy from Git history.
- Existing outputs are never deleted or overwritten as a candidate bundle.
  A failed attempt must be investigated and its partial candidate handled
  deliberately before retrying.
- `locally-qualified` still does not mean published, signed, customer-ready,
  production-ready, or design-partner accepted. No customer endpoint,
  credential, provider call, registry publication, or organizational signing
  authority is added.

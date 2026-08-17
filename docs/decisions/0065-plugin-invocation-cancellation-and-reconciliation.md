# ADR 0065: plugin invocation cancellation and reconciliation

**Status:** Accepted
**Date:** 2026-08-17

## Context

ADR 0064 prevents duplicate plugin execution, but an owned claim can remain live
while an operator needs it stopped or ambiguous after a runner disappears.
Deleting a claim or rerunning it would lose evidence or duplicate unknown
impact. A process-local signal would not survive runner or API restarts.

## Decision

PostgreSQL remains the authority for invocation lifecycle. A cancellation is a
tenant-scoped, policy-approved, durable intent with an atomic audit record. The
runner polls that intent from outside the untrusted container, terminates the
container, and stores a terminal cancelled result. Plugins receive neither
ambient cancellation authority nor a control-plane credential.

An accepted cancellation wins over subsequently produced normal output. The
ledger rejects a non-cancelled terminal commit while intent is pending; the
runner then commits `plugin.runtime.cancelled`. A terminal result that won the
transaction first remains immutable and a later cancellation returns it.

Ambiguous claims may be reconciled only by a `platform-admin` whose exact action
is also approved by policy, and only after the immutable invocation deadline.
Reconciliation stores `plugin.execution.outcome-unknown`, never executes the
plugin, and appends audit in the same transaction. It returns an existing
terminal status idempotently.

## Consequences

- Cancellation intent survives API and runner restarts and is visible through
  one public exact-tenant status contract.
- Container termination is cooperative evidence; terminal status, not the
  request response, is the closure fact.
- Unknown prior impact cannot be mislabeled successful or silently retried.
- Reconciliation authority is narrower than ordinary plugin use and requires
  both a platform role and policy approval.
- Connected and side-effecting plugins still require mediated network,
  credential, and action grants before they can be enabled.

## Verification

Schema, application, HTTP, SDK, in-memory, PostgreSQL, concurrent transition,
Docker runner, migration, backup/restore, and Helm upgrade tests cover the
lifecycle. Cross-tenant reads and transitions return no lifecycle data.

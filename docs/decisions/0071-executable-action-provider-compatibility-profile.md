# ADR 0071: executable action-provider compatibility profile

**Status:** Accepted
**Date:** 2026-08-17

## Context

The proposal-only mediation boundary had contract, SDK, policy, audit, grant,
idempotency, runner-routing, and application tests, but the executable Docker
matrix exercised only resource-observer paths. Those tests could not prove that
an isolated action-provider process can use the public SDK through the trusted
relay and reach the ordinary governed proposal queue without receiving approval
or execution authority.

## Decision

Add `host-mediated-action-proposal` as a distinct, additive compatibility
profile. Sign a least-authority action-provider manifest for the same immutable
example image, invoke its `propose-restart` method in the no-network sandbox,
and pass one invocation-bound dry-run grant through the private mediation
socket. Compose the relay with the real `GovernedActionService` over a seeded
completed investigation and canonical resource.

The gate validates the input, invocation result, and proposal receipt, then
reads host state to prove that exactly one proposal is `pending-approval` and
that neither an approval nor an action result exists. The report records the
profile's capability and method and uses closed checks for the proposal queue
and the absence of delegated approval or execution.

## Consequences

- The local matrix now proves the process, signature, sandbox, SDK, socket,
  policy, audit, and governed-proposal path together.
- The action row cannot be confused with read compatibility, and its manifest
  digest is independent from both read-oriented profiles.
- The profile remains fixture-backed exact-host conformance. It does not certify
  a customer plugin, identity issuer, provider, live mutation, rollback, another
  architecture, or a support commitment.
- Approval and execution stay outside the plugin protocol and require their
  existing independent actors and policy decisions.

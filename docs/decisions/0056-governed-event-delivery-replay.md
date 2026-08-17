# ADR 0056: Governed event-delivery quarantine replay

**Status:** Accepted

## Context

Terminal quarantine prevents a permanently failing destination from consuming dispatcher capacity, but operators still need a safe recovery path after the customer receiver is repaired. Direct database edits or an automatic replay loop would bypass policy and audit, and a stale approval could otherwise replay a later quarantine generation with the same outbox identity.

## Decision

Add `event-delivery.requeue` as a second governed action type. A platform administrator may propose it only from a completed investigation with `propose` authority over the event subject resource. The immutable parameters bind the exact tenant-scoped outbox ID, CloudEvents ID, quarantine timestamp, and failed-attempt count shown by the delivery-health contract.

Use the existing independent approval, execution-role, current-policy, digest, expiry, durable one-shot claim, audit, and fail-closed reconciliation chain. Dry-run remains the default and changes no delivery state. A live approved execution atomically clears only the matching quarantine generation, resets its bounded attempt cycle, and preserves the original CloudEvents ID. Any stale generation or tenant mismatch produces a terminal stable precondition failure without mutation.

Replay is explicitly non-reversible and high risk when live. It does not suppress the normal at-least-once guarantee: customer receivers must continue deduplicating `(tenantid, source, id)`. The action exposes no event payload, destination configuration, credential, provider response, or exception text.

## Consequences

- recovery requires explicit operator intent, investigation context, independent review, and a separate executor identity;
- an old approval cannot replay a newly quarantined generation;
- a successful replay gets a fresh finite attempt budget and may quarantine again if the receiver remains unavailable;
- a crash after the one-shot execution claim retains the existing `manual-reconciliation-required` behavior;
- automatic replay, bulk replay, broker-specific recovery, and receiver interoperability certification remain out of scope.

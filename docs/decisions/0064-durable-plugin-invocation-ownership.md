# ADR 0064: durable plugin invocation ownership

**Status:** Accepted
**Date:** 2026-08-17

## Context

ADR 0038 isolates signed, digest-pinned plugins, but its process-local request
ledger forgets claims when the runner restarts. Replaying an invocation after an
unknown process or database failure could duplicate provider impact. A duplicate
request ID alone is also insufficient: it must stay bound to the original
tenant, session, and canonical request content.

## Decision

PostgreSQL is the initial authority for plugin invocation ownership. The runner
must atomically claim `(tenantId, requestId)` before starting a container. Each
claim stores the session ID, canonical invocation digest, complete invocation,
and claim time. The transaction locks the persisted session, verifies the exact
session document, and enforces its request limit across runner replicas.

An exact retry has three outcomes:

- a completed claim returns the stored host-created terminal result without
  starting a container, including after the execution deadline has passed;
- a non-terminal claim returns `plugin.request.reconciliation-required` and is
  never automatically replayed;
- a request ID reused with another session or content digest fails with
  `plugin.request.conflict`.

Successful output and stable runtime failures are committed as terminal
`PluginInvocationResult` documents. Failed and cancelled results contain the
stable error and usage only; `outputDigest` remains meaningful and required only
when output exists. If execution finishes but result persistence is uncertain,
the claim stays ambiguous and future attempts fail closed.

The in-memory implementation follows the same state transitions for local
conformance, but it is not restart durable. Network, credential, action, and
cancellation authority remain disabled until their own mediated workflows and
reconciliation policies are accepted.

## Consequences

- Duplicate delivery cannot automatically duplicate plugin execution across
  process restarts or replicas.
- Terminal results are reconstructable and idempotently recoverable.
- Ambiguous claims require an explicit future reconciliation or cancellation
  operation; operators cannot clear or replay them by restarting the runner.
- Database backup and restore now include plugin claims and terminal results.
- The runner still is not authorized for connected or side-effecting plugins.

## Verification

Unit tests cover content binding, cached terminal results, ambiguous claims,
stable failed results, and request limits. PostgreSQL tests use separate adapter
instances and concurrent claimers to prove restart recovery and serialization.
The Docker conformance runner validates the same terminal-result schema.

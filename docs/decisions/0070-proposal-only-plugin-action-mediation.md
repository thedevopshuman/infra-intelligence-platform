# ADR 0070: proposal-only plugin action mediation

**Status:** Accepted
**Date:** 2026-08-17

## Context

Plugins can perform bounded provider reads through an invocation-local socket,
but action permissions remain rejected. Giving an untrusted container an action
credential, provider mutation endpoint, control-plane token, approval method, or
executor would bypass the platform's one-shot governed action workflow and its
separation of duties.

## Decision

Add a distinct proposal-only action mediation protocol on the existing
invocation-local, no-network socket. A host-issued grant binds one invocation,
tenant, actor, expiry, action-type and target allowlists, dry-run policy,
request count, and maximum proposal lifetime. It must remain within the
manifest-bound action declarations.

The untrusted request contains no endpoint, credential, approval, executor,
policy result, expiry, or idempotency key. The host derives the expiry and a
stable idempotency key, evaluates `plugin:propose-action`, commits an audit
intent, and then invokes the normal governed proposal service. The normal
service independently validates the completed investigation, target,
parameters, current policy, tenant, and idempotency record.

The response is only a proposal receipt. A plugin cannot approve, execute, or
reconcile the proposal; those remain separate authenticated roles and public
control-plane workflows. The first allowed action is the closed Kubernetes
workload restart shape. Event-delivery replay and arbitrary provider mutations
remain unavailable to plugins.

## Consequences

- Plugin recommendations can enter the same visible approval queue as human or
  agent proposals without receiving mutation authority.
- Manifest declarations, invocation grants, current policy, audit, completed
  investigation scope, and the governed action service must all agree.
- Dry-run-only grants provide the default rollout posture; explicitly issued
  grants may allow a live-mode proposal, but still cannot approve or execute it.
- Durable invocation ownership prevents automatic replay after ambiguous runner
  failure, and action proposal idempotency prevents duplicate proposal records.
- Direct action execution, action credentials, arbitrary HTTP mutation, and
  provider-specific error detail never cross the plugin socket.

# ADR 0036: Request-scoped Kubernetes restart execution

**Status:** Accepted
**Date:** 2026-08-17

## Context

The first live action must prove that approval, least privilege, provider preconditions, verification, and rollback compose without turning the control plane into a general Kubernetes client. A rollout restart is narrow, but an unguarded patch can still target a replacement object, escape namespace scope, or leave an unhealthy rollout behind.

## Decision

The only live-capable action remains `kubernetes.restart-workload`. Its adapter is explicitly selected and live execution has a separate integration switch. It derives integration identity from the accepted resource source, requires the observed Kubernetes object UID, requests only `resources:read` and `workloads:patch`, and supports only allowlisted apps/v1 Deployment, StatefulSet, and DaemonSet paths.

Every patch first uses Kubernetes server-side `dryRun=All` and includes the last read `resourceVersion`. The live patch changes only the pod-template annotation `iip.platform/restarted-at`. Success requires the controller to observe the returned generation and all desired replicas to be ready within a configured bound. Verification failure triggers a resource-version-guarded restoration of the prior annotation; the result distinguishes successful rollback from unresolved failure.

## Consequences

- The default runtime remains incapable of live mutation.
- Object replacement and stale-write races fail before a trusted mutation.
- Customers can use an external credential issuer without changing action contracts or application code.
- Rollback restores the prior pod template, but cannot promise that an already unhealthy workload becomes healthy.
- Adding another action type requires its own closed parameters, provider preconditions, verification, rollback, policy semantics, and evaluation fixtures.

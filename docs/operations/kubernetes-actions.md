# Kubernetes governed actions

**Status:** Explicit opt-in executor; dry-run remains the default

The Kubernetes action adapter implements only `kubernetes.restart-workload` for Deployment, StatefulSet, and DaemonSet objects. The normal runtime still composes the local no-impact validator. Select the API adapter only after configuring its protected integration, exact workload RBAC, credential path, approval identities, and rollback response.

## Safety sequence

For every approved execution, the application first persists its one-shot claim. The adapter then:

1. resolves the tenant and integration derived from the accepted resource observation;
2. requests only `resources:read` and `workloads:patch` through the credential broker;
3. reads the exact namespace, kind, and name and compares Kubernetes `metadata.uid` with the observed `providerObjectUid`;
4. submits a strategic merge patch with `dryRun=All` and a `metadata.resourceVersion` precondition;
5. stops there for a dry-run proposal;
6. for an explicitly live proposal, submits the identical patch without `dryRun`;
7. waits for the controller to observe the new generation and for desired replicas to become ready; and
8. if verification expires, restores the prior `iip.platform/restarted-at` annotation using another server-side dry-run and resource-version-guarded patch.

The adapter never changes images, replicas, selectors, containers, environment, volumes, or arbitrary annotations. A replacement object UID, an out-of-scope namespace/kind, malformed response, expired credential, or missing target binding fails closed. No provider response body, endpoint credential, or exception text appears in the public result.

## Runtime configuration

The default is:

```text
IIP_KUBERNETES_ACTION_EXECUTOR=dry-run
```

To use Kubernetes API server-side dry-run, set:

```text
IIP_KUBERNETES_ACTION_EXECUTOR=kubernetes-api
IIP_KUBERNETES_ACTIONS_INTEGRATIONS_JSON=<deploy/kubernetes-actions/integrations.example.json>
```

With the local static broker, also provide `IIP_KUBERNETES_ACTIONS_CREDENTIALS_JSON` as a protected Secret. In production, use `IIP_CREDENTIAL_BROKER_MODE=external-http`; static long-lived credentials are a development facility only. The integration document is closed, tenant-bound, TLS-only, and allowlists cluster identity, namespaces, workload kinds, response size, request timeout, verification duration, and poll interval.

`liveExecutionEnabled` is a second integration-level switch. Keep it `false` while validating approval and server-side dry-run. A proposal must also have `dryRun: false`; neither switch alone grants live authority.

## Kubernetes RBAC

[`deploy/kubernetes-actions/rbac.yaml`](../../deploy/kubernetes-actions/rbac.yaml) shows the maximum verbs: `get` and `patch` on `apps` workloads. Replace its namespace and `resourceNames` with the exact approved scope. Do not bind this role to the API pod's service account and do not enable ambient token mounting. The credential broker should issue a short-lived token for a distinct action identity only after checking tenant, integration, actor, scopes, and deadline.

## Result and recovery

`succeeded` means the new controller generation became ready. `rolled-back` means restart verification failed but the previous annotation was restored and observed on the exact UID. `failed` includes a stable `errorCode`; when rollback was attempted it includes `rollback.status` and a bounded summary.

No action is automatically retried. If the process disappears outside a trusted terminal result, the tenant-explicit workflow timer atomically changes the expired durable lifecycle to `manual-reconciliation-required` and appends one audit record. The timer has no executor and cannot repeat provider impact. Compare the workload UID, generation, annotation, rollout state, proposal digest, approval, and audit records before issuing a new proposal.

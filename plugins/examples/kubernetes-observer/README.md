# Kubernetes observer example

This package is a runnable, read-only resource-observer example. It consumes the public resource collection request contract, normalizes a Kubernetes `List` document, and returns the public collection result contract without importing server internals.

The fixture CLI proves normalization, ordering, tenancy propagation, bounded output, reconciliation completion, and secret omission. An explicit `kubectl` development transport lists each resource type independently, returns a provider cursor map, resumes bounded watches from host-committed state, and recovers expired watches through a fresh full reconciliation. Capability-token verification, cancellation propagation, and process isolation remain host/runtime work.

## Run the conformance fixture

From the repository root:

```bash
PYTHONPATH=sdks/python/src:plugins/examples/kubernetes-observer/src \
  python3 -m kubernetes_observer \
  --request plugins/examples/kubernetes-observer/fixtures/collection-request.json \
  --objects plugins/examples/kubernetes-observer/fixtures/kubernetes-list.json
```

The result must exactly match `fixtures/expected-result.json`. `make verify` runs this comparison and validates every returned resource against the shared JSON Schemas.

## Run against the local kind cluster

Create or reuse the isolated development cluster, seed the known failure, and run a reconciliation collection:

```bash
kind create cluster --name iip-dev --wait 120s
kubectl --context kind-iip-dev apply -f deploy/kubernetes/dev/seed-incident.yaml
PYTHONPATH=sdks/python/src:plugins/examples/kubernetes-observer/src \
  python3 -m kubernetes_observer \
  --request plugins/examples/kubernetes-observer/fixtures/live-collection-request.json \
  --live-context kind-iip-dev \
  --kubeconfig "$HOME/.kube/config"
```

The live mode requires both a named context and an explicit kubeconfig path. It never chooses the current/default context. This is a local development adapter, not the production credential-broker boundary.

The fixture covers cluster, namespace, node, Deployment, ReplicaSet, Pod, Service, Ingress, and ConfigMap resources. It builds `contains`, `owns`, `runs_on`, `reads_from`, and `routes_to` relationships. A `Secret`, ConfigMap values, annotations, pod environment variables, service-account details, and an out-of-scope namespace are deliberately present in the input and absent from the result.

## Live adapter boundary

The development transport issues one list for Namespace, one for Node, and one per configured namespace for every namespaced type. The complete result returns each list `resourceVersion` in `providerCursors` and binds the map to a deterministic aggregate checkpoint. Copy both fields into the next reconciliation request's `spec.resume`; the host accepts them only after the previous snapshot is durable.

The observer resumes every API-path watch from its own cursor. A change, watch timeout, or Kubernetes `410 Gone` closes the cycle with a fresh full list before any snapshot is returned. Expiration discards the whole prior cursor set rather than guessing a later cursor or treating one resource type as empty. Watch bookmarks are accepted but are not expected on a guaranteed schedule. See the Kubernetes [API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/#efficient-detection-of-changes) documentation and [ADR 0009](../../../docs/decisions/0009-provider-cursor-sets-and-watch-recovery.md).

`deploy/rbac.yaml` is a least-privilege example for the declared resource set. It intentionally excludes Secrets and mutation verbs. The ServiceAccount disables automatic token mounting. A real runner must explicitly request a short-lived projected token or receive a request-scoped credential from the host broker; see the Kubernetes [ServiceAccount token](https://kubernetes.io/docs/tasks/configure-pod-container/configure-service-account/) guidance. The RBAC binding is illustrative and must be narrowed to the configured namespaces during installation.

`plugin.json` is duplicated into `contracts/examples/plugin-manifest.json` as the canonical documentation example; repository validation keeps them equal.

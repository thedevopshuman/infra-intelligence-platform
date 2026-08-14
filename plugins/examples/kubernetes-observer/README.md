# Kubernetes observer example

This package is a runnable, read-only resource-observer example. It consumes the public resource collection request contract, normalizes a Kubernetes `List` document, and returns the public collection result contract without importing server internals.

The implemented boundary is deliberately narrower than the future plugin runtime. The fixture CLI proves normalization, ordering, tenancy propagation, bounded output, reconciliation completion, and secret omission. Live Kubernetes authentication, list/watch transport, capability-token verification, health framing, cancellation, and process isolation remain host/runtime work.

## Run the conformance fixture

From the repository root:

```bash
PYTHONPATH=sdks/python/src:plugins/examples/kubernetes-observer/src \
  python3 -m kubernetes_observer \
  --request plugins/examples/kubernetes-observer/fixtures/collection-request.json \
  --objects plugins/examples/kubernetes-observer/fixtures/kubernetes-list.json
```

The result must exactly match `fixtures/expected-result.json`. `make verify` runs this comparison and validates every returned resource against the shared JSON Schemas.

The fixture covers cluster, namespace, node, Deployment, ReplicaSet, Pod, Service, Ingress, and ConfigMap resources. It builds `contains`, `owns`, `runs_on`, `reads_from`, and `routes_to` relationships. A `Secret`, ConfigMap values, annotations, pod environment variables, service-account details, and an out-of-scope namespace are deliberately present in the input and absent from the result.

## Live adapter boundary

A production adapter should use an initial list and then watch from the list collection `resourceVersion`. Kubernetes can return `410 Gone` when a requested version is no longer available; that condition starts a fresh list/reconciliation pass rather than guessing a cursor. Watch bookmarks are useful but do not arrive on a guaranteed schedule. See the Kubernetes [API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/) documentation.

`deploy/rbac.yaml` is a least-privilege example for the declared resource set. It intentionally excludes Secrets and mutation verbs. The ServiceAccount disables automatic token mounting. A real runner must explicitly request a short-lived projected token or receive a request-scoped credential from the host broker; see the Kubernetes [ServiceAccount token](https://kubernetes.io/docs/tasks/configure-pod-container/configure-service-account/) guidance. The RBAC binding is illustrative and must be narrowed to the configured namespaces during installation.

`plugin.json` is duplicated into `contracts/examples/plugin-manifest.json` as the canonical documentation example; repository validation keeps them equal.

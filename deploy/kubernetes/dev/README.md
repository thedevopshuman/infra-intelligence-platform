# Development Kubernetes overlay

Create the namespace and render/install the chart with local image values:

```bash
kubectl apply -f deploy/kubernetes/dev/namespace.yaml
helm upgrade --install iip deploy/helm/infra-intelligence \
  --namespace iip-system \
  -f deploy/kubernetes/dev/values.yaml \
  --set auth.existingSecret=iip-auth
```

The image must be built and made available to your local cluster separately. This overlay is not production configuration. Create `iip-auth` through the cluster's secret-management workflow with an `identities-json` key containing the local verifier configuration described in [local development](../../../docs/operations/local-development.md). Never commit the verifier configuration or raw token.

The chart uses the in-memory runtime unless `database.existingSecret` names a Secret whose `database-url` key contains the PostgreSQL connection string. Create that Secret through the cluster's secret-management workflow; never commit it or put credentials in values files. Set `database.migrations.enabled=true` only when the chart should run the isolated pre-install/pre-upgrade migration Job. Serving pods always have automatic migrations disabled. The disposable `make test-helm-install` gate proves this path against the explicit local kind cluster.

`seed-incident.yaml` is separate from the platform chart. It creates an `iip-demo` namespace, an intentionally failing Deployment, and a harmless reconciliation-probe ConfigMap for the live observer/evaluation smoke test. The live gate deletes only that probe between snapshots to verify host-generated tombstones. Never install the fixture outside a disposable development cluster.

`event-evidence-fixture.yaml` is also test-only. It creates the isolated `iip-event-test` namespace, a zero-replica Deployment, and a service account limited to Deployment `get` and Event `get/list`. `make test-kubernetes-events` adds one Event dynamically, uses a short-lived token, verifies direct HTTPS Event evidence collection, and deletes the namespace. See the [Kubernetes Event evidence guide](../../../docs/operations/kubernetes-event-evidence.md).

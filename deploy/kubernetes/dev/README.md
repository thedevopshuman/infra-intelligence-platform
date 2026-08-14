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

The chart uses the in-memory runtime unless `database.existingSecret` names a Secret whose `database-url` key contains the PostgreSQL connection string. Create that Secret through the cluster's secret-management workflow; never commit it or put credentials in values files. Schema migration is a separate deployment step because `database.autoMigrate` defaults to false.

`seed-incident.yaml` is separate from the platform chart. It creates an `iip-demo` namespace and an intentionally failing Deployment for the live observer/evaluation smoke test. Never install that fixture outside a disposable development cluster.

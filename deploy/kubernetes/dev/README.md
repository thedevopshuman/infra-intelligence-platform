# Development Kubernetes overlay

Create the namespace and render/install the chart with local image values:

```bash
kubectl apply -f deploy/kubernetes/dev/namespace.yaml
helm upgrade --install iip deploy/helm/infra-intelligence \
  --namespace iip-system \
  -f deploy/kubernetes/dev/values.yaml
```

The image must be built and made available to your local cluster separately. This overlay is not production configuration.

The chart uses the in-memory runtime unless `database.existingSecret` names a Secret whose `database-url` key contains the PostgreSQL connection string. Create that Secret through the cluster's secret-management workflow; never commit it or put credentials in values files. Schema migration is a separate deployment step because `database.autoMigrate` defaults to false.

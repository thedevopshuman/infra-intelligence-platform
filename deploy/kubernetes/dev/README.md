# Development Kubernetes overlay

Create the namespace and render/install the chart with local image values:

```bash
kubectl apply -f deploy/kubernetes/dev/namespace.yaml
helm upgrade --install iip deploy/helm/infra-intelligence \
  --namespace iip-system \
  -f deploy/kubernetes/dev/values.yaml
```

The image must be built and made available to your local cluster separately. This overlay is not production configuration.


# Kubernetes Event evidence

**Status:** Live read-only reference adapter

The control plane can query Kubernetes core Events for already-known resources and store the normalized result as immutable Evidence. The public request contains resource UIDs, normalized filters, bounds, and an integration ID; it never contains an API endpoint, token, kubeconfig, namespace wildcard, raw field selector, or command arguments. [ADR 0021](../decisions/0021-read-only-kubernetes-event-api-adapter.md) records the live transport and authority decisions.

## Local real-cluster gate

Use an explicit kubeconfig and context:

```bash
IIP_KUBECONFIG=/absolute/path/to/.kube/config \
IIP_KUBE_CONTEXT=kind-iip-dev \
make test-kubernetes-events
```

The target applies `deploy/kubernetes/dev/event-evidence-fixture.yaml` in the isolated `iip-event-test` namespace. Its service account may only `get` Deployments and `get/list` Events in that namespace. The test creates one warning Event, requests a ten-minute service-account token, extracts the selected context's HTTPS endpoint and CA, executes the adapter and full Evidence commit, checks normalized condition correlation and credential omission, then removes the namespace. It does not enable or depend on Docker Desktop's separate built-in Kubernetes control plane.

This gate is separate from `make verify`: deterministic parsing, scope, credential, deadline, pagination, and normalization tests do not need a cluster, while the opt-in gate proves real Kubernetes API behavior.

## Protected runtime configuration

Select the backend explicitly:

```text
IIP_KUBERNETES_EVENTS_BACKEND=kubernetes-api
IIP_KUBERNETES_EVENTS_INTEGRATIONS_JSON=<protected non-secret registry JSON>
IIP_KUBERNETES_EVENTS_CREDENTIALS_JSON=<secret-bearing broker JSON>
```

Start from `deploy/kubernetes-events/integrations.example.json`. Each integration fixes:

- exact tenant/integration ownership and the expected observer `clusterExternalId`;
- one HTTPS API endpoint and optional absolute CA-bundle path;
- allowed namespaces and platform resource types;
- the namespace used to find Events for cluster-scoped resources;
- request timeout, per-response byte limit, page size, and maximum pages;
- optional reason-to-condition overrides; and
- a logical credential reference.

The static local broker document has this secret-bearing shape and must be supplied only through a protected secret channel:

```json
{
  "credentials": [
    {
      "tenantId": "local",
      "integrationId": "kubernetes-local",
      "credentialRef": "credential://kubernetes/local/events-reader",
      "bearerToken": "REPLACE_WITH_A_SHORT_LIVED_SERVICE_ACCOUNT_TOKEN",
      "expiresAt": "2026-08-16T12:00:00Z"
    }
  ]
}
```

The token must remain valid through the request deadline. Production deployment still needs an external issuer that authorizes, issues, rotates, revokes, and audits short-lived leases; the static JSON broker is a reference boundary, not the production credential design.

Select the shared [external credential broker client](credential-broker.md) to remove provider tokens from ordinary control-plane configuration. In `external-http` mode, `IIP_KUBERNETES_EVENTS_CREDENTIALS_JSON` is not required; the adapter requests exactly `events:read` and `resources:read` for the configured logical reference and deadline. A separately operated issuer remains required.

## Read and normalization behavior

For each requested resource, the adapter parses the observer identity `<cluster>/<namespace>/<name>` (or `<cluster>/<name>` for supported cluster-scoped kinds), constructs a path from a closed built-in resource catalog, and reads the exact object to obtain its current Kubernetes UID. It then lists core Events in the configured namespace with `involvedObject.uid=<current UID>`. Only `GET` requests are possible through this transport.

The first adapter supports Namespace, Node, Pod, Service, ConfigMap, Deployment, ReplicaSet, StatefulSet, DaemonSet, and Ingress identities. An integration must explicitly allow each type it uses. Type, cluster, namespace, name, provider UID, reason, severity, and timestamps are revalidated before normalization.

Kubernetes may omit item TypeMeta inside an `EventList`; omission is accepted because the typed list endpoint supplies it, while conflicting values are rejected. `Normal` and `Warning` become `normal` and `warning`. Known reasons use the built-in taxonomy, integration mappings override it, and unknown valid reasons use `kubernetes.event.unclassified`. Message text never determines authority or taxonomy and always passes Evidence redaction before persistence.

Core Event objects aggregate occurrences. When the aggregate started before the request window, the adapter records only the latest in-window observation with count one. That conservative representation avoids claiming that the aggregate count occurred inside the requested range.

## Helm and network policy

Set `kubernetesEventEvidence.backend`, provide `integrationsJson`, reference a Secret containing the credential JSON, and mount a Secret containing the API CA bundle at the exact path named by the integration. The chart keeps service-account token automount disabled; do not grant the control-plane pod ambient cluster credentials.

If NetworkPolicy is enabled, set `networkPolicy.kubernetesApiEgress.enabled`, the exact API-server CIDR, and port. The default is disabled because the chart cannot safely infer a customer control-plane address. Keep namespace/resource RBAC least-privilege and separate for each customer integration.

## Current limits

This adapter reads core `v1` Events retained by the current API server. It does not watch, reconstruct expired history, traverse related resources, query `events.k8s.io/v1`, use client certificates, or dynamically register integrations. Empty retained state is honest no-data, not proof that no historical Event occurred.

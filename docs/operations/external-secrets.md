# External secret controller integration

**Status:** Executable local controller handoff; customer backend qualification required
**Decision:** [ADR 0102](../decisions/0102-external-secret-controller-handoff.md)

The IIP chart never creates credentials. It accepts only names of existing
Kubernetes Secrets and fixed keys. An external secret controller runs before
the chart and materializes those objects; IIP receives no permission to read the
provider secret store and Helm values contain no secret bytes.

The minimal PostgreSQL-backed production handoff is
[`examples/iip-database.externalsecret.yaml`](../../deploy/helm/infra-intelligence/examples/iip-database.externalsecret.yaml).
It is included in the packaged Helm chart so an operator does not need the
source checkout.
It expects an operator-owned `ClusterSecretStore` named
`iip-production-secret-store` and maps only the provider property `url` into:

| Kubernetes Secret | Required key | Consumer |
| --- | --- | --- |
| `iip-database` | `database-url` | API, worker, receiver, migration and backup jobs when enabled |

Change the remote key and store name for the customer environment. Do not add a
provider credential, database URL, token, certificate private key, or populated
JSON policy to the example or Helm values. Production OIDC with a publicly
trusted issuer requires no OAuth client secret; optional CA, policy, telemetry,
evidence-provider, receiver, and TLS Secret keys remain listed in the relevant
sections of the [Helm deployment guide](helm-deployment.md).

## Complete chart secret inventory

Every secret-bearing chart boundary is name-only. The configured Secret must
exist in the IIP release namespace before the consuming workload is created.
Multiple settings may intentionally name the same Kubernetes Secret, but each
consumer projects only its configured key. Empty optional settings do not mount
anything.

| Helm values path | Default required key or type | Purpose |
| --- | --- | --- |
| `imagePullSecrets` | registry-specific `.dockerconfigjson` | Private image-registry pull authority |
| `database.existingSecret` | `database-url` | PostgreSQL connection URL |
| `auth.existingSecret` | `identities-json` | Controlled-evaluation hashed identities; do not use as production OIDC storage |
| `auth.oidc.caBundleExistingSecret` | `ca.crt` | Optional private OIDC issuer trust anchor |
| `ingress.tls.existingSecret` | Kubernetes TLS Secret (`tls.crt`, `tls.key`) | Customer ingress certificate and private key |
| `policy.externalHttp.bearerTokenExistingSecret` | `token` | Optional external policy service credential |
| `policy.externalHttp.caBundleExistingSecret` | `ca.crt` | External policy service trust anchor |
| `credentialBroker.externalHttp.caBundleExistingSecret` | `ca.crt` | Credential-broker trust anchor; workload identity itself is projected by Kubernetes |
| `eventPublisher.httpsWebhook.tokenExistingSecret` | `token` | Event receiver credential |
| `eventPublisher.httpsWebhook.caBundleExistingSecret` | `ca.crt` | Event receiver trust anchor |
| `telemetry.existingSecret` | `otlp-headers` | Outbound OTLP headers |
| `telemetryEvidence.prometheus.credentialsExistingSecret` | `prometheus-credentials-json` | Protected Prometheus integration credentials |
| `logEvidence.loki.credentialsExistingSecret` | `loki-credentials-json` | Protected Loki integration credentials |
| `logEvidence.opensearch.credentialsExistingSecret` | `opensearch-credentials-json` | Protected OpenSearch integration credentials |
| `kubernetesEventEvidence.credentialsExistingSecret` | `kubernetes-events-credentials-json` | Request-scoped Kubernetes Event reader bindings |
| `kubernetesEventEvidence.caBundleExistingSecret` | `ca.crt` | Kubernetes Event API trust anchor |
| `kubernetesActions.credentialsExistingSecret` | `kubernetes-actions-credentials-json` | Request-scoped governed Kubernetes action bindings |
| `kubernetesActions.caBundleExistingSecret` | `ca.crt` | Kubernetes action API trust anchor |
| `contextEvidence.integrationsExistingSecret` | `context-integrations-json` | Protected context catalog and roots |
| `contextEvidence.credentialsExistingSecret` | `github-context-credentials-json` | Development-only GitHub context credential mapping; production uses the external broker |
| `contextEvidence.caBundleExistingSecret` | `ca.crt` | Optional GitHub Enterprise trust anchor |
| `investigationSignalCatalog.existingSecret` | `investigation-signal-catalog-json` | Tenant-scoped investigation candidate policy |
| `evidenceRedaction.policiesExistingSecret` | `evidence-redaction-policies-json` | Content-addressed exact-tenant additive Evidence privacy policy |
| `aiAttribution.policiesExistingSecret` | `ai-attribution-policies-json` | Protected application/team attribution policy |
| `aiCostEngine.catalogsExistingSecret` | `ai-price-catalogs-json` | Versioned AI price catalogs |
| `aiCostEngine.qualificationsExistingSecret` | `ai-price-catalog-qualifications-json` | Exact protected catalog qualification policies and current reports |
| `aiSavingsEngine.profilesExistingSecret` | `ai-savings-profiles-json` | Deterministic savings-rule profiles |
| `otlpReceiver.channelsExistingSecret` | `otlp-receiver-channels-json` | Tenant-bound metrics intake channels |
| `otlpLogsReceiver.channelsExistingSecret` | `otlp-logs-receiver-channels-json` | Tenant-bound logs intake channels |
| `aiUsageReceiver.channelsExistingSecret` | `ai-usage-receiver-channels-json` | Tenant-bound metadata-only AI usage channels |
| `otlpIngest.tls.serverExistingSecret` | `tls.crt`, `tls.key` | OTLP receiver server identity |
| `otlpIngest.tls.clientCaExistingSecret` | `ca.crt` | OTLP client trust bundle |
| `otlpIngest.tls.clientCrlExistingSecret` | `ca.crl` | Optional current client revocation list |
| `otlpIngest.tls.identitiesExistingSecret` | `otlp-client-identities-json` | Exact SPIFFE identity-to-channel bindings |

Key names are configurable through their adjacent `*SecretKey` values except
the standard ingress TLS keys. Keeping the defaults reduces configuration
drift. JSON-bearing values remain subject to their documented closed contracts;
External Secrets Operator transports bytes but does not validate IIP policy.

Apply the store and `ExternalSecret`, wait for its `Ready=True` condition and
the target Secret, then install IIP with:

```bash
helm upgrade --install iip deploy/helm/infra-intelligence \
  --namespace iip-system \
  --set database.existingSecret=iip-database \
  --set auth.mode=oidc
```

Run the local compatibility profile with Docker Desktop and the explicit
`kind-iip-dev` context. Every Kubernetes and Helm operation names that context;
the profile does not read or change the caller's current context:

```bash
make test-external-secrets
```

The profile verifies the official controller chart digest, installs External
Secrets Operator 2.10.0 with its multi-architecture image index pinned, creates
runtime-only source values, proves named-secret least authority, target creation,
source-to-target rotation propagation, and IIP chart rendering, then removes its disposable
namespaces and exact test RBAC. The Kubernetes provider is only a deterministic
local stand-in. A customer promotion must repeat the profile with the actual
secret backend and separately approve store identity, encryption, rotation,
deletion, outage, audit, recovery, and break-glass policy.

Kubernetes does not refresh environment variables in an already running
container when a Secret changes. After a synchronized database URL, credential,
header, channel, catalog, or policy value changes, perform a reviewed rolling
restart of the workloads that consume it (or qualify a controller that does so)
and verify readiness plus the old credential's revocation. Mounted certificate
and CA files may update on the volume, but IIP components that load them at
startup still require a rollout unless the relevant runbook explicitly proves
hot reload. Target synchronization alone is not an application-rotation claim.

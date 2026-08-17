# Helm deployment and schema migration

**Status:** Executable self-hosted reference path

The chart can install the control-plane API against an existing PostgreSQL database, apply packaged schema migrations through a separately authorized hook, optionally declare a TLS-only Ingress binding, and schedule checksum-complete logical backups into existing protected storage. It does not create a production database, backup storage, identity provider, ingress controller, certificate/private key, or image registry.

Reviewed automatic investigation candidates are optional. Set `investigationSignalCatalog.existingSecret` and `secretKey` to project the same tenant-bound [signal catalog](investigation-signal-catalog.md) into the API and worker. The chart never renders the catalog body into a ConfigMap or values-derived manifest.

## Required inputs

Before installation, provide:

- an immutable platform image available to the cluster;
- a PostgreSQL database and an existing Secret whose `database-url` key is a complete connection URL;
- authentication configuration through an existing Secret or the documented OIDC mode;
- reviewed tenant, policy, network, telemetry, backup, and retention settings.

The default repository and tag are placeholders. Set `image.repository` to the published repository and `image.digest` to the verified release OCI index digest in production. A non-empty digest takes precedence over the tag for the API, worker, OTLP receiver, and migration Job. The chart injects its version and this exact digest into application workloads so authenticated users can verify them through `GET /v1/system/version` and the console.

## Validate configuration before access

The chart's `values.schema.json` is a closed customer configuration contract. Unknown keys, unsupported modes, unsafe Service exposure, relaxed container security, and out-of-range settings fail schema validation. Render-time guards also reject invalid relationships such as a heartbeat that cannot renew its lease, an exporter-health stale threshold shorter than two reports, exporter-SLO retention shorter than its window, an event-delivery latency objective as long as its measurement window, telemetry export without an OTLP endpoint, or backup scheduling without existing storage and database-only NetworkPolicy.

Run both checks against the exact protected values file before installation:

```bash
helm lint deploy/helm/infra-intelligence \
  --values /protected/path/iip-values.yaml
helm template iip deploy/helm/infra-intelligence \
  --namespace iip-system \
  --values /protected/path/iip-values.yaml >/dev/null
```

The chart intentionally exposes only `ClusterIP` Services and keeps service-account token automounting disabled. Put any later external access behind a separately reviewed authenticated TLS boundary; do not turn the internal HTTP Service into an internet-facing load balancer.

## Authenticated TLS ingress

The optional Ingress keeps the backend Service internal and requires the customer's existing TLS Secret and controller. Configure the exact controller class, DNS host, TLS redirect annotation, and controller NetworkPolicy selectors, for example:

```yaml
ingress:
  enabled: true
  className: nginx
  host: iip.example.com
  tls:
    existingSecret: iip-console-tls
  tlsRedirectAnnotation: nginx.ingress.kubernetes.io/force-ssl-redirect

networkPolicy:
  enabled: true
  ingressController:
    enabled: true
    namespaceSelector:
      kubernetes.io/metadata.name: ingress-nginx
    podSelector:
      app.kubernetes.io/component: controller
```

The chart sets the selected redirect annotation to `"true"` and does not permit `NodePort` or `LoadBalancer` Services. Before customer use, independently verify HTTPS-only routing, the full certificate chain and rotation, maximum request/header/time settings, controller availability, source-IP behavior, and the OIDC issuer/origin configuration. Bearer or OIDC authentication remains enforced by the application after TLS termination.

## Customer console OIDC sign-in

Production console sign-in uses the same OIDC verifier as API clients plus an optional public browser profile. Register `https://<ingress-host>/console` as an exact redirect URI at the identity provider, use Authorization Code with `S256` PKCE, and configure the issuer to return an RS256 JWT access token with the exact audience and actor, tenant, and roles claims expected by the API. The client is public: do not create, embed, or mount a client secret.

`auth.oidc.configJson` is rendered into non-secret application configuration. Treat it as reviewed deployment policy even though the browser subset is public. A complete profile is structurally similar to:

```yaml
auth:
  mode: oidc
  existingSecret: ""
  oidc:
    configJson: >-
      {"issuer":"https://identity.example.com/","audience":"iip-control-plane","jwksUrl":"https://identity.example.com/.well-known/jwks.json","actorClaim":"sub","tenantClaim":"iip_tenant_id","rolesClaim":"iip_roles","browser":{"clientId":"iip-console","authorizationEndpoint":"https://identity.example.com/oauth2/authorize","tokenEndpoint":"https://identity.example.com/oauth2/token","redirectUri":"https://iip.example.com/console","scopes":["openid","profile"],"providerLabel":"Organization SSO"},"cacheSeconds":300,"clockSkewSeconds":30}
    caBundleExistingSecret: iip-oidc-ca
    caBundleSecretKey: ca.crt
    caBundleMountPath: /var/run/iip-oidc-ca

networkPolicy:
  oidcEgress:
    enabled: true
    cidr: 203.0.113.10/32
    port: 443
```

The browser calls the token endpoint directly. Its CORS policy must allow the exact console origin, `POST`, and the `content-type` header without cookies. The authorization and token endpoint URLs must not contain query strings, fragments, or embedded credentials. The console's CSP adds only the configured token endpoint origin to `connect-src`; server-side NetworkPolicy independently controls JWKS access from the API pod.

Before rollout, run `make test-oidc` to certify the shipped verifier's local real-TLS profile, then qualify the selected issuer and browser flow using the [OIDC runbook](oidc-identity.md). Verify the public `GET /v1/authentication/console` response, redirect registration, MFA and consent behavior, token audience and lifetime, tenant/role claim mapping, CORS denial for other origins, logout expectations, key and certificate rotation, revocation expectations, outage, and recovery. The platform keeps no refresh token, ID token, identity-provider password, or server session.

### External policy qualification

Select the fail-closed external decision adapter with reviewed configuration
and existing Secrets. Paths inside `configJson` must match the mounted files:

```yaml
policy:
  mode: external-http
  externalHttp:
    configJson: >-
      {"endpoint":"https://policy.example.internal/v1/data/iip/decision","caBundlePath":"/var/run/iip-policy-ca/ca.crt","bearerTokenPath":"/var/run/iip-policy-token/token","timeoutSeconds":5,"maxResponseBytes":65536}
    bearerTokenExistingSecret: iip-policy-token
    caBundleExistingSecret: iip-policy-ca

networkPolicy:
  policyEgress:
    enabled: true
    cidr: 203.0.113.20/32
    port: 443
```

Before rollout, run `make test-policy-engine` to certify the shipped adapter's
local real-TLS profile, then qualify the selected engine using the
[policy-engine runbook](policy-engine.md). Review every closed application
action, immutable snapshot semantics, credential and certificate rotation,
revocation, response bounds, audit delivery, outage, failover, and recovery.
Never use the permissive local policy mode in a production deployment.

## Mutual-TLS OTLP intake

The OTLP listener is a separate Deployment and ClusterIP Service. Production
enablement requires a server keypair, a client CA, a protected SPIFFE identity
registry, tenant-bound channel configuration, and exact Collector ingress. A
representative values fragment is:

```yaml
database:
  existingSecret: iip-database

otlpReceiver:
  enabled: true
  channelsExistingSecret: iip-otlp-metrics-channels

otlpLogsReceiver:
  enabled: true
  channelsExistingSecret: iip-otlp-logs-channels

otlpIngest:
  tls:
    mode: mutual-spiffe
    serverExistingSecret: iip-otlp-server-tls
    clientCaExistingSecret: iip-otlp-client-ca
    identitiesExistingSecret: iip-otlp-client-identities

networkPolicy:
  enabled: true
  databaseEgress:
    enabled: true
    cidr: 10.20.30.40/32
    port: 5432
  otlpReceiverIngress:
    enabled: true
    namespaceSelector:
      kubernetes.io/metadata.name: observability
    podSelector:
      app.kubernetes.io/name: customer-collector
```

The server Secret uses the configured `tls.crt` and `tls.key` keys. The client
CA Secret contains `ca.crt`; the identity Secret contains
`otlp-client-identities-json` in the shape of
[`client-identities.example.json`](../../deploy/otlp/client-identities.example.json).
The chart mounts these only into the receiver. Health probes use HTTPS without
a client certificate and reveal only stable status. Every OTLP POST still
requires both the client certificate and channel Bearer credential.

Run `make test-otlp-receiver` before promotion. It writes a source-bound report
after real official-exporter, mTLS/SPIFFE, PostgreSQL durability, and Collector
persistent-queue checks. Production Collectors should adapt the validated
[`collector-to-iip.example.yaml`](../../deploy/otel/collector-to-iip.example.yaml)
and mount its queue directory on a durable volume.

## Fresh durable install

Create the database and identity Secrets through the cluster's secret-management workflow, then use a protected values file similar to:

```yaml
image:
  repository: registry.example.test/iip/control-plane
  tag: 0.55.0
  digest: sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

database:
  existingSecret: iip-database
  migrations:
    enabled: true

auth:
  mode: local-hashed
  existingSecret: iip-auth

eventDeliverySlo:
  windowSeconds: 3600
  maximumDeliveryLatencySeconds: 60
  minimumAttainmentBasisPoints: 9900
  minimumEligibleEvents: 20

investigationCompletionSlo:
  windowSeconds: 3600
  maximumCompletionSeconds: 300
  minimumAttainmentBasisPoints: 9900
  minimumEligibleJobs: 20

investigationQueue:
  maxOutstandingJobsPerTenant: 1000

evidenceRetention:
  enabled: false
  intervalSeconds: 3600
  ephemeralSeconds: 86400
  standardSeconds: 2592000
  extendedSeconds: 31536000
  batchSize: 100

worker:
  enabled: true
  tenants: [tenant-a, tenant-b]
  investigationConcurrency: 4
  maxTenantInvestigationConcurrency: 1

queryAvailabilitySlo:
  windowSeconds: 3600
  minimumAvailabilityBasisPoints: 9990
  minimumEligibleRequests: 100

telemetry:
  metricsEnabled: true
  otlpEndpoint: https://otel-collector.observability.svc:4318

networkPolicy:
  enabled: true
  databaseEgress:
    enabled: true
    cidr: 10.20.30.40/32
    port: 5432
```

Install with an explicit namespace and values source:

```bash
helm upgrade --install iip deploy/helm/infra-intelligence \
  --namespace iip-system \
  --create-namespace \
  --values /protected/path/iip-values.yaml \
  --wait --timeout 10m
```

The migration NetworkPolicy hook is created first, then the migration Job. The Job receives only the database Secret and runs every packaged migration under an advisory lock. The serving Deployment rolls out only after the hook succeeds. `/readyz` independently opens a bounded database connection and verifies the latest migration recorded by the new image. After rollout, compare the authenticated runtime report's application, required migration, source revision, chart version, and image digest with the promoted release evidence.

`worker.investigationConcurrency` bounds simultaneous investigation tasks in each worker pod. `worker.maxTenantInvestigationConcurrency` is enforced from durable unexpired leases across all replicas; keep its default of one until measured tenant workloads justify a larger share. The process scheduler gives each enrolled tenant at most one local task and rotates polling order. These controls do not replace resource requests/limits, completion-SLO monitoring, or deliberate tenant sharding when the enrolled tenant count is much larger than available process slots.

`investigationQueue.maxOutstandingJobsPerTenant` bounds each tenant's durable non-terminal backlog across API replicas. Size it from measured arrival rate, completion capacity, and acceptable queue delay; do not raise it merely to hide sustained completion-SLO misses. A full tenant receives `429 investigation.queue.capacity-exceeded`; an exact idempotent resubmission still returns its existing job. Alert on sustained rejection at the ingress/API telemetry layer and investigate worker capacity or a faulty submitter.

Run the [investigation capacity certification](investigation-capacity.md) before promotion. The local PostgreSQL profile proves the queue's large-tenant and overload invariants, but customer sizing must repeat and extend it on the intended database, network, worker topology, and provider-latency distribution.

`evidenceRetention` is disabled by default. Enabling it requires the workflow worker and exact tenant enrollment. Review legal hold and backup lifecycle first, then use the [evidence retention runbook](evidence-retention.md); the administrator endpoint is observe-only and deletion remains bounded, policy-gated, tenant-serialized, and audited.

The local-hashed authentication example is suitable for a controlled evaluation. The default Secret name is `iip-auth`, but the chart never creates its sensitive contents. Configure the documented OIDC boundary for production; never place local verifier JSON, raw tokens, or an OAuth client secret in a values file. The reviewed OIDC verifier and public-client profile are non-secret configuration; protect changes to them as security policy. Serving pods always receive `IIP_DATABASE_AUTO_MIGRATE=false`; the hook is the chart's only schema authority.

## Scheduled logical backups

Backups are disabled by default. To enable them, pre-create a protected PersistentVolumeClaim and configure `backup.enabled`, its schedule/time zone, `backup.destination.existingClaim`, and exact database egress. The digest-pinned PostgreSQL client receives the database Secret and backup volume but no identity Secret, service-account token, or general network access. It writes a custom-format dump and publishes its checksum sidecar only after the dump catalog validates.

The chart does not create or prune storage. Before customer use, verify encryption, off-cluster replication, failure-domain separation, immutability, retention, capacity alerts, access review, and restore authorization. The complete configuration and operator checks are in [PostgreSQL backup and restore](postgresql-backup-restore.md).

## Upgrade procedure

Before upgrading:

1. Review migration and compatibility notes between the installed and target versions.
2. Complete and verify the database backup procedure for the deployment's RPO/RTO policy.
3. Put the target OCI index digest in `image.digest` and render the exact values with `helm template`.
4. Run `helm upgrade --install ... --wait`; do not bypass a failed hook.
5. Verify the migration Job log, Deployment rollout, `/readyz`, and the customer workflow appropriate to the environment.

The migration hook is forward-only. Helm application rollback does not reverse database changes. A schema rollback needs an explicit reviewed recovery procedure.

## Local install conformance

With Docker Desktop and the explicit `kind-iip-dev` context available, run:

```bash
make test-helm-install
```

The gate builds and loads the current image, registers its exact host-platform digest on every disposable kind node, creates an exact-name namespace and PostgreSQL instance, installs the chart with migrations enabled, verifies the hook applied the latest packaged migration, and proves API readiness from inside the pod. It then authenticates without printing the generated credential and requires the runtime report, event-delivery health, and rolling publication objective to match the installed configuration. A second Helm revision adds two API replicas, a TLS Ingress declaration, and scheduled backup configuration. The gate proves immutable image selection, runtime identity, migration idempotency, and exact ingress binding; runs the installed backup CronJob on demand; verifies its persisted checksum; restores it into a separate database; confirms every packaged migration; and checks rollout and Helm history. It removes the namespace and claim, refuses non-kind contexts, and never prints generated credentials, database contents, or private keys. Set `IIP_KEEP_TEST_NAMESPACE=true` only when retaining a failed local fixture for debugging is intentional.

The first revision additionally proves the public local console-authentication discovery document, the investigation-completion objective, and the disabled-by-default Evidence retention report from inside the installed pod.

## Production gaps

This proves deployment mechanics, not production certification. The local release path generates verified SBOM/provenance and 128-tenant PostgreSQL overload evidence; the chart declares guarded TLS ingress and schedules logical backups; the worker enforces bounded tenant-fair investigation admission; and the kind gate restores one. Organizational image signing, external secret-controller integration, controller-specific TLS conformance, high availability, zero-downtime migration compatibility, customer-environment sustained workload and failover tests, database failover, storage durability/encryption/retention, point-in-time recovery, disaster recovery, and environment-specific policy remain release and customer gates.

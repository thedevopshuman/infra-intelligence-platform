# Helm deployment and schema migration

**Status:** Executable self-hosted reference path

The chart can install the control-plane API against an existing PostgreSQL database, apply packaged schema migrations through a separately authorized hook, optionally declare a TLS-only Ingress binding, and schedule checksum-complete logical backups into existing protected storage. It does not create a production database, backup storage, identity provider, ingress controller, certificate/private key, or image registry.

Reviewed automatic investigation candidates are optional. Set `investigationSignalCatalog.existingSecret` and `secretKey` to project the same tenant-bound [signal catalog](investigation-signal-catalog.md) into the API and worker. The chart never renders the catalog body into a ConfigMap or values-derived manifest.

Additional tenant privacy detectors are optional. Set
`evidenceRedaction.policiesExistingSecret` and `policiesSecretKey` to project
one protected, content-addressed [redaction policy set](evidence-redaction.md)
into the API, worker, and isolated receiver. Invalid configuration prevents a
process from serving; omitting it disables only the optional email/IPv4
detectors and never disables mandatory credential redaction.

Protected repository context is optional and disabled by default. The
`production-github-context.values.yaml` overlay selects immutable-revision
GitHub reads, projects the allowlist and private CA from existing protected
objects, and requires explicit provider egress. Production uses the shared
request-scoped credential broker; the static credential Secret is a local
development fallback only. Follow the [context evidence
runbook](context-evidence.md) and retain compatibility evidence before enabling
the adapter for a customer repository.

The optional AI cost engine is worker-owned and disabled by default. Its
protected catalog wrapper comes from an existing Secret. It is mounted into
the API only when generation-bound allocation reporting is explicitly enabled,
and is never mounted into the OTLP receiver. `aiCostEngine.enabled=true` requires the worker,
an exact tenant enrollment, and `aiCostEngine.catalogsExistingSecret`; fixture
pricing remains separately prohibited by default. Production additionally
requires `aiCostEngine.requireQualification=true` and a protected
`qualificationsExistingSecret`; the worker revalidates the exact report,
catalog, policy, level, and current time before every pass. Follow the
[AI cost-engine runbook](ai-cost-engine.md) before enabling it.

The optional AI savings engine is also worker-owned and disabled by default.
It requires the cost engine, exact worker tenant enrollment, and a protected
profile wrapper from an existing Secret. `aiSavingsEngine` configuration is
never projected into the API or OTLP receiver. Suitability reports marked
`test-fixture` are rejected unless `aiSavingsEngine.allowTestFixtures=true`;
that switch is forbidden by the production preflight profile. Follow the
[AI savings-engine runbook](ai-savings-engine.md) before enabling it.
When OTLP metrics are enabled, its aggregate projection uses
`telemetry.aiEconomicsAttributeMode`; retain the default `tenant-scope` unless
the destination is independently isolated to one tenant.

Protected AI attribution is worker-owned and disabled by default. Enabling
`aiAllocationReporting` additionally gives the API read-only access to the
same attribution-policy and price-catalog Secrets so it can validate every
joined ledger fact against the configured generations. The API does not
receive attribution or cost execution flags, and the receiver receives neither
Secret. The reporting worker projects only stable protected application/team
IDs; display names remain API data and never become metric labels.

## Required inputs

Before installation, provide:

- an immutable platform image available to the cluster;
- a PostgreSQL database and an existing Secret whose `database-url` key is a complete connection URL;
- authentication configuration through an existing Secret or the documented OIDC mode;
- reviewed tenant, policy, network, telemetry, backup, and retention settings.

The chart never creates credential-bearing Secrets. The
[external-secret-controller runbook](external-secrets.md) provides the packaged
database handoff example, inventories all optional Secret names and keys, and
defines the local exact-key/rotation compatibility gate.

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

For a production-shaped deployment, use the closed source- and
configuration-bound gate instead of treating a successful render as enough.
It ships non-secret core, protected GitHub context, and AI FinOps examples,
checks the complete profile,
and can verify exact prerequisite objects and keys in an explicitly named
context before Helm applies anything. Follow the
[customer deployment preflight](customer-deployment-preflight.md). Only a
cluster-mode pass is `install-ready`, and even that result remains
`pre-install-only` rather than customer certification.

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
    clientCrlExistingSecret: iip-otlp-client-crl
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
CA Secret contains `ca.crt`; for an intermediate-issued client, this bundle
must include the intended root and the CRL-signing issuing intermediate while
the Collector presents its leaf plus required intermediate chain. The optional
CRL Secret contains exactly one PEM
CRL under `ca.crl`; and the identity Secret contains
`otlp-client-identities-json` in the shape of
[`client-identities.example.json`](../../deploy/otlp/client-identities.example.json).
The CRL must be signed by a CA in the configured client bundle with explicit
CA and CRL-signing authority. The chart mounts these only into the receiver. Health probes use HTTPS without
a client certificate and reveal only stable status. A configured CRL must be
no larger than 1 MiB, have a `lastUpdate` no later than the receiver clock, and
have a future `nextUpdate`. Once it expires, readiness and intake fail closed.
Before that deadline, publish a new immutable, version-named CRL Secret and
atomically upgrade `otlpIngest.tls.clientCrlExistingSecret`; changing the Secret
reference rolls the receiver Deployment. If the same Secret name is updated in
place, an explicit receiver rollout is still required because the SSL context
does not hot-reload projected bytes. Every OTLP POST still requires both the
client certificate and channel Bearer credential. Follow the complete rotation
and rollback procedure in the
[receiver runbook](otlp-metrics-receiver.md#crl-rotation-runbook).

Run `make test-otlp-receiver` before promotion. It writes a source-bound report
after real official-exporter, intermediate-CA mTLS/SPIFFE, current and rotated
CRL, PostgreSQL durability, and Collector persistent-queue checks. Production Collectors should adapt the validated
[`collector-to-iip.example.yaml`](../../deploy/otel/collector-to-iip.example.yaml)
and mount its queue directory on a durable volume.

## Fresh durable install

Create the database and identity Secrets through the cluster's secret-management workflow, then use a protected values file similar to:

```yaml
image:
  repository: registry.example.test/iip/control-plane
  tag: 0.83.0
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

aiAttribution:
  enabled: true
  policiesExistingSecret: iip-ai-attribution

aiCostEngine:
  enabled: true
  catalogsExistingSecret: iip-ai-prices
  requireQualification: true
  qualificationsExistingSecret: iip-ai-price-qualifications

aiAllocationReporting:
  enabled: true
  sourceRecordLimit: 10000
  windowSeconds: 86400
  intervalSeconds: 60

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

Every long-running application Deployment uses a surge-first rollout that
creates one extra replica, permits zero unavailable replicas, and keeps each
new pod ready for two seconds before advancing. API and receiver termination
budgets are closed values contracts:

```yaml
apiTermination:
  gracePeriodSeconds: 60
  endpointDrainSeconds: 5
otlpIngest:
  terminationGracePeriodSeconds: 60
  endpointDrainSeconds: 5
worker:
  terminationGracePeriodSeconds: 120
```

Kubernetes first marks a terminating endpoint and runs the bounded pre-stop
delay so Service and ingress routing can converge. `SIGTERM` then stops new API
or receiver acceptance while the process joins active request handlers before
closing its shared runtime. The worker stops polling and waits for its bounded
in-flight work. Each endpoint delay must be smaller than its total grace
period; Helm rejects an invalid relationship. Size the remaining budget above
the customer's qualified longest request or provider call. Kubernetes still
force-kills work that outlives the total budget.

The production examples enable separate API, worker, and receiver
PodDisruptionBudgets as applicable. Each integer `minAvailable` must preserve
at least one replica and remain below its replica count. They also enable the
closed `availability.topologySpread` setting with hostname, `maxSkew: 1`,
`minDomains: 2`, and `DoNotSchedule`; the chart generates an exact selector for
each component. The default is disabled so a one-node development cluster
remains usable. A hard spread policy intentionally leaves redundant replicas
Pending when two eligible topology domains are unavailable, so validate node
labels and capacity before rollout.

`worker.investigationConcurrency` bounds simultaneous investigation tasks in each worker pod. `worker.maxTenantInvestigationConcurrency` is enforced from durable unexpired leases across all replicas; keep its default of one until measured tenant workloads justify a larger share. The process scheduler gives each enrolled tenant at most one local task and rotates polling order. These controls do not replace resource requests/limits, completion-SLO monitoring, or deliberate tenant sharding when the enrolled tenant count is much larger than available process slots.

`investigationQueue.maxOutstandingJobsPerTenant` bounds each tenant's durable non-terminal backlog across API replicas. Size it from measured arrival rate, completion capacity, and acceptable queue delay; do not raise it merely to hide sustained completion-SLO misses. A full tenant receives `429 investigation.queue.capacity-exceeded`; an exact idempotent resubmission still returns its existing job. Alert on sustained rejection at the ingress/API telemetry layer and investigate worker capacity or a faulty submitter.

Run the [investigation capacity certification](investigation-capacity.md) before promotion. The local PostgreSQL profile proves the queue's large-tenant and overload invariants, but customer sizing must repeat and extend it on the intended database, network, worker topology, and provider-latency distribution.

`evidenceRetention` is disabled by default. Enabling it requires the workflow worker and exact tenant enrollment. Review legal hold and backup lifecycle first, then use the [evidence retention runbook](evidence-retention.md); the administrator endpoint is observe-only and deletion remains bounded, policy-gated, tenant-serialized, and audited.

The local-hashed authentication example is suitable for a controlled evaluation. The default Secret name is `iip-auth`, but the chart never creates its sensitive contents. Configure the documented OIDC boundary for production; never place local verifier JSON, raw tokens, or an OAuth client secret in a values file. The reviewed OIDC verifier and public-client profile are non-secret configuration; protect changes to them as security policy. Serving pods always receive `IIP_DATABASE_AUTO_MIGRATE=false`; the hook is the chart's only schema authority.

## Scheduled logical backups

Backups are disabled by default. To enable them, pre-create a protected PersistentVolumeClaim and configure `backup.enabled`, its schedule/time zone, `backup.destination.existingClaim`, and exact database egress. The digest-pinned PostgreSQL client receives the database Secret and backup volume but no identity Secret, service-account token, or general network access. It writes a custom-format dump and publishes its checksum sidecar only after the dump catalog validates.

The chart does not create or prune storage. Before customer use, verify encryption, off-cluster replication, failure-domain separation, immutability, retention, capacity alerts, access review, and restore authorization. The complete configuration and operator checks are in [PostgreSQL backup and restore](postgresql-backup-restore.md). Retain the strict local source-bound report with `make test-backup-restore`, then run `make verify-backup-restore-report` from the clean release checkout; that report remains a logical-restore regression gate rather than production continuity evidence.

## Upgrade procedure

Before upgrading:

1. Review migration and compatibility notes between the installed and target versions.
2. Complete and verify the database backup procedure for the deployment's RPO/RTO policy.
3. Put the target OCI index digest in `image.digest` and render the exact values with `helm template`.
4. Run `helm upgrade --install ... --wait`; do not bypass a failed hook.
5. Verify the migration Job log, all Deployment rollouts, PodDisruptionBudgets,
   topology placement, `/readyz`, graceful-drain logs, and the customer workflow
   appropriate to the environment.

The migration hook is forward-only. Helm application rollback does not reverse database changes. A schema rollback needs an explicit reviewed recovery procedure.

## Local install conformance

With Docker Desktop and the explicit `kind-iip-dev` context available, run:

```bash
make test-helm-install
```

To test the actual packaged release chart and attested OCI archive instead of
checkout artifacts, first build the release bundle and then run:

```bash
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-0.83.0-0123456789ab \
  make test-release-install PYTHON=.venv/bin/python
```

For every target, prove the supported N-1 transition using the packaged target
and the explicit prior release revision. Equal latest migrations are allowed;
a migration regression is not:

```bash
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-0.83.0-0123456789ab \
IIP_UPGRADE_FROM_REVISION=48f2168 \
  make test-release-upgrade PYTHON=.venv/bin/python
```

For promotion evidence, run both through `make qualify-release` with the same
variables. It writes a minimized report adjacent to the bundle and requires
both closed profiles before `make verify-release-qualification` succeeds. The
report binds the exact release-manifest digest and measured local environment;
it does not turn Kind results into a production-cluster claim.

The source gate builds and loads the current image; the release gate verifies
the bundle, loads its OCI image, and uses its packaged chart. Both register the
exact immutable digest on every disposable Kind node, create an exact-name
namespace and PostgreSQL instance, install with migrations enabled, verify the
hook applied the latest packaged migration, and prove API readiness from inside
the pod. They authenticate without printing the generated credential and
require the runtime report, event-delivery health, and rolling publication
objective to match the installed configuration. A second Helm revision adds
two API replicas, a TLS Ingress declaration, and scheduled backup
configuration. The gates prove immutable image selection, runtime identity,
migration idempotency, and exact ingress binding; run the installed backup
CronJob on demand; verify its persisted checksum; restore it into a separate
database; confirm every packaged migration; and check rollout and Helm history.
They remove the namespace and claim, refuse non-Kind contexts, and never print
generated credentials, database contents, or private keys. Set
`IIP_KEEP_TEST_NAMESPACE=true` only when retaining a failed local fixture for
debugging is intentional.

The N-1 gate separately seeds data through the public API, upgrades to the
packaged target, rolls the application back without reversing the forward-only
database migration, and re-upgrades. It requires both application versions to
remain ready against the expected schema, preserves the tenant record throughout,
and rejects duplicate or missing migrations. A separate non-service-account pod
sends authenticated release-identity and Resource reads through the Service for
the full transition. It accepts only the two exact source revisions and requires
zero request or data failures.

After the target stabilizes, the same gate takes an exclusive bounded lock on
the Resource projection, starts a direct authenticated read against one API
pod, verifies that the read is blocked in PostgreSQL, and terminates that exact
pod. The connection must return the original tenant data after the lock clears,
and the replacement Deployment must become ready. This distinguishes Service
availability from preservation of work already accepted by a terminating
process.

The first revision additionally proves the public local console-authentication discovery document, the investigation-completion objective, and the disabled-by-default Evidence retention report from inside the installed pod.

## Production gaps

This proves deployment mechanics, not production certification. The local release path generates verified SBOM/provenance, a manifest-bound install/N-1 qualification report, separate source-bound PostgreSQL logical-recovery and physical-streaming/promotion/named-target-recovery reports, a minimized static/live customer deployment preflight report, a source-bound three-node planned-disruption availability report, and 128-tenant PostgreSQL overload evidence; the chart declares guarded TLS ingress, component-aware disruption budgets and hard topology spread, ships a controller-neutral external-secret example, and schedules logical backups; the worker enforces bounded tenant-fair investigation admission; the external-secret gate proves exact-key synchronization and rotation with named-secret-only source authority; the owned Kind availability gate drains one worker while requiring zero-failure API and OTLP Service access and complete component recovery; and the N-1 gate proves a data-preserving upgrade, application rollback against the forward schema, idempotent re-upgrade, zero-failure bounded reads through the internal Service, and completion of one deliberately blocked read during target-pod termination. The preflight proves configuration and prerequisite presence only. Organizational image signing, customer secret-backend/controller interoperability, controller-specific end-to-end TLS and upgrade conformance, production request-duration and streaming profiles, production-scale latency/load and failure-injected availability, customer-environment sustained workload and failover tests, automatic database failover/fencing, storage durability/encryption/retention, customer-hosted point-in-time recovery, disaster recovery, and environment-specific policy remain release and customer gates. See [Kubernetes availability qualification](kubernetes-availability-qualification.md) for the exact local boundary.

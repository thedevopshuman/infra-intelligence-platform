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

The chart's `values.schema.json` is a closed customer configuration contract. Unknown keys, unsupported modes, unsafe Service exposure, relaxed container security, and out-of-range settings fail schema validation. Render-time guards also reject invalid relationships such as a heartbeat that cannot renew its lease, telemetry export without an OTLP endpoint, or backup scheduling without existing storage and database-only NetworkPolicy.

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

## Fresh durable install

Create the database and identity Secrets through the cluster's secret-management workflow, then use a protected values file similar to:

```yaml
image:
  repository: registry.example.test/iip/control-plane
  tag: 0.30.0
  digest: sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

database:
  existingSecret: iip-database
  migrations:
    enabled: true

auth:
  mode: local-hashed
  existingSecret: iip-auth

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

The local-hashed authentication example is suitable for a controlled evaluation. The default Secret name is `iip-auth`, but the chart never creates its sensitive contents. Configure the documented OIDC boundary for production; do not place either local verifier JSON or OIDC client material directly in a values file. Serving pods always receive `IIP_DATABASE_AUTO_MIGRATE=false`; the hook is the chart's only schema authority.

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

The gate builds and loads the current image, registers its exact host-platform digest on every disposable kind node, creates an exact-name namespace and PostgreSQL instance, installs the chart with migrations enabled, verifies the hook applied the latest packaged migration, and proves API readiness from inside the pod. It then authenticates without printing the generated credential and requires the runtime report to match the application version, contract version, migration, chart, and exact installed digest. A second Helm revision adds two API replicas, a TLS Ingress declaration, and scheduled backup configuration. The gate proves immutable image selection, runtime identity, migration idempotency, and exact ingress binding; runs the installed backup CronJob on demand; verifies its persisted checksum; restores it into a separate database; confirms every packaged migration; and checks rollout and Helm history. It removes the namespace and claim, refuses non-kind contexts, and never prints generated credentials, database contents, or private keys. Set `IIP_KEEP_TEST_NAMESPACE=true` only when retaining a failed local fixture for debugging is intentional.

## Production gaps

This proves deployment mechanics, not production certification. The local release path generates verified SBOM/provenance evidence; the chart declares guarded TLS ingress and schedules logical backups; and the kind gate restores one. Organizational image signing, external secret-controller integration, controller-specific TLS conformance, high availability, zero-downtime migration compatibility, capacity tests, database failover, storage durability/encryption/retention, point-in-time recovery, disaster recovery, and environment-specific policy remain release and customer gates.

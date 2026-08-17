# Helm deployment and schema migration

**Status:** Executable self-hosted reference path

The chart can install the control-plane API against an existing PostgreSQL database and apply packaged schema migrations through a separately authorized hook. It does not create a production database, identity provider, ingress, certificate, or image registry.

## Required inputs

Before installation, provide:

- an immutable platform image available to the cluster;
- a PostgreSQL database and an existing Secret whose `database-url` key is a complete connection URL;
- authentication configuration through an existing Secret or the documented OIDC mode;
- reviewed tenant, policy, network, telemetry, backup, and retention settings.

The default repository and tag are placeholders. Pin a released image digest in production rather than relying on a mutable tag.

## Fresh durable install

Create the database and identity Secrets through the cluster's secret-management workflow, then use a protected values file similar to:

```yaml
image:
  repository: registry.example.test/iip/control-plane
  tag: 0.25.0

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

The migration NetworkPolicy hook is created first, then the migration Job. The Job receives only the database Secret and runs every packaged migration under an advisory lock. The serving Deployment rolls out only after the hook succeeds. `/readyz` independently opens a bounded database connection and verifies the latest migration recorded by the new image.

The local-hashed authentication example is suitable for a controlled evaluation. Configure the documented OIDC boundary for production; do not place either local verifier JSON or OIDC client material directly in a values file. Serving pods always receive `IIP_DATABASE_AUTO_MIGRATE=false`; the hook is the chart's only schema authority.

## Upgrade procedure

Before upgrading:

1. Review migration and compatibility notes between the installed and target versions.
2. Complete and verify the database backup procedure for the deployment's RPO/RTO policy.
3. Pin the target image digest and render the exact values with `helm template`.
4. Run `helm upgrade --install ... --wait`; do not bypass a failed hook.
5. Verify the migration Job log, Deployment rollout, `/readyz`, and the customer workflow appropriate to the environment.

The migration hook is forward-only. Helm application rollback does not reverse database changes. A schema rollback needs an explicit reviewed recovery procedure.

## Local install conformance

With Docker Desktop and the explicit `kind-iip-dev` context available, run:

```bash
make test-helm-install
```

The gate builds and loads the current image, creates a disposable exact-name namespace and PostgreSQL instance, installs the chart with migrations enabled, verifies the hook applied the latest packaged migration, proves API readiness from inside the pod, and removes the namespace. It refuses non-kind contexts and never prints generated credentials. Set `IIP_KEEP_TEST_NAMESPACE=true` only when retaining a failed local fixture for debugging is intentional.

## Production gaps

This proves deployment mechanics, not production certification. Image signing/SBOM/provenance, external secret-controller integration, ingress/TLS, high availability, zero-downtime migration compatibility, capacity tests, database failover, scheduled backups, disaster recovery, and environment-specific policy remain release and customer gates.

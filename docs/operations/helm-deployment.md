# Helm deployment and schema migration

**Status:** Executable self-hosted reference path

The chart can install the control-plane API against an existing PostgreSQL database, apply packaged schema migrations through a separately authorized hook, and optionally declare a TLS-only Ingress binding. It does not create a production database, identity provider, ingress controller, certificate/private key, or image registry.

## Required inputs

Before installation, provide:

- an immutable platform image available to the cluster;
- a PostgreSQL database and an existing Secret whose `database-url` key is a complete connection URL;
- authentication configuration through an existing Secret or the documented OIDC mode;
- reviewed tenant, policy, network, telemetry, backup, and retention settings.

The default repository and tag are placeholders. Pin a released image digest in production rather than relying on a mutable tag.

## Validate configuration before access

The chart's `values.schema.json` is a closed customer configuration contract. Unknown keys, unsupported modes, unsafe Service exposure, relaxed container security, and out-of-range settings fail schema validation. Render-time guards also reject invalid relationships such as a heartbeat that cannot renew its lease or telemetry export without an OTLP endpoint.

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

The local-hashed authentication example is suitable for a controlled evaluation. The default Secret name is `iip-auth`, but the chart never creates its sensitive contents. Configure the documented OIDC boundary for production; do not place either local verifier JSON or OIDC client material directly in a values file. Serving pods always receive `IIP_DATABASE_AUTO_MIGRATE=false`; the hook is the chart's only schema authority.

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

The gate builds and loads the current image, creates a disposable exact-name namespace and PostgreSQL instance, installs the chart with migrations enabled, verifies the hook applied the latest packaged migration, and proves API readiness from inside the pod. It then performs a second Helm revision with two API replicas and a TLS Ingress declaration, proving the hook remains idempotent and the exact class, host, existing TLS Secret, redirect policy, NetworkPolicy peer, rollout, and Helm history are preserved. It removes the namespace, refuses non-kind contexts, and never prints generated credentials or private keys. Set `IIP_KEEP_TEST_NAMESPACE=true` only when retaining a failed local fixture for debugging is intentional.

## Production gaps

This proves deployment mechanics, not production certification. The local release path generates verified SBOM/provenance evidence and the chart declares a guarded TLS Ingress, but organizational image signing, external secret-controller integration, controller-specific TLS conformance, high availability, zero-downtime migration compatibility, capacity tests, database failover, scheduled backups, disaster recovery, and environment-specific policy remain release and customer gates.

# ADR 0102: keep secret synchronization outside the application chart

**Status:** Accepted
**Date:** 2026-09-05

## Context

The Helm chart consumes existing Kubernetes Secrets but deliberately does not
create credentials. Operators previously had no executable example proving that
an external controller could create and rotate those exact Secret names and
keys before IIP installation. Adding provider credentials or secret values to
Helm values would weaken the existing authority boundary.

## Decision

Keep secret-store selection and synchronization outside the IIP chart. Publish
one value-free External Secrets Operator `v1` example that maps a protected
provider property to the required `iip-database/database-url` key. The example
is included in the packaged chart and references a pre-existing
`ClusterSecretStore`; it does not define provider
authentication, a cloud backend, or any secret value.

Add an exact-version local compatibility profile using the official External
Secrets Operator 2.10.0 Helm artifact after verifying its published SHA-256
digest. The disposable profile:

- installs the controller on the explicit local Kind cluster with its controller,
  webhook, and certificate-controller image pinned to one OCI index digest;
- uses the Kubernetes provider only as a local stand-in for an external store;
- grants a dedicated non-mounted service account `get` access to one named
  upstream Secret and denies list plus unrelated-secret reads;
- maps only the required database and local-test authentication properties into
  the exact chart Secret names and keys;
- proves target creation and source-to-target rotation without printing values;
  and
- renders the IIP chart against the controller-created Secret names before
  deleting the disposable namespaces and cluster-scoped test RBAC.

The local upstream Secret is generated at runtime and exists only inside the
disposable source namespace. It is never committed or reported.

## Consequences

Customers can use External Secrets Operator without granting it authority
through IIP or placing secret material in chart values. Other controllers remain
compatible because the stable product boundary is still an existing Kubernetes
Secret with documented keys.

Synchronization into a Kubernetes Secret is not application hot reload.
Environment-backed settings and startup-loaded files require a reviewed workload
rollout and post-rotation readiness/revocation verification unless a separate
executable profile proves live reload for that exact consumer.

The profile does not qualify AWS Secrets Manager, Azure Key Vault, Google Secret
Manager, Vault, or a customer controller installation. Production rollout must
review the selected store authentication, namespace scope, encryption, rotation,
deletion, outage, audit, and break-glass behavior and must run the same
create/rotate/readiness checks in that environment.

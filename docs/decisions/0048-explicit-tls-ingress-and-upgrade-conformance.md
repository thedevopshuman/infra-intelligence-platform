# ADR 0048: explicit TLS ingress and upgrade conformance

**Status:** Accepted
**Date:** 2026-08-17

## Context

The control-plane Service was intentionally cluster-internal. Customers still need a portable way to place the authenticated API and console behind their ingress controller without turning the plain backend Service into a public load balancer. The chart also needs evidence that its migration hook remains idempotent during upgrade, not only during a fresh install.

## Decision

Keep the API Service `ClusterIP` and add an opt-in Kubernetes `Ingress`. Enabling it requires an explicit ingress class, exact host, existing TLS Secret, and controller-specific TLS-redirect annotation. It also requires NetworkPolicy plus exact ingress-controller namespace and pod selectors; the controller is added as a permitted peer without replacing the existing internal API peer. The chart never creates certificate private keys or invents a controller policy.

Extend the disposable kind conformance gate to perform two Helm revisions. Revision one applies the full schema to a clean PostgreSQL database and reaches readiness. Revision two repeats the migration hook, scales the API, adds the TLS Ingress and controller NetworkPolicy peer, and proves that migration count is unchanged, the Deployment is ready, the exact class/host/Secret/redirect binding is stored, and Helm records the second revision as deployed.

The local cluster does not claim controller-specific TLS routing because it deliberately installs no ingress controller. Production readiness still requires HTTPS-only behavior, certificate-chain and rotation checks, OIDC redirect/origin configuration, header/body/time limits, controller availability, and an external scan against the customer's selected controller.

## Consequences

- The chart has a portable, fail-closed TLS ingress declaration without exposing the backend Service directly.
- Plain HTTP redirect behavior is an explicit controller annotation rather than an undocumented assumption.
- NetworkPolicy ingress authority names both the controller namespace and pods.
- Schema migrations are proven idempotent across an actual Helm upgrade, while zero-downtime compatibility across different application versions remains a release gate.

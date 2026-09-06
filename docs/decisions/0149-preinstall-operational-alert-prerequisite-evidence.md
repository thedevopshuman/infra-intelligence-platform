# ADR 0149: Prove operational alert prerequisites before installation

**Status:** Accepted

**Date:** 2026-09-06

## Context

ADR 0148 supplies an optional Prometheus Operator rule adapter, but a rendered
resource alone does not show that a production profile selected it or that the
target cluster can accept it. Discovering that the CRD or target namespace is
missing during `helm upgrade --install` is an avoidable customer-facing
failure.

The existing customer deployment preflight already binds exact values, renders
the complete chart, and reads named prerequisite objects without mutation. It
is the narrow owner for pre-install checks, but it must not become the owner of
Prometheus, Alertmanager, notification contacts, or escalation policy.

## Decision

1. Add the sanitized operational-alert profile to the chart-generated
   deployment profile: enablement, fixed API and metric-name profile, resolved
   namespace, and selector-label count only.
2. Require both production profiles to enable the rule handoff, use the
   supported `monitoring.coreos.com/v1` and underscore/no-suffix profiles,
   resolve a valid namespace, configure at least one selector label, and enable
   metric export.
3. In live preflight mode, use read-only Kubernetes discovery to require the
   namespaced `PrometheusRule` resource and verify that the exact target
   namespace already exists. Retain only closed pass/fail results; do not retain
   the context, namespace, API response, UID, rule labels, or object names.
4. Keep static mode honest by reporting both live checks as `not-run`. If the
   cluster API is unavailable, both prerequisite checks fail rather than being
   inferred.
5. Continue to list customer operational alert routing as an external
   qualification requirement. API discovery and namespace presence do not
   prove Prometheus rule selection, evaluation, missing-data detection,
   Alertmanager routing, contact delivery, recovery, or escalation.

## Consequences

- A production configuration cannot receive `configuration-ready` without a
  bounded alert policy, and a cluster configuration cannot receive
  `install-ready` without the exact CRD discovery and namespace prerequisites.
- The preflight identity needs non-resource discovery access plus `get` on the
  target namespace, but no create, update, patch, delete, list, or watch
  authority is added.
- The additive `v1alpha1` report now has 26 core or 32 AI checks and four live
  checks. Exact sequences, stable errors, derived summaries, SDK unions, and
  the example remain closed and verified.
- Customer alert delivery remains a later live qualification and operating
  decision.

## Alternatives considered

- Letting Helm fail on an absent CRD or namespace was rejected because it
  turns a deterministic prerequisite into a partial installation failure.
- Asking the preflight identity to create the CRD or namespace was rejected
  because that would import cluster-administration authority.
- Treating discovery as proof of notification delivery was rejected because
  the rule selector, metric stream, evaluator, router, and contact path are
  separate customer systems.

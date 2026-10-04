# ADR 0156: Persistent single-host community onboarding

**Status:** Accepted implementation boundary

**Date:** 2026-10-04

## Context

Public-first distribution needs an installation that starts empty and retains
operator data. The existing AI FinOps Docker topology deliberately uses
synthetic prices/traffic, known credentials, and disposable storage. Reusing it
unchanged would confuse demo evidence with a real installation. Its primary
Grafana totals also depend on completed fixed-window savings evaluations,
which is unsuitable for a continuously populated usage view.

## Decision

Add a separate, explicitly pre-release **single-host community preview**.
The operational launcher under `scripts/` composes the existing server image,
public configuration contracts, PostgreSQL, isolated OTLP receiver, official
Collector, Prometheus, and Grafana. It introduces no server use case, provider
SDK, inference proxy, promotion report, or additional production-preflight
profile. Existing production and release gates retain their authority.

- Require one exact tenant/channel, non-fixture catalog and attribution,
  currently valid production-catalog qualification, and optional reviewed
  savings profiles. Never infer prices, organizational ownership, missing
  meters, or permission for a provider call.
- Generate independent opaque credentials and a local trust domain in
  owner-only state. Verify PostgreSQL hostname/CA identity across all clients;
  authenticate Collector intake over TLS and receiver delivery with both
  SPIFFE mTLS and its separate channel token. API/Grafana HTTP is loopback-only
  and not an external production ingress posture.
- Run a network-disabled one-shot root initializer only to project selected
  files and set named-volume ownership. Serving services and the separate
  migrator run non-root, without Docker sockets or cloud credentials.
- Require an explicitly local Unix-socket Docker daemon before dispatching
  credentials. Exclude runtime/environment/credential directories from image
  build contexts. Preserve ordinary package dependency boundaries.
- Filter exact reviewed service/provider/model/operation/region values and
  metadata types **before** the Collector's persistent queue. Drop content
  fields, events, links, unknown metadata, status/name/schema text and free-form
  error type. Hash provider request IDs before disk; the receiver's second
  hash remains deterministic but differs from direct receiver correlation.
  The operator still disables source content capture; dropped telemetry cannot
  support complete-accounting claims.
- Store immutable configuration generations including rendered Collector and
  dashboard content. Serialize CLI commands and require a stopped/removed
  project before switching the manifest. Retain credentials, prior generations,
  and named data volumes on ordinary shutdown and configuration changes.
- Use existing rolling allocation metrics for the new dashboard's primary
  usage, estimated cost, ownership, and coverage views. Count one organizational
  dimension once; render currency/scale from the catalog. Keep optional
  fixed-window change/savings separate and never manufacture a zero result.

## Consequences and limitations

This is a concrete installation step, not completion of public v1. Local
health and configuration checks are distinct from same-invocation provider,
ledger, pricing, and dashboard evidence. Synthetic Docker regression tests
remain synthetic even though they exercise the non-fixture-enabled runtime.

Public images/licensing/signing, external identity/ingress, backup/restore,
upgrade, alerting and supported-host qualification remain release work.
Certificate renewal/rollback is not automated; leaf certificates expire after
one year and the CA signing key is not retained. AI-ledger retention is not yet
implemented, and disabled event publication retains outbox backlog. These
limitations are explicit operational blockers for unattended long-term use,
not permission to weaken production gates or silently discard audit facts.

See the [installation runbook](../operations/community-installation.md) and
[public v1 release plan](../roadmap/public-v1-release-plan.md).

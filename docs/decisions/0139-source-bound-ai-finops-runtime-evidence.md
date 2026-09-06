# ADR 0139: Retain source-bound local AI FinOps runtime evidence

**Status:** Accepted

**Date:** 2026-09-08

## Context

The Docker AI FinOps gate already proves the reference data path, but a console
success line cannot be cross-bound to an exact release candidate. Release
readiness needs machine-verifiable evidence that telemetry intake, durable
usage/cost facts, explicit coverage, deterministic findings, aggregate export,
and the Grafana dashboard worked together. Retaining raw ledger rows, model
names, token quantities, prices, or amounts would expose unnecessary data and
turn a compatibility report into an accounting export.

## Decision

Generate an `AiFinopsRuntimeCompatibilityReport` after the complete disposable
Docker flow passes. Bind it to the source revision, dirty state, application
version, cost-engine version, Docker platform/runtime, closed profile, fixed
aggregate counts, ordered checks, and explicit local-only limitations. Derive
its ID from the complete retained content and provide an independent verifier
that can require a clean source.

The report contains no provider target, tenant, service, invocation identity,
usage quantity, rate, monetary amount, credential, endpoint, or content. It
records fixture pricing and synthetic provider spans as limitations rather than
using this evidence to satisfy the separate live-provider or price-authority
gate.

## Consequences

- An exact candidate can carry reproducible evidence for the complete local AI
  FinOps reference flow instead of relying on human interpretation of logs.
- The report is added to local release readiness without weakening the
  existing external customer/provider/pricing boundaries.
- The `local-candidate-only` release-readiness profile advances from 18 to 19
  ordered inputs and the one-command local qualifier must generate and verify
  this report before aggregation.
- Customer deployment, Collector/PKI, live provider, approved price catalog,
  and invoice claims still require their own reports and cross-binding.
- Aggregate measurements are fixed to the accepted fixture; changing the flow
  requires an intentional contract/profile revision.

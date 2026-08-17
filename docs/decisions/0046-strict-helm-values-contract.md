# ADR 0046: strict Helm values contract

**Status:** Accepted
**Date:** 2026-08-17

## Context

The chart exposes security, tenancy, worker, evidence-backend, OpenTelemetry, receiver, and network settings. YAML accepts misspelled keys and type coercion, while several valid-looking combinations cannot produce a safe runtime. Discovering those mistakes only after a pod starts makes installation harder to diagnose and can accidentally relax a deployment boundary.

## Decision

Treat `values.schema.json` as the customer-facing configuration contract for each chart minor version. It closes unknown fields, enumerates supported modes, bounds counts, ports, timeouts, replicas, and resource shapes, and fixes invariant security settings such as non-root execution, read-only root filesystems, disabled service-account token automounting, and internal-only Services.

Render-time guards validate relationships that JSON Schema draft 7 cannot express clearly: heartbeat versus lease, retry ordering, trace batch versus queue, required OTLP endpoints, authentication configuration, external policy and credential-broker configuration, and enabled receiver channel Secrets. Invalid settings must fail before Kubernetes resources are applied.

The chart remains extensible through explicitly typed label, annotation, scheduling, affinity, and toleration maps. Adding or renaming a value is a chart contract change and must update the schema, default values, documentation, and validation tests together.

## Consequences

- Typos, unsupported modes, unsafe exposure, and most invalid bounds fail during lint, template, install, or upgrade.
- The default local authentication Secret name is explicit; the chart never generates or stores credentials in values.
- API and OTLP Services remain `ClusterIP` until an authenticated TLS ingress boundary is implemented and tested.
- Existing private values files containing undocumented keys may need correction before upgrading to chart `0.24.0`.

# ADR 0041: isolated OTLP receiver process

**Status:** Accepted  
**Date:** 2026-08-17
**Amended by:** [ADR 0080](0080-mutual-tls-otlp-workload-identity-and-buffering.md)

## Context

The first OTLP metrics and logs receivers reused the control-plane HTTP listener. Channel credentials were independent, but one process still held interactive identity configuration, receiver credentials, browser assets, control operations, and telemetry intake. That made listener exposure, scaling, network policy, and failure domains unnecessarily coupled.

## Decision

OTLP/HTTP intake runs as a dedicated process and listener. It exposes only `/healthz`, `/readyz`, `/v1/metrics`, and `/v1/logs`; it composes no interactive authenticator, historical telemetry backend, credential broker, action executor, console, or control-plane routes. It requires the shared durable PostgreSQL store so accepted telemetry Evidence remains available to investigations and control-plane readers.

Protected channel configuration continues to derive tenant, integration, resource, catalog, limits, sensitivity, and retention. A per-process, per-channel token bucket rejects excess request rate before reading a body, while channel budgets bound encoded, decompressed, normalized, cardinality, age, and processing costs. Global or horizontally coordinated rate enforcement remains an ingress responsibility.

The control-plane composition disables OTLP intake by default. `IIP_OTLP_RECEIVER_MODE=shared` remains an explicit development compatibility mode, not the Helm topology. Helm places channel secrets only in the receiver pod, creates a dedicated Service and component selector, and can restrict ingress to an exact Collector namespace/pod set. The receiver has no ambient Kubernetes service-account token.

## Consequences

- Compromise or overload of the intake listener has a smaller credential and route surface.
- API and receiver replicas, network policy, ports, credentials, and resource budgets can change independently.
- Deployments must provide durable database connectivity before enabling either receiver signal.
- Static high-entropy channel Bearer credentials remain the implemented workload credential. Federated workload identity or mTLS, rotation automation, distributed admission, durable intake buffering, and receiver SLOs remain explicit production decisions.

# Integration configuration contract

**Status:** v1alpha1

`IntegrationConfig` records tenant-scoped provider setup without embedding credentials. `credentialRef` is an opaque broker reference; the referenced secret is resolved only for one authorized provider request. Collection scope, schedule, and declared read permissions are reviewable independently of secret material.

The Kubernetes example is reconciliation-first and read-only. Configuration parameters are validated by the selected plugin. Disabling an integration prevents new collection sessions but does not erase historical resources, events, evidence, or audit records.

The current public shape is reconciliation/collector-oriented. The first Prometheus telemetry-evidence adapter therefore uses a protected deployment registry rather than pretending this contract already has a complete registration, credential-broker, and lifecycle use case. [ADR 0015](../decisions/0015-prometheus-telemetry-evidence-adapter.md) defines that reference boundary. A future additive contract revision can unify collector and evidence integrations after dynamic registration authority and storage are implemented.

# Python SDK boundary

This package contains public transport and model types only. It does not import the server kernel. The API will expand contract-first; unstable methods remain under the `v1alpha1` namespace in their paths and models.

```python
from infra_intelligence_sdk import Client, ResourceObservation

client = Client("http://localhost:8080", bearer_token=token)
accepted = client.ingest_resource(ResourceObservation.from_dict(payload))
```

`Evidence`, `InvestigationRequest`, and `InvestigationReport` expose the corresponding `v1alpha1` public envelopes without importing server implementation classes. `run_investigation`, `get_investigation`, and `get_evidence` use the executable reference API.

`InvestigationSignalCatalog` models protected tenant profile documents for configuration tooling. `InvestigationRequest.catalog_snapshot` reads server-owned profile and resolved-subset provenance from an accepted request; clients must not populate that field when submitting work. Report `signalPlan` entries identify request versus protected-catalog origin without exposing protected query configuration.

`InvestigationRequest.telemetry_selections` returns typed `InvestigationTelemetrySelection` candidates. Each contains only a provider-neutral query, output limits, and an optional closed `InvestigationTelemetryInterpretation`; authenticated identity, resources, time range, and deadline are inherited by the server from the investigation. `InvestigationReport.telemetry_assessments` exposes the applied unit-aware rule and committed Evidence citation without importing server internals.

`KubernetesEventEvidenceRequest` and `KubernetesEventEvidenceResult` expose the normalized customer-cluster event boundary, which is distinct from internal platform CloudEvents. `InvestigationRequest.kubernetes_event_selections` and `InvestigationReport.kubernetes_event_assessments` expose condition-count correlation and committed Evidence citations. The SDK never carries kubeconfig, cluster credentials, or Kubernetes client types.

`EvaluationScenario` exposes the offline, replayable scenario envelope used by evaluation tooling. It carries fixtures and a scoring oracle but grants no runtime access or authority.

`ResourceObservationCursor` represents optional source ordering, replay checkpoint, and reconciliation metadata carried inside a resource observation.

`ResourceCollectionRequest` and `ResourceCollectionResult` expose the bounded public interface used by resource-observer plugins. `request.resume` carries a host-committed aggregate checkpoint and opaque provider cursor map; `result.provider_cursors` reads the candidate cursor map from a complete result. A complete reconciliation response from `ingest_resource_collection` may include host-generated deleted Resource observations after the plugin observations. These lightweight envelope types do not import the server kernel.

`Client.get_resource_neighborhood` and `Client.get_resource_timeline` return the corresponding paginated public envelopes. Pass `spec.page.nextCursor` back unchanged to continue; the cursor is query- and tenant-bound and does not grant authority.

`Client.get_ingestion_freshness` returns an `IngestionFreshnessReport` for one source in the credential-derived tenant. The response exposes applied objectives and stable violations, not provider cursors or resource contents.

`Client.get_runtime_version` returns the authenticated non-secret application, contract, required storage migration, build revision, Helm chart, and immutable image identity reported by the answering process. Optional deployment evidence is absent when it was not supplied rather than inferred from a mutable tag.

`Client.get_event_delivery_health` gives a platform administrator the credential-tenant's bounded outbox backlog and quarantine summary. It exposes CloudEvents identity metadata and stable failure codes, never event payloads, provider responses, or destination configuration.

`EventDeliveryReplayCommand` and `Client.propose_event_delivery_replay` bind one exact quarantine generation into the normal investigation, independent approval, policy, audit, and one-shot execution workflow. Dry-run is the default; live replay preserves the CloudEvents ID so receivers must remain idempotent.

Version 0.3 adds collection ingestion, investigation, evidence, governed action, and plugin-session methods. Supply a Bearer credential; the authenticated server derives tenant, actor, and roles from that credential. The SDK never sends identity assertion headers.

Version 0.4 adds opaque provider cursor resume fields, ingestion-freshness telemetry, backend-neutral telemetry evidence, and the stored `OtlpMetricsEvidence` artifact model. OTLP transport is intentionally not reimplemented by this client; send metrics with a standard OpenTelemetry SDK/Collector and the separately provisioned channel credential.

Version 0.5 adds investigation telemetry interpretation and assessment types. Threshold rules are root-cause scoped, unit-aware, and evaluated server-side against committed normalized Evidence.

Version 0.6 adds baseline-window comparison request and report types. Ordered baseline and evaluation subranges are evaluated from one committed normalized artifact as a difference or ratio, without adding a backend query.

Version 0.7 adds Kubernetes Event evidence request/result, investigation selection/assessment models, and `collect_kubernetes_event_evidence`. The default server backend returns honest no-data until a live adapter is configured.

# Python SDK boundary

This package contains public transport and model types only. It does not import the server kernel. The API will expand contract-first; unstable methods remain under the `v1alpha1` namespace in their paths and models.

```python
from infra_intelligence_sdk import Client, ResourceObservation

client = Client("http://localhost:8080", bearer_token=token)
accepted = client.ingest_resource(ResourceObservation.from_dict(payload))
```

`Evidence`, `InvestigationRequest`, and `InvestigationReport` expose the corresponding `v1alpha1` public envelopes without importing server implementation classes. `run_investigation`, `get_investigation`, and `get_evidence` use the executable reference API.

`EvaluationScenario` exposes the offline, replayable scenario envelope used by evaluation tooling. It carries fixtures and a scoring oracle but grants no runtime access or authority.

`ResourceObservationCursor` represents optional source ordering, replay checkpoint, and reconciliation metadata carried inside a resource observation.

`ResourceCollectionRequest` and `ResourceCollectionResult` expose the bounded public interface used by resource-observer plugins. `request.resume` carries a host-committed aggregate checkpoint and opaque provider cursor map; `result.provider_cursors` reads the candidate cursor map from a complete result. A complete reconciliation response from `ingest_resource_collection` may include host-generated deleted Resource observations after the plugin observations. These lightweight envelope types do not import the server kernel.

`Client.get_resource_neighborhood` and `Client.get_resource_timeline` return the corresponding paginated public envelopes. Pass `spec.page.nextCursor` back unchanged to continue; the cursor is query- and tenant-bound and does not grant authority.

Version 0.3 adds collection ingestion, investigation, evidence, governed action, and plugin-session methods. Supply a Bearer credential; the authenticated server derives tenant, actor, and roles from that credential. The SDK never sends identity assertion headers.

Version 0.4 adds opaque provider cursor resume fields to the resource-collection boundary.

# TypeScript SDK boundary

The SDK exposes public contract types and a small fetch-based client. It has no dependency on server code or a framework runtime.

```ts
import { InfrastructureIntelligenceClient } from "@iip/sdk";

const client = new InfrastructureIntelligenceClient({
  baseUrl: "http://localhost:8080",
  bearerToken: token,
});
```

The package exports `Evidence`, `InvestigationRequest`, and `InvestigationReport` as transport-independent `v1alpha1` contract types and provides executable investigation/evidence client methods.

`EvaluationScenario` describes offline graph, timeline, alert, evidence, request, and scoring fixtures. It is an evaluation artifact, not an API method or authority grant.

Resource observer integrations use the exported `ResourceCollectionRequest`, `ResourceCollectionResume`, and `ResourceCollectionResult` types. Resume state combines the last committed aggregate checkpoint with an opaque provider cursor map. A complete reconciliation response may append host-generated deleted `ResourceObservation` items after the plugin observations. The types describe capability payloads only and expose no server implementation classes.

`getResourceNeighborhood` and `getResourceTimeline` expose the paginated read models. Treat `nextCursor` as opaque and pass it back only with the same resource, direction, and relationship filters.

`getIngestionFreshness` returns an `IngestionFreshnessReport` for one source in the credential-derived tenant. It carries point-in-time objectives and stable violations without exposing provider cursor state or resource contents.

Version 0.3 adds collection ingestion, investigation, evidence, governed action, and plugin-session methods. The server derives tenant, actor, and roles after credential verification; the SDK does not send caller-controlled identity headers.

Version 0.4 adds opaque provider cursor resume fields, ingestion-freshness telemetry, backend-neutral telemetry evidence, the stored `OtlpMetricsEvidence` artifact type, and `InvestigationTelemetrySelection`. Investigation candidates reuse the public metric query and limit types while identity, resources, time range, and deadline remain inherited server-side. OTLP transport is intentionally not reimplemented by this client; use a standard OpenTelemetry SDK/Collector and the separately provisioned channel credential.

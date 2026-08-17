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

`InvestigationSignalCatalog` describes protected tenant profile documents for configuration tooling. `InvestigationRequest.catalogSnapshot` is readonly server-owned provenance on accepted work; clients must omit it when submitting an investigation. Report signal-plan steps distinguish `request` from `protected-catalog` origin without returning protected query configuration.

`EvaluationScenario` describes offline graph, timeline, alert, evidence, request, and scoring fixtures. It is an evaluation artifact, not an API method or authority grant.

Resource observer integrations use the exported `ResourceCollectionRequest`, `ResourceCollectionResume`, and `ResourceCollectionResult` types. Resume state combines the last committed aggregate checkpoint with an opaque provider cursor map. A complete reconciliation response may append host-generated deleted `ResourceObservation` items after the plugin observations. The types describe capability payloads only and expose no server implementation classes.

`getResourceNeighborhood` and `getResourceTimeline` expose the paginated read models. Treat `nextCursor` as opaque and pass it back only with the same resource, direction, and relationship filters.

`getIngestionFreshness` returns an `IngestionFreshnessReport` for one source in the credential-derived tenant. It carries point-in-time objectives and stable violations without exposing provider cursor state or resource contents.

`getRuntimeVersion` returns the authenticated non-secret application, contract, required storage migration, build revision, Helm chart, and immutable image identity reported by the answering process. Optional deployment evidence is omitted rather than guessed from a mutable tag.

`getEventDeliveryHealth` returns the authenticated tenant's bounded outbox backlog and quarantine summary to a platform administrator. Event payloads, provider responses, credentials, and destination configuration are deliberately absent.

`getEventDeliverySlo` returns the deployment-configured rolling publication objective with mature-cohort aggregate counts and integer-basis-point attainment. It measures publisher acknowledgement, not downstream receiver processing.

`getInvestigationCompletionSlo` returns the deployment-configured rolling useful-completion objective for durable asynchronous jobs. It requires platform-administrator authority and contains aggregate outcomes only—never investigation identity, request, evidence, finding, worker, or provider data.

`EventDeliveryReplayParameters` and `proposeEventDeliveryReplay` bind one exact quarantine generation into the normal investigation, independent approval, policy, audit, and one-shot execution workflow. Dry-run is the default; live replay preserves the CloudEvents ID so receivers must remain idempotent.

`PluginInvocationStatus`, `PluginInvocationCancellationRequest`, and `PluginInvocationReconciliationRequest` model durable execution state. `getPluginInvocationStatus`, `cancelPluginInvocation`, and `reconcilePluginInvocation` use the authenticated control-plane lifecycle; reconciliation is administrator-only, post-deadline, and never replays the invocation.

`PluginMediationGrant`, `PluginMediationRequest`, and `PluginMediationResponse`
describe the private invocation-local runner protocol without exposing server
internals, provider endpoints, credential references, or secrets. The
TypeScript package intentionally provides types only because Unix-domain socket
transport is runtime-specific.

Version 0.3 adds collection ingestion, investigation, evidence, governed action, and plugin-session methods. The server derives tenant, actor, and roles after credential verification; the SDK does not send caller-controlled identity headers.

Version 0.4 adds opaque provider cursor resume fields, ingestion-freshness telemetry, backend-neutral telemetry evidence, the stored `OtlpMetricsEvidence` artifact type, and `InvestigationTelemetrySelection`. Investigation candidates reuse the public metric query and limit types while identity, resources, time range, and deadline remain inherited server-side. OTLP transport is intentionally not reimplemented by this client; use a standard OpenTelemetry SDK/Collector and the separately provisioned channel credential.

Version 0.5 adds `InvestigationTelemetryInterpretation` and `InvestigationTelemetryAssessment`. Threshold rules are root-cause scoped, unit-aware, and evaluated server-side against committed normalized Evidence.

Version 0.6 adds `InvestigationTelemetryBaselineComparison` and `InvestigationTelemetryBaselineAssessment`. Ordered baseline and evaluation subranges are evaluated from one committed normalized artifact as a difference or ratio, without adding a backend query.

Version 0.7 adds `KubernetesEventEvidenceRequest`, `KubernetesEventEvidenceResult`, investigation event selection/assessment types, and `collectKubernetesEventEvidence`. These types contain normalized scope and facts only—never kubeconfig, credentials, endpoints, or Kubernetes client objects.

Version 0.33 adds plugin invocation status, cancellation, and reconciliation types and client methods.

Version 0.34 adds invocation-scoped plugin mediation grant, request, and response types.

Version 0.35 adds `PluginCompatibilityReport` for the offline exact-host
conformance artifact. The type does not widen plugin authority or imply support
for untested architectures or provider integrations.

Version 0.36 adds proposal-only plugin action mediation grant, request, and
response types. Approval and execution remain separate control-plane contracts.

Version 0.37 adds the action-provider compatibility profile and its closed
proposal-queue, no-approval, and no-execution check identifiers.

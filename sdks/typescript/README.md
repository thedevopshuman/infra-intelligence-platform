# TypeScript SDK boundary

Version 0.77 adds the protected customer pilot-readiness profile and minimized
design-partner-candidate report. It cross-binds existing release and customer
evidence but grants no publication, signing, installation, provider, load, or
promotion authority.

The SDK exposes public contract types and a small fetch-based client. It has no dependency on server code or a framework runtime.

`getTelemetryExportHealth()` reads the answering API process; `getTelemetryDeploymentExportHealth()` returns bounded recent pseudonymous API and workflow-worker heartbeats. Both stay backend-neutral and exclude endpoint, credential, payload, provider-response, and raw workload identity data.

```ts
import { InfrastructureIntelligenceClient } from "@iip/sdk";

const client = new InfrastructureIntelligenceClient({
  baseUrl: "http://localhost:8080",
  bearerToken: token,
});
```

The package exports `Evidence`, `EvidenceRedactionPolicy`, `InvestigationRequest`, and `InvestigationReport` as transport-independent `v1alpha1` contract types and provides executable investigation/evidence client methods. The policy type is for protected configuration tooling; public callers cannot install or select tenant policies.

`InvestigationSignalCatalog` describes protected tenant profile documents for configuration tooling. `InvestigationRequest.catalogSnapshot` is readonly server-owned provenance on accepted work; clients must omit it when submitting an investigation. `InvestigationSignalPlan` and `InvestigationSignalPromotion` type the fixed plan and its optional one-candidate adaptive transition without returning protected query configuration or provider details.

`EvaluationScenario` describes offline graph, timeline, alert, evidence, request, and scoring fixtures. It is an evaluation artifact, not an API method or authority grant.

Resource observer integrations use the exported `ResourceCollectionRequest`, `ResourceCollectionResume`, and `ResourceCollectionResult` types. Resume state combines the last committed aggregate checkpoint with an opaque provider cursor map. A complete reconciliation response may append host-generated deleted `ResourceObservation` items after the plugin observations. The types describe capability payloads only and expose no server implementation classes.

`getResourceNeighborhood` and `getResourceTimeline` expose the paginated read models. Treat `nextCursor` as opaque and pass it back only with the same resource, direction, and relationship filters.

`getIngestionFreshness` returns an `IngestionFreshnessReport` for one source in the credential-derived tenant. It carries point-in-time objectives and stable violations without exposing provider cursor state or resource contents.

`getRuntimeVersion` returns the authenticated non-secret application, contract, required storage migration, build revision, Helm chart, and immutable image identity reported by the answering process. Optional deployment evidence is omitted rather than guessed from a mutable tag.

`getTelemetryExportHealth` reads process-local exporter outcomes, `getTelemetryDeploymentExportHealth` reads recent pseudonymous API/worker health, and `getTelemetryExportSlo` reads rolling sampled metrics/traces export-attempt attainment. These operations never expose endpoints, headers, credentials, payloads, provider responses, or raw workload identity.

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

`AiUsageRecord`, `AiAttributionPolicy`, `AiUsageAttributionRecord`,
`AiPriceCatalog`, `AwsBedrockPriceCatalogImportPolicy`,
`AiPriceCatalogImportReport`, `AiPriceCatalogQualificationPolicy`,
`AiPriceCatalogQualificationReport`, `AiCostRecord`, `AiSavingsFinding`,
`AiSavingsFindingPage`, `AiModelSuitabilityReport`, `AiAllocationReport`,
`AiEconomicsInvocationObservationRequest`,
`AiEconomicsInvocationObservation`,
`CustomerAiFinopsPrerequisiteProfile`,
`CustomerAiFinopsPrerequisiteReport`,
`CustomerAiFinopsFlowQualificationProfile`,
`CustomerAiFinopsFlowQualificationReport`,
`CustomerPilotReadinessProfile`, and `CustomerPilotReadinessReport`
describe the
metadata-only AI economics ledger. The SDK does not instrument or
proxy model calls; applications continue using standard OpenTelemetry
instrumentation and a customer-controlled Collector.

Version 0.77 adds the customer pilot preflight types. A candidate report binds
one published and organizationally signed release to current local readiness,
customer deployment, post-deployment load, AI prerequisites, and an exact
same-invocation flow while leaving actual partner acceptance and public
governance external.

Version 0.76 adds the customer flow profile/report types. A qualified report
binds one live Bedrock invocation to the exact usage, active attribution,
active pricing, Prometheus aggregate, and provisioned Grafana dashboard while
retaining only digests and aggregate measurements.

Version 0.75 adds `observeAiEconomicsInvocation`. It is a privileged
qualification read, not a trace-search or billing interface. Version 0.74
added the customer AI FinOps prerequisite profile/report. A ready
report means six independently generated artifacts are current and
cross-bound; its fixed limitations explicitly exclude same-invocation live
flow, invoice, and regional-availability claims.

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

Version 0.40 adds `InvestigationSignalPlan` and `InvestigationSignalPromotion` for one bounded promotion of an accepted candidate after a provider gap. The transition carries no query body or authority.

Version 0.46 adds vendor-neutral AI usage, price catalog, calculated-cost, and
evidence-backed savings record types without adding a provider or
instrumentation dependency.

Version 0.56 adds protected price-catalog qualification policy and minimized
report types without a provider pricing client, promotion method, or negotiated
rate exposure.

Version 0.62 adds `AiSavingsFindingPage` and `listAiSavingsFindings()` for
newest-first, tenant- and interval-bound access to committed advisory findings.
Evidence reads and action authority remain separate.

Version 0.61 adds `ControlPlaneLoadQualificationReport` and its closed check,
objective, environment, aggregate measurement, and limitation types. It adds
no client method, credential, traffic generator, or production authority.

Version 0.60 adds `CustomerDeploymentQualificationReport` and the closed
evidence/check/limitation types for one exact customer-cluster chain. It adds
no API method, credential, mutation, publication, or production authority.

Version 0.63 adds `CustomerProcessingQualificationProfile` and
`CustomerProcessingContinuityQualificationReport`, then extends the customer
deployment report with the same-release worker/receiver processing evidence
and post-disruption health ordering. The types add no client method,
credential handling, or Kubernetes mutation authority.

Version 0.59 adds `CustomerContinuityQualificationReport` and its closed check,
objective, environment, aggregate probe, and replacement types. It adds no
client method, credential, or Kubernetes mutation authority.

Version 0.58 adds the protected `EvidenceRedactionPolicy` type and optional
Evidence policy provenance without a policy-installation API, arbitrary
detector expressions, or a credential-redaction disable switch.

Version 0.47 adds protected attribution-policy and immutable usage-attribution
record types without exposing policy installation or workload-controlled
ownership authority.

Version 0.48 adds `getAiAllocationReport()` for a bounded UTC interval grouped
by protected application or team. Authentication determines the tenant and
deployment configuration determines the policy and price generations.

Version 0.49 adds the offline `ReleaseQualificationReport` and its closed
install/upgrade check identifiers. This artifact type does not add a client
method, credential, or production-environment claim.

Version 0.50 adds the offline `PostgreSQLRecoveryQualificationReport` and its
closed logical-restore check identifiers. It adds no database credential,
tenant data, API method, or HA/PITR claim.

Version 0.51 adds the offline `PostgreSQLContinuityQualificationReport`, its
closed physical-continuity checks, and minimized integrity types. It adds no
database credential, tenant data, API method, automatic-failover behavior, or
customer RPO/RTO claim.

Version 0.52 adds the offline `CustomerDeploymentPreflightReport`, its closed
core and AI FinOps checks, and minimized prerequisite summary types. It adds no
cluster credential, deployment mutation, or customer production claim.

Version 0.53 adds the hard topology-spread check and the previously omitted
vulnerability-qualified-release requirement to the closed preflight unions.
It does not turn Kubernetes placement configuration into live availability
evidence.

Version 0.54 adds the offline
`KubernetesAvailabilityQualificationReport`, component/phase state, and closed
check identifiers. It adds no API method, Kubernetes credential, mutation
authority, or production availability claim.

The current `local-multi-node-kind-v2` shape adds closed receiver durable-intake
semantics and aggregate workflow completion measurements for baseline,
drained-node, and recovered states. It still carries no cluster or workflow
identifier and grants no runtime authority.

Version 0.55 adds `AiModelSuitabilityReport` plus the qualified-model saving
calculation and evidence-reference types. These types cannot attest gate
results, install protected profiles, switch models, or grant action authority.

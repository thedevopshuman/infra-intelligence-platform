# Python SDK boundary

Version 0.76 adds the protected customer AI FinOps flow profile and minimized
qualification report. The temporary run evidence that contains trace/span
identity is intentionally excluded from the SDK.

This package contains public transport and model types only. It does not import the server kernel. The API will expand contract-first; unstable methods remain under the `v1alpha1` namespace in their paths and models.

```python
from infra_intelligence_sdk import Client, ResourceObservation

client = Client("http://localhost:8080", bearer_token=token)
accepted = client.ingest_resource(ResourceObservation.from_dict(payload))
```

`Evidence`, `EvidenceRedactionPolicy`, `InvestigationRequest`, and `InvestigationReport` expose the corresponding `v1alpha1` public envelopes without importing server implementation classes. The redaction policy type supports protected configuration tooling only; there is no API that lets a caller install or select a tenant policy. `run_investigation`, `get_investigation`, and `get_evidence` use the executable reference API.

`InvestigationSignalCatalog` models protected tenant profile documents for configuration tooling. `InvestigationRequest.catalog_snapshot` reads server-owned profile and resolved-subset provenance from an accepted request; clients must not populate that field when submitting work. Report `signalPlan` entries identify request versus protected-catalog origin without exposing protected query configuration. `InvestigationReport.signal_promotions` returns typed `InvestigationSignalPromotion` provenance when one accepted candidate was promoted after a provider gap; fixed plans return an empty tuple.

`InvestigationRequest.telemetry_selections` returns typed `InvestigationTelemetrySelection` candidates. Each contains only a provider-neutral query, output limits, and an optional closed `InvestigationTelemetryInterpretation`; authenticated identity, resources, time range, and deadline are inherited by the server from the investigation. `InvestigationReport.telemetry_assessments` exposes the applied unit-aware rule and committed Evidence citation without importing server internals.

`KubernetesEventEvidenceRequest` and `KubernetesEventEvidenceResult` expose the normalized customer-cluster event boundary, which is distinct from internal platform CloudEvents. `InvestigationRequest.kubernetes_event_selections` and `InvestigationReport.kubernetes_event_assessments` expose condition-count correlation and committed Evidence citations. The SDK never carries kubeconfig, cluster credentials, or Kubernetes client types.

`EvaluationScenario` exposes the offline, replayable scenario envelope used by evaluation tooling. It carries fixtures and a scoring oracle but grants no runtime access or authority.

`ResourceObservationCursor` represents optional source ordering, replay checkpoint, and reconciliation metadata carried inside a resource observation.

`ResourceCollectionRequest` and `ResourceCollectionResult` expose the bounded public interface used by resource-observer plugins. `request.resume` carries a host-committed aggregate checkpoint and opaque provider cursor map; `result.provider_cursors` reads the candidate cursor map from a complete result. A complete reconciliation response from `ingest_resource_collection` may include host-generated deleted Resource observations after the plugin observations. These lightweight envelope types do not import the server kernel.

`Client.get_resource_neighborhood` and `Client.get_resource_timeline` return the corresponding paginated public envelopes. Pass `spec.page.nextCursor` back unchanged to continue; the cursor is query- and tenant-bound and does not grant authority.

`Client.get_ingestion_freshness` returns an `IngestionFreshnessReport` for one source in the credential-derived tenant. The response exposes applied objectives and stable violations, not provider cursors or resource contents.

`Client.get_runtime_version` returns the authenticated non-secret application, contract, required storage migration, build revision, Helm chart, and immutable image identity reported by the answering process. Optional deployment evidence is absent when it was not supplied rather than inferred from a mutable tag.

`Client.get_event_delivery_health` gives a platform administrator the credential-tenant's bounded outbox backlog and quarantine summary. It exposes CloudEvents identity metadata and stable failure codes, never event payloads, provider responses, or destination configuration.

`Client.get_telemetry_export_health` reads the answering API process. `Client.get_telemetry_deployment_export_health` reads bounded recent pseudonymous API and workflow-worker heartbeats from the shared store. Neither model exposes OTLP endpoints, headers, credentials, payloads, provider responses, or raw workload identity.

`Client.get_telemetry_export_slo` returns the rolling deployment-wide successful-export-attempt objective from bounded counter samples. Metrics and traces are assessed independently with explicit no-data and minimum-cohort states.

`Client.get_event_delivery_slo` returns the deployment-configured rolling publication objective. It contains mature-cohort aggregate counts and integer-basis-point attainment only; it does not claim downstream receiver processing.

`Client.get_investigation_completion_slo` returns the deployment-configured rolling useful-completion objective for durable asynchronous jobs. It requires platform-administrator authority and contains aggregate outcomes only—never investigation identity, request, evidence, finding, worker, or provider data.

`EventDeliveryReplayCommand` and `Client.propose_event_delivery_replay` bind one exact quarantine generation into the normal investigation, independent approval, policy, audit, and one-shot execution workflow. Dry-run is the default; live replay preserves the CloudEvents ID so receivers must remain idempotent.

`PluginInvocationStatus`, `PluginInvocationCancellationRequest`, and `PluginInvocationReconciliationRequest` expose durable plugin execution state without importing runner or storage internals. The client can read status, request cooperative cancellation, and submit administrator-only post-deadline reconciliation. Reconciliation records an unknown outcome and never replays plugin work.

`PluginMediationGrant`, `PluginMediationRequest`, and `PluginMediationResponse`
describe the invocation-local read boundary. `PluginMediationClient` speaks the
bounded Unix-socket protocol from an isolated plugin without importing server
code, selecting a destination, or receiving a credential. It returns stable
host-created failures for plugin handling; it is not a control-plane HTTP client.

`AiUsageRecord`, `AiAttributionPolicy`, `AiUsageAttributionRecord`,
`AiPriceCatalog`, `AwsBedrockPriceCatalogImportPolicy`,
`AiPriceCatalogImportReport`, `AiPriceCatalogQualificationPolicy`,
`AiPriceCatalogQualificationReport`, `AiCostRecord`, `AiSavingsFinding`,
`AiSavingsFindingPage`, `AiAllocationReport`,
`AiEconomicsInvocationObservationRequest`,
`AiEconomicsInvocationObservation`,
`CustomerAiFinopsPrerequisiteProfile`, and
`CustomerAiFinopsPrerequisiteReport`,
`CustomerAiFinopsFlowQualificationProfile`, and
`CustomerAiFinopsFlowQualificationReport`
expose the AI economics
records as public metadata-only envelopes. They do not
instrument provider calls, send OTLP, or import an AWS SDK. Standard
OpenTelemetry instrumentation remains the collection boundary.

Version 0.76 adds the customer flow profile/report envelopes. A qualified
report binds one live Bedrock invocation to the exact usage, active
attribution, active pricing, Prometheus aggregate, and provisioned Grafana
dashboard while retaining only digests and aggregate measurements.

Version 0.75 adds `observe_ai_economics_invocation`. It is a privileged
qualification read, not a trace-search or billing interface. Version 0.74
added the customer AI FinOps prerequisite profile/report. A ready
report means six independently generated artifacts are current and
cross-bound; its fixed limitations explicitly exclude same-invocation live
flow, invoice, and regional-availability claims.

Version 0.3 adds collection ingestion, investigation, evidence, governed action, and plugin-session methods. Supply a Bearer credential; the authenticated server derives tenant, actor, and roles from that credential. The SDK never sends identity assertion headers.

Version 0.4 adds opaque provider cursor resume fields, ingestion-freshness telemetry, backend-neutral telemetry evidence, and the stored `OtlpMetricsEvidence` artifact model. OTLP transport is intentionally not reimplemented by this client; send metrics with a standard OpenTelemetry SDK/Collector and the separately provisioned channel credential.

Version 0.5 adds investigation telemetry interpretation and assessment types. Threshold rules are root-cause scoped, unit-aware, and evaluated server-side against committed normalized Evidence.

Version 0.6 adds baseline-window comparison request and report types. Ordered baseline and evaluation subranges are evaluated from one committed normalized artifact as a difference or ratio, without adding a backend query.

Version 0.7 adds Kubernetes Event evidence request/result, investigation selection/assessment models, and `collect_kubernetes_event_evidence`. The default server backend returns honest no-data until a live adapter is configured.

Version 0.33 adds plugin invocation status, cancellation, and reconciliation models and client methods.

Version 0.34 adds plugin mediation grant/request/response models and the bounded
Unix-socket client used by isolated Python plugins.

Version 0.35 adds `PluginCompatibilityReport`, the machine-readable exact-host
evidence emitted by the executable Docker matrix. It is not a universal support
claim for architectures or provider integrations that were not exercised.

Version 0.36 adds proposal-only plugin action mediation grant/request/response
models and `PluginMediationClient.propose_action`. The method cannot approve or
execute the returned proposal.

Version 0.37 extends `PluginCompatibilityReport` with the separately signed
action-provider profile and closed checks proving the host stored only a pending
governed proposal. It remains exact-host evidence, not a customer support claim.

Version 0.40 adds typed bounded adaptive investigation promotions. The SDK exposes only signal and selection IDs, stable provider-gap outcomes, and aggregate remaining capacity; it never exposes provider details or grants a new query.

Version 0.46 adds the vendor-neutral AI usage, price catalog, calculated-cost,
and evidence-backed savings record models. It intentionally adds no inference
instrumentation SDK.

Version 0.56 adds protected AI price-catalog qualification policy and minimized
report envelopes. These types add no provider pricing client, promotion API, or
access to negotiated rates.

Version 0.62 adds `AiSavingsFindingPage` and
`Client.list_ai_savings_findings()` for newest-first, tenant- and
interval-bound access to committed advisory findings. Evidence dereferencing
and action authority remain separate.

Version 0.61 adds `ControlPlaneLoadQualificationReport` for aggregate,
source-bound fixed-rate identity-read evidence. It adds no traffic generator,
credential handling, control-plane method, or production-approval authority.

Version 0.60 adds `CustomerDeploymentQualificationReport` for the exact
customer-cluster preflight, installed-health, ingress, and continuity evidence
chain. It grants no cluster, publication, or production-approval authority.

Version 0.63 adds the protected `CustomerProcessingQualificationProfile` and
minimized `CustomerProcessingContinuityQualificationReport`, and upgrades the
customer deployment report to bind that worker/receiver evidence into the
same release, target, and post-disruption health chain. These offline types
carry no credential, API method, or Kubernetes mutation authority.

Version 0.59 adds `CustomerContinuityQualificationReport` for minimized
external-probe and API-pod-Eviction evidence. It adds no client method,
credential, or Kubernetes mutation authority; the separately enabled
repository harness remains the only owner of the disruption workflow.

Version 0.58 adds the protected `EvidenceRedactionPolicy` envelope and optional
Evidence policy provenance. It adds no policy installation method, arbitrary
detector expression, or ability to disable mandatory credential redaction.

Version 0.47 adds protected attribution-policy and immutable usage-attribution
record models. It does not let clients install policies or self-assign
application/team ownership.

Version 0.48 adds the bounded `Client.get_ai_allocation_report` read and its
generation-bound application/team report model. Tenant identity and policy/
catalog selection remain server-owned.

Version 0.49 adds `ReleaseQualificationReport` for offline promotion tooling.
It uses the `iip.dev/v1alpha1` artifact namespace and grants no API access or
runtime authority; the repository verifier remains responsible for its closed
checks, derived totals, and release-manifest binding.

Version 0.50 adds `PostgreSQLRecoveryQualificationReport` for offline recovery
evidence tooling. It carries aggregate, source-bound logical restore facts only;
the executable repository verifier remains authoritative for derived checks and
clean-current-source qualification.

Version 0.51 adds `PostgreSQLContinuityQualificationReport` for offline
physical-continuity evidence tooling. It exposes only minimized streaming,
promotion, row-integrity, safe-sequence, named-target recovery, and timing facts;
it grants no database access and does not turn a local profile into a production
HA or RPO/RTO claim.

Version 0.52 adds `CustomerDeploymentPreflightReport` for minimized offline
deployment evidence tooling. It carries closed configuration and prerequisite
outcomes, counts, and digests only; it grants no cluster access and does not
turn a pre-install pass into customer production certification.

Version 0.53 aligns the preflight model with component disruption budgets,
hard topology spread, and vulnerability-qualified release evidence. The model
remains an opaque validated envelope and grants no Kubernetes authority.

Version 0.54 adds `KubernetesAvailabilityQualificationReport` for minimized,
source-bound planned worker-drain evidence. It adds no API method, cluster
credential, mutation authority, or customer availability claim.

The current `local-multi-node-kind-v2` report additionally exposes minimized
receiver commit and worker completion evidence for baseline, drained-node, and
recovered states. The Python model remains an offline envelope and grants no
cluster or workflow authority.

Version 0.55 adds `AiModelSuitabilityReport`, the protected immutable input
required before an expensive-model saving can be calculated. The SDK exposes
the validated envelope only; it cannot attest gate results, install profiles,
switch models, or grant action authority.

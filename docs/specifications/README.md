# Contract specifications

**Status:** v1alpha1 foundation

The platform exposes contracts at three levels:

1. Human semantics in these specification pages.
2. Machine validation under `contracts/schemas/`.
3. Transport bindings under `api/` and the SDKs.

The authenticated control plane and the independently authenticated OTLP intake listener have separate OpenAPI documents: [`control-plane.openapi.json`](../../api/openapi/control-plane.openapi.json) and [`otlp-receiver.openapi.json`](../../api/openapi/otlp-receiver.openapi.json). A deployment must not co-locate those route sets unless it deliberately enables the documented development compatibility mode.

| Contract | Documentation | Schema | Example |
| --- | --- | --- | --- |
| Resource | [resource-contract.md](resource-contract.md) | `contracts/schemas/resource.schema.json` | `contracts/examples/resource.json`, `contracts/examples/resource-tombstone.json` |
| Resource collection request | [resource-collection-contract.md](resource-collection-contract.md) | `contracts/schemas/resource-collection-request.schema.json` | `contracts/examples/resource-collection-request.json` |
| Resource collection result | [resource-collection-contract.md](resource-collection-contract.md) | `contracts/schemas/resource-collection-result.schema.json` | `contracts/examples/resource-collection-result.json` |
| Resource neighborhood | [resource-query-contract.md](resource-query-contract.md) | `contracts/schemas/resource-neighborhood.schema.json` | `contracts/examples/resource-neighborhood.json` |
| Resource timeline | [resource-query-contract.md](resource-query-contract.md) | `contracts/schemas/resource-timeline.schema.json` | `contracts/examples/resource-timeline.json` |
| Resource change evidence request | [resource-change-evidence-contract.md](resource-change-evidence-contract.md) | `contracts/schemas/resource-change-evidence-request.schema.json` | `contracts/examples/resource-change-evidence-request.json` |
| Resource change evidence result | [resource-change-evidence-contract.md](resource-change-evidence-contract.md) | `contracts/schemas/resource-change-evidence-result.schema.json` | `contracts/examples/resource-change-evidence-result.json` |
| Repository/runbook context request | [context-evidence-contract.md](context-evidence-contract.md) | `contracts/schemas/context-evidence-request.schema.json` | `contracts/examples/context-evidence-request.json` |
| Repository/runbook context result | [context-evidence-contract.md](context-evidence-contract.md) | `contracts/schemas/context-evidence-result.schema.json` | `contracts/examples/context-evidence-result.json` |
| Ingestion freshness report | [ingestion-freshness-contract.md](ingestion-freshness-contract.md) | `contracts/schemas/ingestion-freshness-report.schema.json` | `contracts/examples/ingestion-freshness-report.json` |
| Telemetry export health report | [telemetry-export-health-contract.md](telemetry-export-health-contract.md) | `contracts/schemas/telemetry-export-health-report.schema.json` | `contracts/examples/telemetry-export-health-report.json` |
| Deployment telemetry export health report | [telemetry-deployment-export-health-contract.md](telemetry-deployment-export-health-contract.md) | `contracts/schemas/telemetry-deployment-export-health-report.schema.json` | `contracts/examples/telemetry-deployment-export-health-report.json` |
| Telemetry export SLO report | [telemetry-export-slo-contract.md](telemetry-export-slo-contract.md) | `contracts/schemas/telemetry-export-slo-report.schema.json` | `contracts/examples/telemetry-export-slo-report.json` |
| Telemetry export burn-rate report | [telemetry-export-burn-rate-contract.md](telemetry-export-burn-rate-contract.md) | `contracts/schemas/telemetry-export-burn-rate-report.schema.json` | `contracts/examples/telemetry-export-burn-rate-report.json` |
| Event delivery health report | [event-delivery-health-contract.md](event-delivery-health-contract.md) | `contracts/schemas/event-delivery-health-report.schema.json` | `contracts/examples/event-delivery-health-report.json` |
| Event delivery SLO report | [event-delivery-slo-contract.md](event-delivery-slo-contract.md) | `contracts/schemas/event-delivery-slo-report.schema.json` | `contracts/examples/event-delivery-slo-report.json` |
| Investigation completion SLO report | [investigation-completion-slo-contract.md](investigation-completion-slo-contract.md) | `contracts/schemas/investigation-completion-slo-report.schema.json` | `contracts/examples/investigation-completion-slo-report.json` |
| Investigation dispatch capacity report | [investigation-capacity-contract.md](investigation-capacity-contract.md) | `contracts/schemas/investigation-capacity-report.schema.json` | `contracts/examples/investigation-capacity-report.json` |
| PostgreSQL recovery qualification report | [postgresql-recovery-qualification-contract.md](postgresql-recovery-qualification-contract.md) | `contracts/schemas/postgresql-recovery-qualification-report.schema.json` | `contracts/examples/postgresql-recovery-qualification-report.json` |
| Credential broker compatibility report | [credential-broker-compatibility-contract.md](credential-broker-compatibility-contract.md) | `contracts/schemas/credential-broker-compatibility-report.schema.json` | `contracts/examples/credential-broker-compatibility-report.json` |
| OIDC issuer compatibility report | [oidc-issuer-compatibility-contract.md](oidc-issuer-compatibility-contract.md) | `contracts/schemas/oidc-issuer-compatibility-report.schema.json` | `contracts/examples/oidc-issuer-compatibility-report.json` |
| Policy engine compatibility report | [policy-engine-compatibility-contract.md](policy-engine-compatibility-contract.md) | `contracts/schemas/policy-engine-compatibility-report.schema.json` | `contracts/examples/policy-engine-compatibility-report.json` |
| OTLP receiver compatibility report | [otlp-receiver-compatibility-contract.md](otlp-receiver-compatibility-contract.md) | `contracts/schemas/otlp-receiver-compatibility-report.schema.json` | `contracts/examples/otlp-receiver-compatibility-report.json` |
| Bedrock instrumentation compatibility report | [bedrock-instrumentation-compatibility-contract.md](bedrock-instrumentation-compatibility-contract.md) | `contracts/schemas/bedrock-instrumentation-compatibility-report.schema.json` | `contracts/examples/bedrock-instrumentation-compatibility-report.json` |
| OpenAI instrumentation compatibility report | [openai-instrumentation-compatibility-contract.md](openai-instrumentation-compatibility-contract.md) | `contracts/schemas/openai-instrumentation-compatibility-report.schema.json` | `contracts/examples/openai-instrumentation-compatibility-report.json` |
| Evidence retention report | [evidence-retention-contract.md](evidence-retention-contract.md) | `contracts/schemas/evidence-retention-report.schema.json` | `contracts/examples/evidence-retention-report.json` |
| Query availability telemetry | [query-availability-telemetry-contract.md](query-availability-telemetry-contract.md) | OTLP Metrics protobuf semantic convention | n/a |
| OTLP receiver availability telemetry | [otlp-receiver-availability-telemetry-contract.md](otlp-receiver-availability-telemetry-contract.md) | OTLP Metrics protobuf semantic convention | n/a |
| Runtime version report | [runtime-version-contract.md](runtime-version-contract.md) | `contracts/schemas/runtime-version-report.schema.json` | `contracts/examples/runtime-version-report.json` |
| Session context | [session-context-contract.md](session-context-contract.md) | `contracts/schemas/session-context.schema.json` | `contracts/examples/session-context.json` |
| Console authentication | [console-authentication-contract.md](console-authentication-contract.md) | `contracts/schemas/console-authentication.schema.json` | `contracts/examples/console-authentication-local.json`, `contracts/examples/console-authentication-oidc.json` |
| Page information | [resource-query-contract.md](resource-query-contract.md) | `contracts/schemas/page-info.schema.json` | `contracts/examples/page-info.json` |
| Error | [resource-query-contract.md](resource-query-contract.md) | `contracts/schemas/error.schema.json` | `contracts/examples/error.json` |
| Event | [event-contract.md](event-contract.md) | `contracts/schemas/event.schema.json` | `contracts/examples/event.json`, `contracts/examples/ai-usage-recorded-event.json`, `contracts/examples/ai-usage-attributed-event.json`, `contracts/examples/ai-cost-calculated-event.json`, `contracts/examples/ai-savings-finding-event.json`, `contracts/examples/ai-retry-savings-finding-event.json` |
| Evidence | [evidence-contract.md](evidence-contract.md) | `contracts/schemas/evidence.schema.json` | `contracts/examples/evidence.json` |
| AI usage record | [ai-economics-contracts.md](ai-economics-contracts.md) | `contracts/schemas/ai-usage-record.schema.json` | `contracts/examples/ai-usage-record.json` |
| AI attribution policy | [ai-attribution-contracts.md](ai-attribution-contracts.md) | `contracts/schemas/ai-attribution-policy.schema.json` | `contracts/examples/ai-attribution-policy.json` |
| AI usage attribution record | [ai-attribution-contracts.md](ai-attribution-contracts.md) | `contracts/schemas/ai-usage-attribution-record.schema.json` | `contracts/examples/ai-usage-attribution-record.json` |
| AI allocation report | [ai-allocation-report-contract.md](ai-allocation-report-contract.md) | `contracts/schemas/ai-allocation-report.schema.json` | `contracts/examples/ai-allocation-report.json` |
| AI price catalog | [ai-economics-contracts.md](ai-economics-contracts.md) | `contracts/schemas/ai-price-catalog.schema.json` | `contracts/examples/ai-price-catalog.json` |
| AI cost record | [ai-economics-contracts.md](ai-economics-contracts.md) | `contracts/schemas/ai-cost-record.schema.json` | `contracts/examples/ai-cost-record.json` |
| AI savings finding | [ai-economics-contracts.md](ai-economics-contracts.md) | `contracts/schemas/ai-savings-finding.schema.json` | `contracts/examples/ai-savings-finding.json`, `contracts/examples/ai-retry-savings-finding.json` |
| AI economics telemetry | [ai-economics-telemetry-contract.md](ai-economics-telemetry-contract.md) | OTLP Metrics protobuf semantic convention | n/a |
| Kubernetes Event evidence request | [kubernetes-event-evidence-contract.md](kubernetes-event-evidence-contract.md) | `contracts/schemas/kubernetes-event-evidence-request.schema.json` | `contracts/examples/kubernetes-event-evidence-request.json` |
| Kubernetes Event evidence result | [kubernetes-event-evidence-contract.md](kubernetes-event-evidence-contract.md) | `contracts/schemas/kubernetes-event-evidence-result.schema.json` | `contracts/examples/kubernetes-event-evidence-result.json` |
| Telemetry evidence request | [telemetry-evidence-contract.md](telemetry-evidence-contract.md) | `contracts/schemas/telemetry-evidence-request.schema.json` | `contracts/examples/telemetry-evidence-request.json` |
| Telemetry evidence result | [telemetry-evidence-contract.md](telemetry-evidence-contract.md) | `contracts/schemas/telemetry-evidence-result.schema.json` | `contracts/examples/telemetry-evidence-result.json` |
| Log evidence request | [log-evidence-contract.md](log-evidence-contract.md) | `contracts/schemas/log-evidence-request.schema.json` | `contracts/examples/log-evidence-request.json` |
| Log evidence result | [log-evidence-contract.md](log-evidence-contract.md) | `contracts/schemas/log-evidence-result.schema.json` | `contracts/examples/log-evidence-result.json` |
| OTLP metrics evidence | [otlp-metrics-evidence-contract.md](otlp-metrics-evidence-contract.md) | `contracts/schemas/otlp-metrics-evidence.schema.json` | `contracts/examples/otlp-metrics-evidence.json` |
| OTLP logs evidence | [otlp-logs-evidence-contract.md](otlp-logs-evidence-contract.md) | `contracts/schemas/otlp-logs-evidence.schema.json` | `contracts/examples/otlp-logs-evidence.json` |
| Investigation request | [investigation-contract.md](investigation-contract.md) | `contracts/schemas/investigation-request.schema.json` | `contracts/examples/investigation-request.json`, `contracts/examples/investigation-request-adaptive-replan.json`, `contracts/examples/investigation-request-catalog-resolved.json`, `contracts/examples/investigation-request-changes.json`, `contracts/examples/investigation-request-context.json`, `contracts/examples/investigation-request-kubernetes-events.json`, `contracts/examples/investigation-request-logs.json`, `contracts/examples/investigation-request-telemetry.json`, `contracts/examples/investigation-request-telemetry-baseline.json`, `contracts/examples/investigation-request-telemetry-seasonal.json` |
| Investigation signal catalog | [investigation-signal-catalog-contract.md](investigation-signal-catalog-contract.md) | `contracts/schemas/investigation-signal-catalog.schema.json` | `contracts/examples/investigation-signal-catalog.json` |
| Investigation report | [investigation-contract.md](investigation-contract.md) | `contracts/schemas/investigation-report.schema.json` | `contracts/examples/investigation-report.json`, `contracts/examples/investigation-report-adaptive-replan.json`, `contracts/examples/investigation-report-changes.json`, `contracts/examples/investigation-report-context.json`, `contracts/examples/investigation-report-kubernetes-events.json`, `contracts/examples/investigation-report-logs.json`, `contracts/examples/investigation-report-telemetry.json`, `contracts/examples/investigation-report-telemetry-baseline.json`, `contracts/examples/investigation-report-telemetry-seasonal.json` |
| Investigation status | [investigation-lifecycle-contract.md](investigation-lifecycle-contract.md) | `contracts/schemas/investigation-status.schema.json` | `contracts/examples/investigation-status.json` |
| Investigation job status | [investigation-lifecycle-contract.md](investigation-lifecycle-contract.md) | `contracts/schemas/investigation-job-status.schema.json` | `contracts/examples/investigation-job-status.json` |
| Investigation cancellation request | [investigation-lifecycle-contract.md](investigation-lifecycle-contract.md) | `contracts/schemas/investigation-cancellation-request.schema.json` | `contracts/examples/investigation-cancellation-request.json` |
| Evaluation scenario | [evaluation-scenario-contract.md](evaluation-scenario-contract.md) | `contracts/schemas/evaluation-scenario.schema.json` | `contracts/examples/evaluation-scenario.json` |
| Agent manifest | [agent-contract.md](agent-contract.md) | `contracts/schemas/agent-manifest.schema.json` | `contracts/examples/agent-manifest.json` |
| Plugin manifest | [plugin-contract.md](plugin-contract.md) | `contracts/schemas/plugin-manifest.schema.json` | `contracts/examples/plugin-manifest.json` |
| Plugin invocation | [plugin-invocation-contract.md](plugin-invocation-contract.md) | `contracts/schemas/plugin-invocation.schema.json` | `contracts/examples/plugin-invocation.json`, `contracts/examples/plugin-invocation-mediated.json` |
| Plugin invocation result | [plugin-invocation-contract.md](plugin-invocation-contract.md) | `contracts/schemas/plugin-invocation-result.schema.json` | `contracts/examples/plugin-invocation-result.json`, `contracts/examples/plugin-invocation-result-failed.json` |
| Plugin invocation status | [plugin-invocation-lifecycle-contract.md](plugin-invocation-lifecycle-contract.md) | `contracts/schemas/plugin-invocation-status.schema.json` | `contracts/examples/plugin-invocation-status.json` |
| Plugin invocation cancellation request | [plugin-invocation-lifecycle-contract.md](plugin-invocation-lifecycle-contract.md) | `contracts/schemas/plugin-invocation-cancellation-request.schema.json` | `contracts/examples/plugin-invocation-cancellation-request.json` |
| Plugin invocation reconciliation request | [plugin-invocation-lifecycle-contract.md](plugin-invocation-lifecycle-contract.md) | `contracts/schemas/plugin-invocation-reconciliation-request.schema.json` | `contracts/examples/plugin-invocation-reconciliation-request.json` |
| Plugin mediation grant | [plugin-mediation-contract.md](plugin-mediation-contract.md) | `contracts/schemas/plugin-mediation-grant.schema.json` | `contracts/examples/plugin-mediation-grant.json` |
| Plugin mediation request | [plugin-mediation-contract.md](plugin-mediation-contract.md) | `contracts/schemas/plugin-mediation-request.schema.json` | `contracts/examples/plugin-mediation-request.json` |
| Plugin mediation response | [plugin-mediation-contract.md](plugin-mediation-contract.md) | `contracts/schemas/plugin-mediation-response.schema.json` | `contracts/examples/plugin-mediation-response.json` |
| Plugin action mediation grant | [plugin-action-mediation-contract.md](plugin-action-mediation-contract.md) | `contracts/schemas/plugin-action-mediation-grant.schema.json` | `contracts/examples/plugin-action-mediation-grant.json` |
| Plugin action mediation request | [plugin-action-mediation-contract.md](plugin-action-mediation-contract.md) | `contracts/schemas/plugin-action-mediation-request.schema.json` | `contracts/examples/plugin-action-mediation-request.json` |
| Plugin action mediation response | [plugin-action-mediation-contract.md](plugin-action-mediation-contract.md) | `contracts/schemas/plugin-action-mediation-response.schema.json` | `contracts/examples/plugin-action-mediation-response.json` |
| Plugin compatibility report | [plugin-compatibility-contract.md](plugin-compatibility-contract.md) | `contracts/schemas/plugin-compatibility-report.schema.json` | `contracts/examples/plugin-compatibility-report.json` |
| Integration configuration | [integration-config-contract.md](integration-config-contract.md) | `contracts/schemas/integration-config.schema.json` | `contracts/examples/integration-config.json` |
| Credential lease request | [credential-lease-contract.md](credential-lease-contract.md) | `contracts/schemas/credential-lease-request.schema.json` | `contracts/examples/credential-lease-request.json` |
| Credential lease | [credential-lease-contract.md](credential-lease-contract.md) | `contracts/schemas/credential-lease.schema.json` | `contracts/examples/credential-lease.json` |
| Action proposal | [action-contract.md](action-contract.md) | `contracts/schemas/action-proposal.schema.json` | `contracts/examples/action-proposal.json` |
| Action approval | [action-contract.md](action-contract.md) | `contracts/schemas/action-approval.schema.json` | `contracts/examples/action-approval.json` |
| Action execution status | [action-contract.md](action-contract.md) | `contracts/schemas/action-execution-status.schema.json` | `contracts/examples/action-execution-status.json` |
| Action result | [action-contract.md](action-contract.md) | `contracts/schemas/action-result.schema.json` | `contracts/examples/action-result.json` |
| Action workflow | [action-contract.md](action-contract.md) | `contracts/schemas/action-workflow.schema.json` | `contracts/examples/action-workflow.json`, `contracts/examples/action-workflow-expired.json` |
| Action workflow page | [action-contract.md](action-contract.md) | `contracts/schemas/action-workflow-page.schema.json` | `contracts/examples/action-workflow-page.json` |
| Plugin session | [plugin-session-contract.md](plugin-session-contract.md) | `contracts/schemas/plugin-session.schema.json` | `contracts/examples/plugin-session.json` |
| Policy decision request | [policy-contract.md](policy-contract.md) | `contracts/schemas/policy-decision-request.schema.json` | `contracts/examples/policy-decision-request.json` |
| Policy decision | [policy-contract.md](policy-contract.md) | `contracts/schemas/policy-decision.schema.json` | `contracts/examples/policy-decision.json` |
| Release manifest | [release-manifest-contract.md](release-manifest-contract.md) | `contracts/schemas/release-manifest.schema.json` | `contracts/examples/release-manifest.json` |
| Release qualification report | [release-qualification-report-contract.md](release-qualification-report-contract.md) | `contracts/schemas/release-qualification-report.schema.json` | `contracts/examples/release-qualification-report.json` |

`v1alpha1` means consumers should pin versions and expect deliberate evolution. Breaking changes create a new API/schema version. Fields are never silently repurposed.

Planned contracts: evaluation results and asynchronous AI economics exports.

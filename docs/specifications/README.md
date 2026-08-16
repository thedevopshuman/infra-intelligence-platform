# Contract specifications

**Status:** v1alpha1 foundation

The platform exposes contracts at three levels:

1. Human semantics in these specification pages.
2. Machine validation under `contracts/schemas/`.
3. Transport bindings under `api/` and the SDKs.

| Contract | Documentation | Schema | Example |
| --- | --- | --- | --- |
| Resource | [resource-contract.md](resource-contract.md) | `contracts/schemas/resource.schema.json` | `contracts/examples/resource.json`, `contracts/examples/resource-tombstone.json` |
| Resource collection request | [resource-collection-contract.md](resource-collection-contract.md) | `contracts/schemas/resource-collection-request.schema.json` | `contracts/examples/resource-collection-request.json` |
| Resource collection result | [resource-collection-contract.md](resource-collection-contract.md) | `contracts/schemas/resource-collection-result.schema.json` | `contracts/examples/resource-collection-result.json` |
| Resource neighborhood | [resource-query-contract.md](resource-query-contract.md) | `contracts/schemas/resource-neighborhood.schema.json` | `contracts/examples/resource-neighborhood.json` |
| Resource timeline | [resource-query-contract.md](resource-query-contract.md) | `contracts/schemas/resource-timeline.schema.json` | `contracts/examples/resource-timeline.json` |
| Resource change evidence request | [resource-change-evidence-contract.md](resource-change-evidence-contract.md) | `contracts/schemas/resource-change-evidence-request.schema.json` | `contracts/examples/resource-change-evidence-request.json` |
| Resource change evidence result | [resource-change-evidence-contract.md](resource-change-evidence-contract.md) | `contracts/schemas/resource-change-evidence-result.schema.json` | `contracts/examples/resource-change-evidence-result.json` |
| Ingestion freshness report | [ingestion-freshness-contract.md](ingestion-freshness-contract.md) | `contracts/schemas/ingestion-freshness-report.schema.json` | `contracts/examples/ingestion-freshness-report.json` |
| Session context | [session-context-contract.md](session-context-contract.md) | `contracts/schemas/session-context.schema.json` | `contracts/examples/session-context.json` |
| Page information | [resource-query-contract.md](resource-query-contract.md) | `contracts/schemas/page-info.schema.json` | `contracts/examples/page-info.json` |
| Error | [resource-query-contract.md](resource-query-contract.md) | `contracts/schemas/error.schema.json` | `contracts/examples/error.json` |
| Event | [event-contract.md](event-contract.md) | `contracts/schemas/event.schema.json` | `contracts/examples/event.json` |
| Evidence | [evidence-contract.md](evidence-contract.md) | `contracts/schemas/evidence.schema.json` | `contracts/examples/evidence.json` |
| Kubernetes Event evidence request | [kubernetes-event-evidence-contract.md](kubernetes-event-evidence-contract.md) | `contracts/schemas/kubernetes-event-evidence-request.schema.json` | `contracts/examples/kubernetes-event-evidence-request.json` |
| Kubernetes Event evidence result | [kubernetes-event-evidence-contract.md](kubernetes-event-evidence-contract.md) | `contracts/schemas/kubernetes-event-evidence-result.schema.json` | `contracts/examples/kubernetes-event-evidence-result.json` |
| Telemetry evidence request | [telemetry-evidence-contract.md](telemetry-evidence-contract.md) | `contracts/schemas/telemetry-evidence-request.schema.json` | `contracts/examples/telemetry-evidence-request.json` |
| Telemetry evidence result | [telemetry-evidence-contract.md](telemetry-evidence-contract.md) | `contracts/schemas/telemetry-evidence-result.schema.json` | `contracts/examples/telemetry-evidence-result.json` |
| Log evidence request | [log-evidence-contract.md](log-evidence-contract.md) | `contracts/schemas/log-evidence-request.schema.json` | `contracts/examples/log-evidence-request.json` |
| Log evidence result | [log-evidence-contract.md](log-evidence-contract.md) | `contracts/schemas/log-evidence-result.schema.json` | `contracts/examples/log-evidence-result.json` |
| OTLP metrics evidence | [otlp-metrics-evidence-contract.md](otlp-metrics-evidence-contract.md) | `contracts/schemas/otlp-metrics-evidence.schema.json` | `contracts/examples/otlp-metrics-evidence.json` |
| OTLP logs evidence | [otlp-logs-evidence-contract.md](otlp-logs-evidence-contract.md) | `contracts/schemas/otlp-logs-evidence.schema.json` | `contracts/examples/otlp-logs-evidence.json` |
| Investigation request | [investigation-contract.md](investigation-contract.md) | `contracts/schemas/investigation-request.schema.json` | `contracts/examples/investigation-request.json`, `contracts/examples/investigation-request-kubernetes-events.json`, `contracts/examples/investigation-request-logs.json`, `contracts/examples/investigation-request-telemetry.json`, `contracts/examples/investigation-request-telemetry-baseline.json` |
| Investigation report | [investigation-contract.md](investigation-contract.md) | `contracts/schemas/investigation-report.schema.json` | `contracts/examples/investigation-report.json`, `contracts/examples/investigation-report-kubernetes-events.json`, `contracts/examples/investigation-report-logs.json`, `contracts/examples/investigation-report-telemetry.json`, `contracts/examples/investigation-report-telemetry-baseline.json` |
| Evaluation scenario | [evaluation-scenario-contract.md](evaluation-scenario-contract.md) | `contracts/schemas/evaluation-scenario.schema.json` | `contracts/examples/evaluation-scenario.json` |
| Agent manifest | [agent-contract.md](agent-contract.md) | `contracts/schemas/agent-manifest.schema.json` | `contracts/examples/agent-manifest.json` |
| Plugin manifest | [plugin-contract.md](plugin-contract.md) | `contracts/schemas/plugin-manifest.schema.json` | `contracts/examples/plugin-manifest.json` |
| Integration configuration | [integration-config-contract.md](integration-config-contract.md) | `contracts/schemas/integration-config.schema.json` | `contracts/examples/integration-config.json` |
| Credential lease request | [credential-lease-contract.md](credential-lease-contract.md) | `contracts/schemas/credential-lease-request.schema.json` | `contracts/examples/credential-lease-request.json` |
| Credential lease | [credential-lease-contract.md](credential-lease-contract.md) | `contracts/schemas/credential-lease.schema.json` | `contracts/examples/credential-lease.json` |
| Action proposal | [action-contract.md](action-contract.md) | `contracts/schemas/action-proposal.schema.json` | `contracts/examples/action-proposal.json` |
| Action approval | [action-contract.md](action-contract.md) | `contracts/schemas/action-approval.schema.json` | `contracts/examples/action-approval.json` |
| Action result | [action-contract.md](action-contract.md) | `contracts/schemas/action-result.schema.json` | `contracts/examples/action-result.json` |
| Plugin session | [plugin-session-contract.md](plugin-session-contract.md) | `contracts/schemas/plugin-session.schema.json` | `contracts/examples/plugin-session.json` |

`v1alpha1` means consumers should pin versions and expect deliberate evolution. Breaking changes create a new API/schema version. Fields are never silently repurposed.

Planned contracts: standalone policy decisions, workflow history, and evaluation results.

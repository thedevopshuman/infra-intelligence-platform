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
| Ingestion freshness report | [ingestion-freshness-contract.md](ingestion-freshness-contract.md) | `contracts/schemas/ingestion-freshness-report.schema.json` | `contracts/examples/ingestion-freshness-report.json` |
| Page information | [resource-query-contract.md](resource-query-contract.md) | `contracts/schemas/page-info.schema.json` | `contracts/examples/page-info.json` |
| Error | [resource-query-contract.md](resource-query-contract.md) | `contracts/schemas/error.schema.json` | `contracts/examples/error.json` |
| Event | [event-contract.md](event-contract.md) | `contracts/schemas/event.schema.json` | `contracts/examples/event.json` |
| Evidence | [evidence-contract.md](evidence-contract.md) | `contracts/schemas/evidence.schema.json` | `contracts/examples/evidence.json` |
| Investigation request | [investigation-contract.md](investigation-contract.md) | `contracts/schemas/investigation-request.schema.json` | `contracts/examples/investigation-request.json` |
| Investigation report | [investigation-contract.md](investigation-contract.md) | `contracts/schemas/investigation-report.schema.json` | `contracts/examples/investigation-report.json` |
| Evaluation scenario | [evaluation-scenario-contract.md](evaluation-scenario-contract.md) | `contracts/schemas/evaluation-scenario.schema.json` | `contracts/examples/evaluation-scenario.json` |
| Agent manifest | [agent-contract.md](agent-contract.md) | `contracts/schemas/agent-manifest.schema.json` | `contracts/examples/agent-manifest.json` |
| Plugin manifest | [plugin-contract.md](plugin-contract.md) | `contracts/schemas/plugin-manifest.schema.json` | `contracts/examples/plugin-manifest.json` |
| Integration configuration | [integration-config-contract.md](integration-config-contract.md) | `contracts/schemas/integration-config.schema.json` | `contracts/examples/integration-config.json` |
| Action proposal | [action-contract.md](action-contract.md) | `contracts/schemas/action-proposal.schema.json` | `contracts/examples/action-proposal.json` |
| Action approval | [action-contract.md](action-contract.md) | `contracts/schemas/action-approval.schema.json` | `contracts/examples/action-approval.json` |
| Action result | [action-contract.md](action-contract.md) | `contracts/schemas/action-result.schema.json` | `contracts/examples/action-result.json` |
| Plugin session | [plugin-session-contract.md](plugin-session-contract.md) | `contracts/schemas/plugin-session.schema.json` | `contracts/examples/plugin-session.json` |

`v1alpha1` means consumers should pin versions and expect deliberate evolution. Breaking changes create a new API/schema version. Fields are never silently repurposed.

Planned contracts: standalone policy decisions, workflow history, evaluation results, and credential-broker grants.

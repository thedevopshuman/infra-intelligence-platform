# Contract specifications

**Status:** v1alpha1 foundation

The platform exposes contracts at three levels:

1. Human semantics in these specification pages.
2. Machine validation under `contracts/schemas/`.
3. Transport bindings under `api/` and the SDKs.

| Contract | Documentation | Schema | Example |
| --- | --- | --- | --- |
| Resource | [resource-contract.md](resource-contract.md) | `contracts/schemas/resource.schema.json` | `contracts/examples/resource.json` |
| Event | [event-contract.md](event-contract.md) | `contracts/schemas/event.schema.json` | `contracts/examples/event.json` |
| Evidence | [evidence-contract.md](evidence-contract.md) | `contracts/schemas/evidence.schema.json` | `contracts/examples/evidence.json` |
| Investigation request | [investigation-contract.md](investigation-contract.md) | `contracts/schemas/investigation-request.schema.json` | `contracts/examples/investigation-request.json` |
| Investigation report | [investigation-contract.md](investigation-contract.md) | `contracts/schemas/investigation-report.schema.json` | `contracts/examples/investigation-report.json` |
| Agent manifest | [agent-contract.md](agent-contract.md) | `contracts/schemas/agent-manifest.schema.json` | `contracts/examples/agent-manifest.json` |
| Plugin manifest | [plugin-contract.md](plugin-contract.md) | `contracts/schemas/plugin-manifest.schema.json` | `contracts/examples/plugin-manifest.json` |

`v1alpha1` means consumers should pin versions and expect deliberate evolution. Breaking changes create a new API/schema version. Fields are never silently repurposed.

Planned contracts: action proposal/result, policy decision, integration configuration, pagination, and error envelope.

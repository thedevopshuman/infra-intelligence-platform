# TypeScript SDK boundary

The SDK exposes public contract types and a small fetch-based client. It has no dependency on server code or a framework runtime.

```ts
import { InfrastructureIntelligenceClient } from "@iip/sdk";

const client = new InfrastructureIntelligenceClient({
  baseUrl: "http://localhost:8080",
  tenantId: "local",
  actorId: "developer",
});
```

The package also exports `Evidence`, `InvestigationRequest`, and `InvestigationReport` as transport-independent `v1alpha1` contract types. Client methods follow only when the corresponding API surface is executable.

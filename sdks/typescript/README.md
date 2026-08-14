# TypeScript SDK boundary

The SDK exposes public contract types and a small fetch-based client. It has no dependency on server code or a framework runtime.

```ts
import { InfrastructureIntelligenceClient } from "@iip/sdk";

const client = new InfrastructureIntelligenceClient({
  baseUrl: "http://localhost:8080",
  bearerToken: token,
});
```

The package also exports `Evidence`, `InvestigationRequest`, and `InvestigationReport` as transport-independent `v1alpha1` contract types. Client methods follow only when the corresponding API surface is executable.

`EvaluationScenario` describes offline graph, timeline, alert, evidence, request, and scoring fixtures. It is an evaluation artifact, not an API method or authority grant.

Resource observer integrations use the exported `ResourceCollectionRequest` and `ResourceCollectionResult` types. They describe capability payloads only; they do not expose server implementation classes or a not-yet-built plugin transport.

`getResourceNeighborhood` and `getResourceTimeline` expose the paginated read models. Treat `nextCursor` as opaque and pass it back only with the same resource, direction, and relationship filters.

Version 0.2 replaces the development-only `tenantId` and `actorId` options with `bearerToken`. The server derives tenant, actor, and roles after credential verification; the SDK does not send caller-controlled identity headers.

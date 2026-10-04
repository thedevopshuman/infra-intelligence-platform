import assert from "node:assert/strict";

import {
  InfrastructureIntelligenceClient,
  PlatformApiError,
} from "../dist/index.js";


const report = {
  apiVersion: "iip.platform/v1alpha1",
  kind: "AiHistoryAvailabilityReport",
  metadata: {
    tenantId: "local",
    generatedAt: "2026-10-04T12:00:00Z",
  },
  spec: {
    scope: {
      start: "2026-10-03T12:00:00Z",
      end: "2026-10-04T12:00:00Z",
    },
    status: "history-retired",
    coverage: {
      retainedUsageRecords: 7,
      retiredUsageRecords: 2,
    },
  },
};

const requestedUrls = [];
const client = new InfrastructureIntelligenceClient({
  baseUrl: "https://control.example/",
  bearerToken: "history-token-0123456789",
  fetch: async (input) => {
    requestedUrls.push(String(input));
    return new Response(JSON.stringify(report), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  },
});

const received = await client.getAiHistoryAvailability({
  start: "2026-10-03T12:00:00Z",
  end: "2026-10-04T12:00:00Z",
});
assert.deepEqual(received, report);
assert.deepEqual(requestedUrls, [
  "https://control.example/v1/ai/economics/history-availability?start=2026-10-03T12%3A00%3A00Z&end=2026-10-04T12%3A00%3A00Z",
]);

const retiredClient = new InfrastructureIntelligenceClient({
  baseUrl: "https://control.example",
  bearerToken: "history-token-0123456789",
  fetch: async () => new Response(
    JSON.stringify({ error: { code: "ai.history.retired" } }),
    {
      status: 410,
      headers: { "content-type": "application/json" },
    },
  ),
});

const retiredCalls = [
  () => retiredClient.getAiHistoryAvailability({
    start: "2026-10-03T12:00:00Z",
    end: "2026-10-04T12:00:00Z",
  }),
  () => retiredClient.getAiAllocationReport({
    start: "2026-10-03T12:00:00Z",
    end: "2026-10-04T12:00:00Z",
    groupBy: "application",
  }),
  () => retiredClient.observeAiEconomicsInvocation({
    apiVersion: "iip.platform/v1alpha1",
    kind: "AiEconomicsInvocationObservationRequest",
    spec: { traceId: "1".repeat(32), spanId: "2".repeat(16) },
  }),
];

for (const call of retiredCalls) {
  await assert.rejects(call, (error) => {
    assert.ok(error instanceof PlatformApiError);
    assert.equal(error.status, 410);
    assert.equal(error.code, "ai.history.retired");
    return true;
  });
}

console.log("TypeScript AI history SDK checks passed");

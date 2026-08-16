# Investigation lifecycle contracts

**Status:** v1alpha1

**Machine contracts:** `contracts/schemas/investigation-status.schema.json`, `contracts/schemas/investigation-cancellation-request.schema.json`

An accepted investigation is durable before its first evidence tool runs. `InvestigationStatus` exposes only bounded lifecycle metadata; it never contains the question, trigger, evidence, provider output, credentials, or model context. The authenticated tenant remains the lookup authority.

## Status

`InvestigationStatus` uses the investigation ID and exact canonical request digest. `startedAt` is immutable. A non-terminal status is either `running` or `cancellation-requested` and carries `leaseExpiresAt`. A terminal status is `completed`, `failed`, or `cancelled`, carries `completedAt` and an immutable `reportRef`, and no longer carries a lease.

The exact validated request envelope is stored unchanged. The execution lease is bounded by its accepted `maxWallTimeSeconds`; it is a crash-detection boundary, not permission to exceed the investigation deadline. A duplicate submission with the same ID and request returns the immutable report if terminal, returns `investigation.in_progress` while the lease is live, or closes an expired attempt with a failed `runtime-error` report. It never reruns tools under the same investigation ID. A retry that should gather evidence again uses a new ID.

`GET /v1/investigations/{investigationId}/status` returns the tenant-scoped status. `GET /v1/investigations/{investigationId}` continues to return only a terminal report, so existing report consumers never receive a changing document.

## Cancellation

`InvestigationCancellationRequest` identifies one investigation, the authenticated tenant and actor, a request time, and one closed reason code: `operator-requested`, `incident-resolved`, or `superseded`. Payload identity is asserted against authenticated context and never grants access. The path ID and body ID must match at the HTTP boundary.

`POST /v1/investigations/{investigationId}/cancel` atomically records the first request and returns the current status. Repeated requests return the first recorded cancellation or the terminal state; they do not rewrite audit intent. A running worker checks this durable state before each eligible evidence call and before terminalization. Already committed evidence remains immutable and may stay listed in the cancelled report, but cancellation removes hypotheses and recommendations so a partial run cannot be presented as a conclusion.

Cancellation is cooperative: an in-flight provider call must return or honor its existing deadline before the worker can stop. Cancellation does not delete the request, Evidence, status, ledger, or report.

## Validation invariants

- request, status, cancellation, and report share the authenticated tenant and investigation ID;
- status `requestDigest` pins the exact accepted request;
- `startedAt <= updatedAt <= completedAt` where the fields apply;
- only non-terminal states carry `leaseExpiresAt`;
- only cancellation states carry the immutable cancellation tuple;
- a terminal status resolves to exactly one immutable report; and
- cancellation or crash recovery never repeats evidence calls under the same ID.

# Investigation lifecycle contracts

**Status:** v1alpha1

**Machine contracts:** `contracts/schemas/investigation-status.schema.json`, `contracts/schemas/investigation-job-status.schema.json`, `contracts/schemas/investigation-cancellation-request.schema.json`

An accepted investigation is durable before its first evidence tool runs. `InvestigationStatus` exposes only bounded lifecycle metadata; it never contains the question, trigger, evidence, provider output, credentials, or model context. The authenticated tenant remains the lookup authority.

## Status

`InvestigationStatus` uses the investigation ID and exact canonical request digest. `startedAt` is immutable. A non-terminal status is either `running` or `cancellation-requested` and carries `leaseExpiresAt`. A terminal status is `completed`, `failed`, or `cancelled`, carries `completedAt` and an immutable `reportRef`, and no longer carries a lease.

The exact validated request envelope is stored unchanged. The execution lease is bounded by its accepted `maxWallTimeSeconds`; it is a crash-detection boundary, not permission to exceed the investigation deadline. A duplicate submission with the same ID and request returns the immutable report if terminal, returns `investigation.in_progress` while the lease is live, or closes an expired attempt with a failed `runtime-error` report. It never reruns tools under the same investigation ID. A retry that should gather evidence again uses a new ID.

`GET /v1/investigations/{investigationId}/status` returns the tenant-scoped status. `GET /v1/investigations/{investigationId}` continues to return only a terminal report, so existing report consumers never receive a changing document.

## Background dispatch

`POST /v1/investigation-jobs` validates resource scope and the complete investigation request before durably accepting it, then returns `202` with `InvestigationJobStatus`. Submission is idempotent for the same investigation ID and exact request digest; reusing an ID for different input returns `investigation.id.conflict`. The existing synchronous `POST /v1/investigations` remains compatible for bounded callers.

Job state is `queued`, `running`, `cancellation-requested`, `completed`, `failed`, or `cancelled`. Queue time and attempt count are always present. Queued jobs expose their next `availableAt`; claimed jobs expose bounded heartbeat and lease timestamps; terminal jobs expose completion time and, when execution started, the immutable report reference. Only a stable redacted error code may be public. Worker identity, claim token, request content, evidence, provider output, credentials, and exception text never enter this contract.

Workers claim within one explicitly configured tenant. A claim uses an unexposed random token, is atomic under competing workers, and can be renewed only by its owner. An expired dispatch lease may be reclaimed. This lease protects delivery; it does not extend `maxWallTimeSeconds` or the investigation's separate non-renewable execution lease. If a worker crashes after investigation execution starts, a replacement observes the live execution lease and retries later; after that lease expires, the existing conservative recovery path produces a failed report without repeating tools.

`GET /v1/investigation-jobs/{investigationId}` returns dispatch state. `POST /v1/investigation-jobs/{investigationId}/cancel` uses the existing cancellation envelope. Queued work becomes terminal without executing; claimed work records intent in both job and investigation lifecycle state. Cancellation wins terminal races, while already committed Evidence remains immutable.

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
- dispatch claims always include an exact tenant predicate and never enumerate tenants implicitly;
- a dispatch heartbeat cannot overwrite a concurrent cancellation; and
- retry exhaustion terminates with a stable code instead of retrying indefinitely.

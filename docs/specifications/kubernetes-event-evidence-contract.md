# Kubernetes Event evidence request and result contracts

**Status:** v1alpha1

**Machine contracts:**

- `contracts/schemas/kubernetes-event-evidence-request.schema.json`
- `contracts/schemas/kubernetes-event-evidence-result.schema.json`

These contracts define a bounded, provider-neutral read of customer Kubernetes Events related to already-known platform resources. They are distinct from the platform's internal CloudEvents history: `KubernetesEventEvidenceResult` is customer incident evidence, while `event.schema.json` records platform lifecycle facts.

## Identity, tenancy, and authority

The authenticated transport identity is authoritative. `tenantId` and `actorId` are checked assertions, and every `resourceRef` must resolve in that tenant before an adapter runs. The public request carries an integration ID but no endpoint, kubeconfig, token, namespace wildcard, field selector, client options, or raw command arguments.

The application passes a credential-free `KubernetesEventQuery` port value to an adapter. Each resource includes its platform UID and bounded Kubernetes external identity. The adapter alone resolves integration configuration and short-lived credentials. A plugin or adapter receives no ambient credential or broader resource scope.

`requestId` correlates one result; it is not an idempotency key. Repeating a historical query creates another immutable Evidence item because Kubernetes event state may have changed.

## Query and normalization semantics

The query may filter by normalized severity (`normal` or `warning`) and Kubernetes reason. Empty arrays mean no filter for that field. The time range is closed and limited to seven days, with `start < end <= requestedAt`; the deadline is at most five minutes after the request time.

Adapters map provider records to a stable event shape:

- opaque deterministic `kve_` identity;
- one requested platform resource UID;
- normalized severity and provider reason;
- a provider-independent condition taxonomy key such as `workload.progress-deadline-exceeded`;
- bounded first/last occurrence times and positive occurrence count; and
- optional controller and message text.

Events are ordered by `lastObservedAt` and then event ID so adapter return order cannot change artifact hashes. Returned records must satisfy the requested resource, severity, reason, and time bounds. The application rejects—not silently drops—out-of-scope records.

The core Kubernetes Event API stores occurrence aggregates, not an exact historical stream. When a live aggregate's first occurrence predates the requested range but its latest occurrence is inside it, the reference adapter emits only that provable latest occurrence (`firstObservedAt == lastObservedAt`, `occurrenceCount == 1`). It does not attribute the provider's full aggregate count to the requested window.

`complete` requires one or more events and no warning. `partial` requires events plus `backend-partial` or `event-limit`. `no-data` requires no events and no warnings. Summary counts must equal the normalized records. Limits bound event count, decoded artifact bytes, time range, and deadline.

## Security and privacy

All adapter fields are untrusted. The platform validates structure, scope, timestamps, control characters, enumeration values, counts, and sizes before it commits the artifact. Optional messages then pass the shared structured redactor; credentials and secret-like assignments must not survive in stored evidence, logs, summaries, or reports. Provider response bodies and exception text are replaced by stable platform errors.

The resulting JSON is stored as an `Evidence` artifact with type `kubernetes.event`, internal sensitivity, ephemeral retention, resource citations, request digest, content hash, and redaction metadata. Investigation reasoning reads only those committed tenant-scoped bytes, never an adapter's raw result.

## HTTP binding

`POST /v1/evidence/kubernetes/events/queries` accepts `KubernetesEventEvidenceRequest` and returns the committed Evidence envelope with `201 Created`.

| HTTP status | Stable code | Meaning |
| --- | --- | --- |
| `400` | `kubernetes.event.request.invalid` | Contract, identity, time, filter, resource, or bound validation failed. |
| `401` | `authentication.required` / `authentication.invalid` | Bearer authentication failed. |
| `403` | `policy.denied` | The actor cannot collect the scoped evidence. |
| `408` | `evidence.deadline.exceeded` | The bounded collection deadline elapsed. |
| `503` | `evidence.provider.unavailable` / `storage.unavailable` | Adapter, normalization/redaction, or storage failed closed. |

The default local backend returns honest `no-data`, enabling deterministic local and Docker tests without a cluster.

## Live Kubernetes API binding

The optional `kubernetes-api` adapter calls the HTTPS API directly and issues only `GET` requests. Protected tenant/integration configuration fixes the API endpoint, CA bundle, credential reference, cluster identity, namespace and resource-type allowlists, pagination limits, response limit, timeout, and condition overrides. The adapter resolves a Bearer lease for exactly `events:read` and `resources:read`; none of that configuration enters this public contract.

For each platform resource, the adapter constructs a path from a closed built-in type catalog and the observer's external identity, reads the exact current object, and binds Event lookup to its Kubernetes UID. This prevents name reuse from attaching an old object's Events to a new resource. The core `v1` Event collection is paged with an `involvedObject.uid` field selector. Omitted item TypeMeta is accepted because Kubernetes list serialization may omit it; conflicting TypeMeta and every involved-object mismatch fail closed.

Pagination or event-count truncation is `partial` only when at least one normalized Event can be returned. Truncation with no matching Event fails closed because `no-data` could not be asserted honestly. [ADR 0021](../decisions/0021-read-only-kubernetes-event-api-adapter.md) records the transport, identity, and authority semantics; the [operations guide](../operations/kubernetes-event-evidence.md) describes local and Helm configuration.

## Investigation binding

`kubernetesEventSelections` reuses the public query and limit fragments. Authenticated identity, resources, time range, and deadline are inherited from the investigation and cannot be overridden. General selections and selections whose `rootCauseClasses` contain the current resource-derived classification are eligible. Collection requires `kubernetes.event` and `events/search` when the request supplies those upper bounds.

A root-cause-scoped selection may declare an interpretation: a set of normalized conditions, a minimum match count, and distinct matched/unmatched dispositions. Each attempt consumes one tool call; a committed result consumes one evidence item. Event selections run before metric selections, making cross-signal budget order deterministic.

Assessment occurs only from the exact committed artifact. Complete evidence reports matched IDs/count and a supporting, contradicting, or neutral disposition. Explicit empty evidence is `no-data`; partial evidence is `incomplete`. Corrupt or mismatched evidence becomes a stable unknown. The deterministic runtime adds citations but does not silently change root-cause class, rank, confidence, or terminal outcome.

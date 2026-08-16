# ADR 0021: Query Kubernetes Event evidence through an exact-scope read-only API adapter

**Status:** Accepted

**Date:** 2026-08-16

## Context

ADR 0020 fixes the normalized Kubernetes Event evidence boundary but intentionally leaves live transport replaceable. A live implementation must resolve platform resource identity to the current Kubernetes object, avoid ambient kubeconfig authority, tolerate the Kubernetes API's list encoding, and stay bounded even when Event collections are large. Running `kubectl` inside the control plane would add a shell/tool dependency and make the selected context part of process-global state.

Kubernetes core `Event` objects are lossy aggregates. A series may have started before the requested window even when its latest occurrence falls inside it; the API does not expose the individual historical occurrences needed to calculate an exact in-window count.

## Decision

- Add a `KubernetesApiEventsBackend` adapter selected only by `IIP_KUBERNETES_EVENTS_BACKEND=kubernetes-api`. Keep `no-data` as the default.
- Call the Kubernetes HTTPS API directly with verified TLS. Never execute `kubectl` in the server adapter and never follow credential-bearing redirects.
- Resolve the exact `(tenantId, integrationId)` from protected configuration. That record fixes the endpoint, CA bundle, cluster external identity, namespace and resource-type allowlists, pagination/response/time limits, condition mappings, and a logical credential reference.
- Resolve a Bearer lease for exactly `events:read` and `resources:read`. The local static broker exists for development; production remains responsible for external short-lived issuance and rotation.
- Reconstruct API paths only from built-in resource-type bindings and the normalized external identity emitted by the Kubernetes observer. Read each exact current object first and bind Event lookup to its provider UID, preventing stale name reuse from attaching old evidence to a new object.
- Issue only Kubernetes API `GET` requests: one exact object read and paginated namespace Event collection reads with an `involvedObject.uid` field selector. Reject resources outside the configured cluster, namespace, or type.
- Accept omitted `apiVersion`/`kind` on Event list items because Kubernetes list serialization may omit item TypeMeta; reject conflicting values and validate the typed `EventList` envelope plus every involved-object identity.
- Map known reasons to stable condition keys, allow protected per-integration overrides, and map an otherwise valid reason to `kubernetes.event.unclassified` rather than deriving meaning from message text.
- Include an Event series' full count only when its first and last timestamps both fit the requested range. If the series began before the range, represent the in-range latest observation as `firstObservedAt == lastObservedAt` and `occurrenceCount == 1`; never claim an unverifiable historical count.
- Mark bounded truncation as `partial` when at least one event is available. Fail closed if pagination truncates an otherwise empty result, because the public contract cannot honestly express “possibly no data.”
- Keep the real-cluster test opt-in. It creates an isolated namespace, a service account with only Deployment `get` and Event `get/list`, a fixture Event, and a short-lived token; it deletes the namespace when finished.

## Consequences

- Investigations, public contracts, SDKs, and the Evidence pipeline can use live Kubernetes Events without importing Kubernetes client types or credentials.
- The adapter works in the API image without a `kubectl` binary. Docker Desktop's built-in Kubernetes is optional; an explicit kind context is sufficient.
- Protected configuration and the CA bundle must be deployed alongside a secret-bearing credential document. NetworkPolicy egress must explicitly permit only the selected API server address and port.
- Core Event aggregates cannot provide arbitrary historical reconstruction. Missing retained Events remain honest no-data, not proof that an incident never occurred.
- Related-resource traversal, `events.k8s.io/v1`, watch streaming, dynamic integration registration, and a production credential broker remain later work.

## Revisit triggers

Revisit when design-partner clusters require workload identity, client certificates, an external credential broker, `events.k8s.io/v1`, cluster-scoped Event namespace discovery, related-object traversal, or a retained event backend with exact historical occurrence semantics.

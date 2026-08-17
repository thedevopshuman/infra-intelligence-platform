# Event delivery health report contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/event-delivery-health-report.schema.json`

`EventDeliveryHealthReport` is the authenticated tenant view of transactional-outbox backlog and terminal delivery quarantine. It tells an operator whether accepted platform events are still waiting, currently leased, retrying after a bounded failure, or no longer eligible for automatic delivery.

`healthy` means the tenant has neither pending nor quarantined events. `backlogged` means at least one event is pending and none is quarantined. `degraded` means at least one event exhausted its configured attempt budget and entered quarantine; an ordinary backlog may also exist.

`pendingEvents` excludes published and quarantined rows. `inFlightEvents` and `retryingEvents` are subsets of pending events. The oldest pending age is evaluated with platform time and is present only when the pending count is nonzero. The bounded quarantine list is newest-first and returns stable CloudEvents identity and routing metadata only. It never returns event `data`, credentials, endpoint configuration, provider response text, stack traces, or exception messages.

## Authority

`GET /v1/operations/events/delivery-health?limit=50` requires Bearer authentication, the `platform-admin` role, and policy approval for `event-delivery-health:read`. The authenticated tenant is the only storage scope; no tenant parameter is accepted.

This contract is read-only. Requeueing a quarantined event can cause a duplicate external effect and therefore requires a separate future governed workflow with policy evaluation, idempotency, audit, and explicit recovery semantics. Database edits and automatic quarantine replay are not part of this contract.

| HTTP status | Code | Meaning |
| --- | --- | --- |
| `400` | `request.invalid` | The query is malformed, contains an unknown field, or has a limit outside 1–50. |
| `401` | `authentication.required` / `authentication.invalid` | Bearer authentication failed. |
| `403` | `policy.denied` | The platform-admin role or policy authorization is absent. |
| `503` | `event.delivery-health.unavailable` | Storage returned inconsistent state or was unavailable. |

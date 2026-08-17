# Event delivery SLO report contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/event-delivery-slo-report.schema.json`

`EventDeliverySloReport` is the authenticated, tenant-scoped rolling objective for the transport-neutral publication path. It measures each durable outbox row once from platform creation to durable publisher acknowledgement.

The window is configured by the deployment and ends at `metadata.evaluatedAt`. An event is `immature` until `maximumDeliveryLatencySeconds` has elapsed. The remaining events form the eligible cohort:

- `withinObjectiveEvents` were acknowledged at or before their individual deadline;
- `lateDeliveredEvents` were acknowledged after that deadline;
- `undeliveredEvents` still have no durable acknowledgement at evaluation time;
- `quarantinedEvents` is a subset of `undeliveredEvents`.

`eligibleEvents` equals the sum of within-objective, late-delivered, and undelivered events. `createdEvents` equals eligible plus immature events. `attainmentBasisPoints` is `null` when the eligible cohort is empty; otherwise it is `floor(withinObjectiveEvents * 10000 / eligibleEvents)`.

`no-data` means there is no mature cohort. `insufficient-data` means the cohort is smaller than `minimumEligibleEvents`. Once the minimum is reached, `meeting` means attainment is at least `minimumAttainmentBasisPoints`; otherwise the status is `breached`.

The report measures publisher acknowledgement, not downstream customer processing. It contains aggregate counts only—never event identity or payload, destination configuration, credentials, provider output, or exceptions.

## Authority

`GET /v1/operations/events/delivery-slo` requires Bearer authentication, the `platform-admin` role, and policy approval for `event-delivery-slo:read`. Tenant and objective values come from authenticated and deployment context; the endpoint accepts no query parameters.

| HTTP status | Code | Meaning |
| --- | --- | --- |
| `400` | `request.invalid` | The endpoint received any query field. |
| `401` | `authentication.required` / `authentication.invalid` | Bearer authentication failed. |
| `403` | `policy.denied` | The platform-admin role or policy authorization is absent. |
| `503` | `event.delivery-slo.unavailable` | Storage returned inconsistent state or was unavailable. |

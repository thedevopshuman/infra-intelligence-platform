# Transactional outbox event delivery

**Status:** Executable reference implementation

Every accepted resource observation commits its structured CloudEvent and an outbox row in the same PostgreSQL transaction. The optional workflow-worker dispatcher publishes those rows at least once without changing the event contract or exposing private lease state.

## Delivery guarantees

- Claims always include one exact tenant and a named worker identity.
- A successful publish is acknowledged only while that worker still owns the lease.
- A provider failure releases the row with a stable code and exponentially increasing delay capped by configuration.
- A crash or lost acknowledgement after publish can cause redelivery. Consumers must deduplicate the CloudEvents identity `(tenantid, source, id)`; the HTTPS adapter also sends `Idempotency-Key: <event id>`.
- One failed row does not stop later rows in the same bounded batch.

## Local Docker sink

The Docker Desktop profile sets `IIP_EVENT_PUBLISHER_MODE=stdout-json`. The worker writes a bounded `outbox.event.published` JSON record, flushes it, and then acknowledges the row. This proves the delivery lifecycle and clears local freshness backlog without requiring a broker. It is a development sink: customer infrastructure identifiers can appear in container logs.

Inspect aggregate delivery without exposing credentials:

```bash
docker logs iip-local-workflow-worker-1 2>&1 \
  | rg 'outbox.delivery.completed|ingestion-freshness.sampled'
```

## HTTPS webhook mode

The Helm chart supports an authenticated structured-CloudEvents endpoint:

```yaml
worker:
  enabled: true
  tenants: [tenant-a]

database:
  existingSecret: iip-database

eventPublisher:
  mode: https-webhook
  httpsWebhook:
    endpoint: https://events.example.test/v1/cloudevents
    tokenExistingSecret: iip-event-publisher-token
    caBundleExistingSecret: iip-event-publisher-ca

networkPolicy:
  enabled: true
  databaseEgress:
    enabled: true
  eventPublisherEgress:
    enabled: true
    cidr: 10.20.30.40/32
    port: 443
```

The token Secret must contain the configured key, `token` by default. The optional CA Secret must contain `ca.crt`. The token is read for every publish and never enters an environment variable, event, log, or error. All `worker.tenants` are routed to the configured endpoint; the runtime rejects any difference between webhook tenant authority and worker enrollment.

## Runtime settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `IIP_EVENT_PUBLISHER_MODE` | `disabled` | `disabled`, local-only `stdout-json`, or `https-webhook` |
| `IIP_EVENT_PUBLISHER_CONFIG_JSON` | unset | Closed HTTPS endpoint, exact tenant IDs, mounted token/CA paths, timeout, and response bound |
| `IIP_OUTBOX_BATCH_SIZE` | `100` | Messages claimed per tenant pass, 1–500 |
| `IIP_OUTBOX_LEASE_SECONDS` | `30` | Delivery ownership lease, 5–300 seconds |
| `IIP_OUTBOX_RETRY_BASE_SECONDS` | `5` | Initial failed-delivery delay, 1–300 seconds |
| `IIP_OUTBOX_RETRY_MAX_SECONDS` | `300` | Backoff cap, no smaller than the base and no larger than 3600 seconds |
| `IIP_OUTBOX_MAX_ATTEMPTS` | `8` | Final failed attempt moves the tenant-scoped row into terminal quarantine, 1–1000 |
| `IIP_EVENT_DELIVERY_SLO_WINDOW_SECONDS` | `3600` | Rolling cohort duration, 300–2,592,000 seconds |
| `IIP_EVENT_DELIVERY_SLO_MAXIMUM_LATENCY_SECONDS` | `60` | Maximum creation-to-publisher-acknowledgement latency; must be shorter than the window |
| `IIP_EVENT_DELIVERY_SLO_MINIMUM_ATTAINMENT_BASIS_POINTS` | `9900` | Required mature-cohort attainment, 1–10,000 basis points |
| `IIP_EVENT_DELIVERY_SLO_MINIMUM_ELIGIBLE_EVENTS` | `20` | Minimum mature cohort before reporting meeting or breached |

## Quarantine recovery

After the configured final failure, the owned row is quarantined with platform time and `event.publisher.unavailable`; it is excluded from normal claims and ingestion pending counts. Inspect exact-tenant backlog and bounded value-minimized quarantine metadata with `GET /v1/operations/events/delivery-health?limit=50` as a `platform-admin`. The endpoint never returns event data, destination configuration, credentials, or provider responses. Quarantine is not deletion, and there is no automatic or ungoverned requeue endpoint.

After repairing and validating the customer receiver, run an investigation with `maxAuthority: propose` over the quarantined event's `subject`. Propose `event-delivery.requeue` using the exact `outboxId`, `eventId`, `quarantinedAt`, and `attempts` returned by the health report. Dry-run is the default. A different `approver` must review it, and an `executor` performs the single live attempt only when the immutable proposal explicitly sets `dryRun: false`. Success preserves the CloudEvents ID and resets the finite delivery-attempt cycle. A stale proposal fails without changing the row. The receiver must deduplicate `(tenantid, source, id)` because replay does not weaken at-least-once semantics.

## Rolling publication objective

`GET /v1/operations/events/delivery-slo` gives a `platform-admin` the configured exact-tenant publication objective. The deployment fixes the window, maximum latency, attainment target, and minimum sample; callers cannot select a tenant or easier objective.

The denominator contains each durable outbox row once after its individual latency deadline. Publisher acknowledgement by the deadline is a success. Late acknowledgement and still-undelivered rows are misses, while quarantine is reported as a subset of undelivered misses. Attainment uses integer basis points and reports `no-data`, `insufficient-data`, `meeting`, or `breached`. Retries and governed replay do not inflate the cohort or turn a late event into an on-time one.

This is not a receiver-processing claim. For HTTPS it ends at a successful bounded response; a future broker adapter ends it at its declared durable acceptance boundary. Keep outbox history for at least the configured window and certify downstream receiver processing separately.

## Remaining production work

The webhook is one replaceable publisher, not a claim that HTTP replaces a broker. A production decision still needs measured throughput and failure evidence, outbox retention enforcement, long-term/regional objective aggregation and alert routing, payload retention rules, bulk-recovery policy, and customer receiver interoperability tests. Kafka, NATS JetStream, cloud queues, or another transport can implement the same application port without changing ingestion or SLO semantics.

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

## Remaining production work

After the configured final failure, the owned row is quarantined with platform time and `event.publisher.unavailable`; it is excluded from normal claims and ingestion pending counts. Inspect exact-tenant backlog and bounded value-minimized quarantine metadata with `GET /v1/operations/events/delivery-health?limit=50` as a `platform-admin`. The endpoint never returns event data, destination configuration, credentials, or provider responses. Quarantine is not deletion, and there is intentionally no automatic or ungoverned requeue endpoint.

The webhook is one replaceable publisher, not a claim that HTTP replaces a broker. A production decision still needs measured throughput and failure evidence, governed replay, delivery latency/error SLOs, payload retention rules, and customer interoperability tests. Kafka, NATS JetStream, cloud queues, or another transport can implement the same application port without changing ingestion.

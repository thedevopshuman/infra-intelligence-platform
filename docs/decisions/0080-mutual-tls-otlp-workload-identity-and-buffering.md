# ADR 0080: mutual TLS OTLP workload identity and buffering

**Status:** Accepted
**Date:** 2026-08-17

## Context

The isolated OTLP receiver had tenant-bound Bearer channels, strict payload
budgets, and durable PostgreSQL evidence persistence. It did not authenticate
the sending workload independently of the channel token, encrypt its listener,
or define who owns telemetry waiting to be delivered during receiver outages.

## Decision

The production receiver profile uses TLS 1.2 or later with a customer-selected
server certificate and client CA. Every OTLP POST requires a CA-verified client
certificate containing exactly one configured SPIFFE URI SAN. Protected
configuration maps that SPIFFE ID to explicit channel IDs. The existing
tenant-bound Bearer channel credential remains a separate factor; neither
factor can widen the other's authority. Health and readiness disclose only a
stable process/dependency result and may be queried over verified server TLS
without a client certificate.

The receiver authenticates the Bearer channel and client identity before
reading a request body. It returns success only after normalized Evidence and
its artifact are committed transactionally to PostgreSQL. The receiver remains
stateless and horizontally replaceable, so pre-receiver outage buffering belongs
to the customer-side OpenTelemetry Collector. The shipped Collector example
uses `file_storage` plus the exporter's persistent sending queue, requires a
customer-provisioned persistent volume, retries indefinitely, and sends through
mutual TLS. Queue capacity, disk alerts, retention, and loss policy remain
customer deployment choices.

`disabled` and server-only TLS modes remain explicit development or migration
profiles. Helm defaults to disabled because the receiver itself is disabled by
default, but a production enablement must select `mutual-spiffe` and provide all
three protected inputs: server keypair, client CA, and identity registry.

## Consequences

- A stolen channel token alone cannot submit telemetry from an untrusted
  workload, and a valid client certificate cannot choose a different channel.
- Certificate rotation can preserve authority by retaining the SPIFFE ID; scope
  changes require a protected registry update.
- Load balancers must preserve end-to-end client certificates or terminate mTLS
  only in a separately reviewed identity-aware gateway profile.
- Collector queues survive transient receiver or network outages only when the
  configured storage directory is backed by durable customer storage.
- This decision does not make IIP a general telemetry backend: only allowlisted
  signals become bounded investigation Evidence.

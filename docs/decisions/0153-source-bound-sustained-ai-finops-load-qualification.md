# ADR 0153: Qualify source-bound sustained AI FinOps load locally

**Status:** Accepted

**Date:** 2026-09-08

## Context

The local AI FinOps Docker gate proves a broad provider-neutral flow with 15
fixed usage records. The customer sustained-workload gate exercises API reads,
OTLP metrics, and asynchronous investigations, but deliberately sends no GenAI
provider traffic. Neither artifact establishes that the usage ledger,
attribution worker, cost worker, and aggregate exporter keep pace with a
declared sustained AI telemetry arrival rate or drain their backlogs afterward.

Looping the functional fixture would mostly exercise deterministic
deduplication because it reuses fixed invocation identities and historical
timestamps. Treating Collector exporter success as durable receiver delivery
would also be incorrect: the Collector may acknowledge pipeline acceptance
before its exporter receives a PostgreSQL-backed IIP acknowledgement.

A local Docker benchmark still cannot define customer capacity. Host
resources, customer Collector configuration, private networking, database
class, provider mix, burst distribution, retention, and production failures
remain outside that environment.

## Decision

Add a content-addressed `AiFinopsSustainedLoadProfile`, a source-bound
`AiFinopsSustainedLoadQualificationReport`, and an explicitly run Docker
qualifier.

1. The closed `local-ai-finops-sustained-load-v1` profile schedules current,
   unique, metadata-only spans at a bounded fixed rate through the existing
   separate Bedrock- and OpenAI-shaped Collector channels. Distribution is
   equal and deterministic; there is no provider SDK call or inference proxy.
2. A monotonic scheduler counts late slots as misses and never emits catch-up
   bursts. Duration, rate, concurrency, replay share, request timeout, lag,
   total unique volume, and drain time are hard bounded. The sustained cohort
   is capped at 9,984: together with the wrapper's 15 seeded functional records,
   it remains below the rolling allocation exporter's fail-closed 10,000-record
   source ceiling.
3. After exact original durability (`usageRecordsBeforeReplay =
   attemptedSpans`), a deterministic post-load replay sends
   exact copies from the newest durable tail of the original cohort, keeping
   replay timestamps within the receiver's age limit at the longest profile
   duration. Incomplete durability suppresses replay and records zero scheduled
   replay work. Replay is paced at the original total span rate, starts no new
   send at or after the shared drain deadline, and goes directly to the isolated
   receiver under the matching fixed fixture channel. Only a PostgreSQL
   commit-bound acknowledgement observed by the deadline counts as success;
   explicit rejection, timeout, or a later acknowledgement is unacknowledged.
   In-flight client shutdown is bounded by one request timeout and remains part
   of the measured run duration. The receiver must acknowledge every replay by
   the deadline without increasing the usage-ledger count. Final attribution
   and cost counts prove completion, not unchanged generations across replay.
4. Original Collector exporter success is retained only as
   `collector-pipeline-acceptance-only`. Exact PostgreSQL deltas independently
   prove durable usage, attribution, cost, pricing coverage, stage latency,
   sampled peak backlog, final drain, and provider distribution for this run's
   96-bit trace-prefix cohort. Attribution and cost joins require the exact
   fixed local policy/catalog IDs and versions plus attribution engine `0.1.0`
   and cost engine `0.2.0`. At least one successful in-workload backlog sample
   is required and counted separately from the final snapshot.
5. Prometheus must converge to the exact final durable usage and cost counts,
   and no other `local`-tenant usage record created at or after the database
   marker may have a trace ID outside the run's 96-bit trace-prefix cohort.
   This prevents a second durable local workload from compensating the global
   application-dimension delta; it does not claim host/process isolation from
   arbitrary non-IIP traffic. Grafana provisioning, metadata-only storage, and
   the absence of prohibited high-cardinality/content labels remain explicit
   checks.
6. The report is content-addressed and contains aggregate measurements only.
   It excludes tenant, service, model, target, credential, trace/span/request
   identity, token quantity, price, amount, content, request sample, and raw
   error values. The runner inspects the application and Collector images that
   actually run. Traffic-free verification content-binds both reported image
   identities but attests the application image against a trusted expected
   digest only when the caller supplies `--image-digest`.
7. The qualifier remains outside the server, API, SDK behavior, agent/plugin
   runtime, and normal `make verify` traffic boundary. SDKs expose offline
   envelope types only.
8. The existing fixed functional report remains unchanged. Making sustained
   load mandatory for release readiness requires a separately versioned
   readiness profile; local evidence cannot replace customer sustained-AI
   workload qualification.

## Consequences

- A regression can be localized to scheduling/Collector intake, usage
  persistence, attribution, cost, backlog drain, aggregate export, or the
  visualization/privacy boundary instead of appearing as one generic timeout.
- Bounded replay pacing, the shared deadline, and report safety ceilings keep
  completed objective misses representable as `not-qualified` evidence.
- All attainment ratios use scheduled unique work, so generator starvation or
  missed slots cannot make the result look healthier.
- The fully priced, allocatable load cohort measures pipeline progress without
  duplicating the functional fixture's unpriced and finding semantics.
- Five minutes and 9,984 unique spans are minimum-duration and maximum-volume
  local evidence boundaries, not a customer throughput promise or long-window
  SLO. The volume leaves room for the 15-record functional seed below the
  allocation exporter's 10,000-record ceiling.
- Customer Collector/PKI/network qualification, live providers, approved or
  private pricing, invoice agreement, representative burst/load profiles,
  failure overlap, regional availability, and backend lifecycle remain
  separate gates.

## Alternatives considered

- Increasing the fixed fixture count was rejected because it would combine
  functional correctness and load objectives while retaining fixed identities
  and windows.
- Reusing the customer core-workload report was rejected because that contract
  explicitly excludes GenAI spans and AI economics throughput.
- Measuring only Collector responses was rejected because pipeline acceptance
  is not durable IIP delivery.
- Adding an always-on in-product load service was rejected because the serving
  platform needs no ambient traffic-generation authority.

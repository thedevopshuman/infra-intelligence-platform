# Customer sustained workload qualification contract

**Status:** Accepted v1alpha1 contract

**Schemas:**
[`customer-sustained-workload-profile.schema.json`](../../contracts/schemas/customer-sustained-workload-profile.schema.json),
[`customer-sustained-workload-qualification-report.schema.json`](../../contracts/schemas/customer-sustained-workload-qualification-report.schema.json)

**Examples:**
[`customer-sustained-workload-profile.json`](../../contracts/examples/customer-sustained-workload-profile.json),
[`customer-sustained-workload-qualification-report.json`](../../contracts/examples/customer-sustained-workload-qualification-report.json)

## Purpose and boundary

`CustomerSustainedWorkloadProfile` is the protected, reviewed authority for one
bounded customer-environment workload. It selects one exact release, tenant,
actor, existing resource, direct API and OTLP targets, synthetic metric,
investigation budget, traffic rates, concurrency, and acceptance objectives.

`CustomerSustainedWorkloadQualificationReport` is the transportable,
privacy-minimized result. It answers whether the selected release sustained the
configured mix of exact runtime-identity reads, PostgreSQL-durable OTLP metric
writes, and durable asynchronous investigations for the selected window.

Both are operational evidence. Neither is a control-plane resource or an API
for creating traffic. SDKs expose only the envelope types.

## Protected profile

The profile is content-addressed as `cswp_<32 hex>` over its metadata without
`id` and its complete `spec`. It must be a regular non-symlink file with no
group or other permissions, mode `0600`, and owned by the current process user
when used by the runner. `reviewedAt`, `validUntil`, and
`maximumProfileAgeSeconds` bound the operator approval. The approval must remain
current through the completed workload; an expired selection produces no
promotable report.

The closed `customer-sustained-core-workload-v1` profile permits:

- 15 minutes through four hours of scheduled traffic;
- 1–30 probe cycles per second and 1–64 probe workers;
- 1–20 investigation submissions per minute and 1–32 workflow workers;
- no more than 500,000 probe cycles or 5,000 investigations;
- combined probe and workflow concurrency no greater than 96;
- integer-basis-point success and scheduler-miss objectives;
- ordered p95 and p99 API, receiver, and workflow objectives; and
- request, scheduler-lag, workflow-completion, profile-age, and report-validity
  bounds.

The API and OTLP base URLs must be direct HTTPS origins without credentials,
paths, queries, or fragments. API access uses a protected Bearer credential.
OTLP access uses a separate protected Bearer credential and a client
certificate/key pair. There is no insecure TLS mode.

## Workload and scheduler semantics

Each probe cycle performs exactly two operations using the production
qualification client:

1. authenticated `GET /v1/system/version`, accepted only when it identifies the
   profile's exact application, contracts, migration, source revision, chart,
   and immutable image digest;
2. non-empty OTLP protobuf `POST /v1/metrics`, accepted only after the receiver
   returns an empty successful response. The selected receiver contract defines
   HTTP 200 after the Evidence and event transaction commits in PostgreSQL.

Each workflow slot submits one zero-model, bounded
`InvestigationRequest` for the selected resource to
`POST /v1/investigation-jobs`, polls its durable job, and then validates the
terminal `InvestigationReport` for the exact tenant and ID. `conclusive` and
`inconclusive` reports are both useful completions; failure, cancellation,
timeout, malformed output, or an unavailable report is a terminal miss.

Probe and workflow slots use independent monotonic fixed-rate schedules. A
worker waits for its assigned slot. If it arrives later than
`maximumSchedulerLagMilliseconds`, the slot is counted as missed and is never
replayed as a catch-up burst. The runner creates one client per worker so
mutable TLS/HTTP client state is not shared across threads.

## Measurements and status

All objective ratios use the scheduled count as their denominator. Scheduler
misses therefore cannot improve API, receiver, or workflow attainment.
Successful-response latency percentiles use nearest-rank integer milliseconds;
failed operations remain visible in aggregate counts and are excluded from
successful latency distributions. A percentile set is null only when that path
has no successes.

The semantic validator recomputes:

- declared probe and workflow volumes;
- attempted, missed, successful, failed, accepted, completed, and terminal
  accounting;
- integer basis points and ordered latency summaries;
- the bounded observation window;
- all 22 ordered checks, the summary, fixed limitations, and report status;
- profile and report content identifiers; and
- expiry no later than the profile, profile-age, or report-validity boundary.

A completed workload produces `qualified` only when every check passes. A
completed run that misses an objective produces valid `not-qualified` evidence.
Unsafe input, stale approval, excessive volume, unreadable protected material,
dirty source, release mismatch, or generator failure stops without a report.

## Data minimization

The report retains public release identity, timestamps, platform/runtime
versions, aggregate counts and percentiles, stable checks, and SHA-256 target
and profile bindings. It never retains tenant, actor, resource, metric/service,
URL/endpoint, hostname, IP address, credential, certificate, prompt, response,
trace/span identity, investigation identity, request-level sample, or raw
exception. Binding digests remain pseudonymous customer evidence and require
the qualification channel's normal access controls and retention policy.

## Verification and nonclaims

Offline verification performs no traffic. It revalidates the report's schema,
semantics, expiry, current protected profile, exact clean source/release, and
both normalized target bindings.

One qualified report covers one synthetic mix, tenant, resource, target pair,
release, objective, and window. It does not prove representative customer
traffic, arbitrary resource/query/action endpoints, provider or inference
calls, failure behavior, database failover, persistent Collector queue
recovery, node/zone/region availability, long-window SLO attainment, or
production approval.

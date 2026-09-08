# AI FinOps sustained-load qualification contract

**Status:** Accepted local `v1alpha1` evidence contract
**API version:** `iip.dev/v1alpha1`

**Schemas:**
[`ai-finops-sustained-load-profile.schema.json`](../../contracts/schemas/ai-finops-sustained-load-profile.schema.json),
[`ai-finops-sustained-load-qualification-report.schema.json`](../../contracts/schemas/ai-finops-sustained-load-qualification-report.schema.json)

**Examples:**
[`ai-finops-sustained-load-profile.json`](../../contracts/examples/ai-finops-sustained-load-profile.json),
[`ai-finops-sustained-load-qualification-report.json`](../../contracts/examples/ai-finops-sustained-load-qualification-report.json)

## Purpose and boundary

`AiFinopsSustainedLoadProfile` selects one bounded, synthetic fixed-rate load
for the disposable Docker AI FinOps topology. The closed
`local-ai-finops-sustained-load-v1` profile sends complete, priced,
attributable metadata-only spans through the separate `aws.bedrock` and
`openai` Collector routes using an equal round-robin distribution.

`AiFinopsSustainedLoadQualificationReport` retains aggregate evidence that the
selected source and inspected application image sustained Collector intake,
durable usage persistence, attribution, cost calculation, privacy-bounded
aggregate export, and dashboard availability. The subject fixes attribution
engine `0.1.0` and cost engine `0.2.0`; bindings fix the exact local fixture
policy and catalog generations used by the database measurements. It is an
offline operational envelope, not a control-plane resource or an API that
creates traffic. SDKs expose its shape only.

The qualification boundary is exactly
`single-host-docker-synthetic-ai-economics-load`. A qualified report detects a
local regression for the declared host, versions, profile, and synthetic
cohort. It is not customer sizing, representative production traffic, live
provider interoperability, invoice reconciliation, or an availability claim.

## Profile identity and bounds

The profile ID is `afslp_` followed by the first 32 lowercase hexadecimal
characters of SHA-256 over canonical JSON containing `metadata` without `id`
and the complete `spec`. Canonical JSON sorts object keys, uses no insignificant
whitespace, and encodes non-ASCII characters with JSON escapes. The report
binds both this ID and the SHA-256 digest of the complete canonical profile.

The profile permits:

- 300 through 1,800 seconds of traffic;
- 1 through 40 scheduled unique spans per second;
- 1 through 32 producer workers;
- deterministic replay of 100 through 1,000 basis points of the durable
  original cohort;
- request timeouts of 100 through 30,000 milliseconds;
- scheduler-lag limits of 10 through 5,000 milliseconds; and
- pipeline-drain limits of 1 through 300 seconds.

The semantic validator additionally rejects a profile whose
`durationSeconds * spansPerSecond` exceeds 9,984 or is odd. The shell wrapper
first seeds the 15-record functional fixture, while the rolling allocation
projection intentionally fails closed above its 10,000-source-record ceiling;
9,984 is the largest even sustained cohort that keeps the combined 9,999
records inside that ceiling. The even-count rule ensures the closed round-robin
distribution schedules exactly half of the unique spans for each provider. A
workload is deliberately smaller than the receiver's broad protocol limit; the
purpose is comparable regression evidence, not maximum-host benchmarking.

## Fixed generation bindings

The closed local cohort requires exactly:

- attribution policy `aap_22222222222222222222222222222222`, version
  `2026-09-05.1`, evaluated by attribution engine `0.1.0`; and
- price catalog `apc_11111111111111111111111111111111`, version
  `2026-09-05.1`, evaluated by cost engine `0.2.0`.

The report repeats the policy and catalog identities under `bindings` and the
engine versions under `subject`. PostgreSQL measurement joins require all four
generation values rather than accepting any other record produced in the run
window. Source hashes, meter rates, token quantities, and calculated amounts
remain excluded from the report.

## Fixed-rate and replay semantics

One monotonic global schedule assigns one unique span to each slot. Producer
concurrency divides that schedule without changing its rate. A slot reached
later than `maximumSchedulerLagMilliseconds` is counted as missed and is never
replayed as a catch-up burst. All original-flow attainment ratios use
`scheduledSpans`, including missed slots, as their denominator.

Every attempted original span has unique trace and span identity derived from
the run and slot. Where the selected channel exposes a provider request-ID
attribute, that identity is also unique: the Bedrock shape carries
`aws.request_id`, while the closed OpenAI shape deliberately has no request-ID
mapping. Each span uses a current invocation timestamp; the existing
four-timestamp functional fixture must not be looped as a load generator. After
every attempted original span is durable, the runner calculates
`floor(usageRecordsBeforeReplay * replayBasisPoints / 10000)` and selects that
many records from the newest durable tail in original schedule order. If exact
original durability is not reached, replay is suppressed and both replay
`scheduledSpans` and `attemptedSpans` are zero. Selecting the tail keeps replay
timestamps inside the receiver's 900-second age limit even for the permitted
1,800-second profile.

Exact original durability means `usageRecordsBeforeReplay = attemptedSpans`.
Consequently, a scheduler miss does not by itself suppress replay. An observed
Collector failure suppresses replay only when the corresponding attempt does
not reach the durable usage ledger; any attempted span still absent there at
the shared drain deadline suppresses replay.

The replay phase stays separate from the original fixed-rate schedule, is paced
at the original total span rate, and shares the bounded pipeline-drain deadline.
It starts no new send at or after that deadline. Exact original payloads go
directly to the isolated receiver under the corresponding fixed local fixture
channel. This deliberate replay-only bypass of the asynchronous Collector makes
each `receiverAcknowledgedSpans` result a PostgreSQL commit-bound
acknowledgement observed by the deadline. An explicit rejection, request
timeout, or acknowledgement observed after the deadline is counted in
`receiverUnacknowledgedSpans`. Shutdown of an in-flight client is bounded by
one request timeout and included in `actualDurationMilliseconds`; it cannot
turn a late acknowledgement into an on-time one. Usage-ledger idempotency
passes only when the receiver acknowledges every scheduled replay by the
deadline, leaves none unacknowledged, and `usageRecordsAfterReplay` equals
`usageRecordsBeforeReplay`; final attribution and cost counts remain completion
evidence, not unchanged-generation proof. Replay suppressed because exact
original durability is incomplete is valid report data but fails
`replay-idempotency` and cannot produce a qualified report.

## Acceptance, durability, and completion

An original-flow OpenTelemetry exporter success means only that the Collector
accepted a request into its pipeline. The fixed
`collector-pipeline-acceptance-only` value prevents that response from being
reported as durable IIP delivery. Durable receiver success is established
independently from exact PostgreSQL ledger deltas; the IIP receiver acknowledges
its downstream Collector only after the usage and event transaction commits.
Replay uses that same receiver commit boundary directly so an unchanged count
cannot pass merely because an asynchronous Collector accepted or queued it.

The following ratios use integer floor division in basis points:

- Collector acceptance: accepted original spans divided by scheduled spans;
- usage persistence: new unique usage records divided by scheduled spans;
- attribution completion: new attribution records divided by scheduled spans;
- cost completion: new cost records divided by scheduled spans; and
- priced coverage: priced cost records divided by cost records, or zero when
  no cost record exists.

Generator accounting must satisfy:

`attemptedSpans + schedulerMissedSpans = scheduledSpans` and
`collectorAcceptedSpans + collectorRejectedSpans = attemptedSpans`.

Replay receiver acknowledgements plus receiver-unacknowledged results must
equal replay attempts. When original durability is incomplete, all replay
scheduling, attempt, acknowledgement, and unacknowledged counts are zero.
Usage, attribution, cost, priced, unpriced, per-provider, and backlog counts
must reconcile exactly.

Reported drain time must cover the minimum wall time required to pace the
attempted replay at `spansPerSecond`. Whole-run duration must in turn cover the
fixed-rate workload minimum plus the reported drain time; a report cannot pair
a fully paced replay with a shorter drain or total duration.

Database joins select only this run's 96-bit trace-prefix cohort and require the
exact fixed local attribution-policy ID/version, price-catalog ID/version, and
attribution/cost engine versions. The report retains those fixture generations
as bindings, not source hashes, rates, or record identities.

Attribution and cost stage latencies are calculated from the usage record's
`recordedAt` to the corresponding `resolvedAt` or `calculatedAt`; the
provider-controlled invocation timestamp is not used as a pipeline clock.
Latency summaries use nearest-rank integer milliseconds, are ordered p50 ≤ p95
≤ p99 ≤ maximum, and are entirely null only when the corresponding successful
cohort is empty. The report envelope permits stage latencies through 2,200,000
milliseconds and an overall run duration through 2,400,000 milliseconds so a
completed miss across the largest permitted workload remains representable;
these safety ceilings are not performance objectives.

The runner samples attribution and cost backlog during the workload and records
the number of successful in-workload snapshots as `backlogSampleCount`; the
final snapshot is excluded from that count and the contract bounds it at 2,000.
It then requires at least one such sample and both final backlogs to be zero
within `maximumPipelineDrainMilliseconds`. Peak values describe pressure but
have no standalone pass threshold. `cohortIsolationPreserved` is true only when
no other `local`-tenant usage record created at or after the database marker has
a trace ID outside this run's 96-bit trace-prefix cohort. This prevents a second
durable local workload from compensating the global application-dimension
Prometheus delta; it does not claim host or process isolation from arbitrary
non-IIP traffic. Prometheus converges only when that isolation condition holds
and its bounded usage and cost request aggregates equal the final durable
cohort. Dashboard availability is a provisioning/read check, not proof that a
human reviewed the dashboard.

## Checks and qualification

The report contains exactly 19 ordered checks:

1. `source-binding`
2. `profile-binding`
3. `compose-configuration`
4. `all-components-healthy`
5. `bounded-load-volume`
6. `fixed-rate-scheduler`
7. `collector-acceptance`
8. `usage-ledger-persistence`
9. `replay-idempotency`
10. `attribution-completion`
11. `cost-completion`
12. `priced-coverage`
13. `export-p95-latency`
14. `attribution-p95-latency`
15. `cost-p95-latency`
16. `pipeline-drain`
17. `prometheus-convergence`
18. `grafana-dashboard`
19. `metadata-only-boundaries`

Every check is recomputed from the profile, environment, bindings, and
measurements. `qualified` is possible only when all 19 pass. A completed run
that misses an objective, cannot complete commit-bound replay before the shared
deadline, or detects concurrent local cohort contamination produces valid
`not-qualified` evidence. Unsafe input, an invalid profile identity, an
excessive workload, a hard representational-ceiling breach, or a generator
failure stops without creating promotable evidence.

The report ID is `afslq_` plus the first 32 lowercase hexadecimal characters
of SHA-256 over canonical JSON containing report `metadata` without `id` and
the complete `spec`. Offline verification recomputes both content identities,
the complete profile digest, all accounting, checks, summary, current-source
binding, the exact fixture and engine generations, the inspected running
application and Collector image identities' structural/content binding, and
the successful status without generating traffic. The default traffic-free
verifier does not independently attest that the reported application digest
identifies the image that ran; supplying a trusted expected digest through
optional `--image-digest` makes that exact application-image comparison.

## Privacy and nonclaims

The transportable report retains source and runtime identity, the fixed local
fixture policy/catalog IDs and versions, engine versions, aggregate counts,
stage percentiles, boolean boundary results, content digests, stable checks,
and seven fixed limitations. It never retains a tenant, service, model,
endpoint, hostname, credential, trace/span/request identity, token quantity,
policy or catalog source hash, catalog rate, monetary amount, prompt, response,
content, arbitrary telemetry label, request-level sample, or raw
provider/runtime error.

The fully priced cohort intentionally isolates throughput from functional
coverage cases. The existing `AiFinopsRuntimeCompatibilityReport` remains the
owner of partial usage, unpriced coverage, deterministic savings, content
rejection, and complete dashboard-answer correctness.

This report does not alter the 19-input `ReleaseReadinessReport`. Requiring it
for local release admission needs a new readiness semantic level rather than a
silent change to the existing closed evidence set. It also cannot substitute
for a customer-environment sustained AI workload or satisfy customer pilot
admission by itself.

# AI history availability report contract

**Status:** v1alpha1  
**Decision:** [ADR 0161](../decisions/0161-explicit-ai-history-availability.md)

`AiHistoryAvailabilityReport` distinguishes locally committed AI usage history
that remains readable from history with recorded whole-invocation retirement.
It is a read-only aggregate, not a retention policy, deletion operation, billing
report, or proof that all customer traffic was captured.

## Query and authority

`GET /v1/ai/economics/history-availability` requires Bearer authentication and
a positive `ai-economics:read` policy decision. Tenant identity comes only from
the authenticated actor; the request accepts exactly one `start` and one `end`
query value and no other parameters. A caller cannot select a tenant,
attribution policy, price catalog, engine version, or retention generation.

The positive half-open interval `[start, end)` is at most 31 days. Both values
are canonical UTC timestamps: whole seconds ending in `Z`, or exactly six
fractional digits when microseconds are nonzero. Offsets, duplicate parameters,
empty/reversed intervals, and noncanonical equivalent timestamps are invalid.
The interval selects invocation start time, not ingestion, calculation,
retirement, or report-generation time. Schema validation checks the closed
shape; semantic validation additionally enforces interval ordering and length.

## Report semantics

The envelope contains `metadata.tenantId` and `metadata.generatedAt`. Its
closed `spec` contains the exact requested `scope`, a `status`, and `coverage`:

| Field | Meaning |
| --- | --- |
| `retainedUsageRecords` | Live usage records in the interval, excluding any invocation with a recorded retirement marker, even if its payload is still present. |
| `retiredUsageRecords` | Recorded whole-invocation retirement markers in the interval, counted once per normalized usage identity. |
| `available` | `retiredUsageRecords` is exactly zero. |
| `history-retired` | `retiredUsageRecords` is greater than zero. |

Counters are integers between zero and `9007199254740991`. A single consistent
storage snapshot supplies both counts. A whole-invocation marker is keyed by
normalized usage identity, not by a claim of distinct physical provider calls:
separate telemetry channels can legitimately have records sharing one
trace/span correlation. Markers take precedence over any still-present usage
payload, so one usage identity cannot count as both retained and retired.
Retirement is recorded per usage identity, not inferred from a single
global date cutoff; this permits historical retained holes needed by a future
pin policy without claiming that such a policy is implemented.

An empty interval is `available` with both counters zero. Here, `available`
means only that locally committed usage has no recorded retirement in this
interval. It does not prove complete instrumentation, unsampled traffic,
successful Collector delivery, complete token meters, completed attribution or
cost processing, invoice agreement, or future availability. `generatedAt` is
the report-generation instant, not an intake-completeness watermark.

The report includes no monetary values, model/service/application/team
dimensions, usage/cost/finding identifiers, trace/span identifiers or digests,
retirement timestamps, policy digests, or retention cutoffs. The authenticated
tenant identifier and requested time scope are its only customer scope.

## Existing reads and retired history

Successful `AiAllocationReport` and `AiEconomicsInvocationObservation`
v1alpha1 envelopes remain unchanged. Allocation rejects an interval containing
any same-tenant retirement marker with HTTP `410` and stable error code
`ai.history.retired`, before applying its source-row limit or returning totals.
The owning allocation read and retirement check share one consistent snapshot;
a separate earlier availability request cannot authorize a later partial read.

The privileged exact-invocation operation checks the canonical tenant-bound
trace/span correlation digest for retirement before returning live records. A
matching marker produces the same `410 ai.history.retired`, even when its
payload remains physically stored. Retired usage must not become
`not-observed`, `processing`, or a partial `complete` response. Missing active
attribution/cost results on genuinely retained usage keep their existing
pending semantics.

Savings-cohort reads likewise reject any same-tenant retirement marker in the
requested interval before narrowing provider/model scope or computing a
cohort. This deliberately conservative guard prevents recommendations and
derived allocation snapshots from silently using partial historical input.
Existing immutable finding-page responses do not dereference their evidence;
this addition does not establish a finding evidence-retention policy.

## Errors and lifecycle limits

Malformed query input returns `400 request.invalid`; missing/invalid
authentication returns `401`; policy denial returns `403 policy.denied`;
storage failure returns `503 storage.unavailable`. A valid availability
report is HTTP `200` even when its status is `history-retired`. The `410`
response belongs to the existing reads that require the retired input, not
this aggregate observation endpoint. External errors contain no provider,
database, record, or exception details.

This unit creates no payload-deletion path, marker-writing public or worker
port, scheduler, configurable AI retention duration, or general tombstone or
erasure implementation. Source payloads are not physically removed. A future
lifecycle must separately prove atomic retirement, protected references and
finding pins, foreign-key handling, exact retry/conflict identity, event replay,
and backup/restore behavior. Repricing and reattribution cannot reconstruct
retired source payloads from this report or its markers.

## Machine contracts and operations

- `contracts/schemas/ai-history-availability-report.schema.json`
- `contracts/examples/ai-history-availability-report.json`
- [AI history availability runbook](../operations/ai-history-availability.md)

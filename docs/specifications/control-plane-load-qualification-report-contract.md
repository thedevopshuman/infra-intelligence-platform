# Control-plane load qualification report contract

**Status:** Accepted v1alpha1 contract

**Schema:** [`control-plane-load-qualification-report.schema.json`](../../contracts/schemas/control-plane-load-qualification-report.schema.json)

**Example:** [`control-plane-load-qualification-report.json`](../../contracts/examples/control-plane-load-qualification-report.json)

## Purpose

`ControlPlaneLoadQualificationReport` retains privacy-minimized evidence that
one exact release served a bounded fixed-rate authenticated read workload
through one verified customer HTTPS ingress path.

The profile answers a deliberately narrow capacity question: can the deployed
control-plane identity endpoint sustain the declared request rate, success
objective, and latency objectives without the load generator hiding missed
schedule slots? It complements customer ingress and continuity evidence; it
does not replace either.

## Closed profile

`customer-control-plane-read-load-v1` requires:

- a clean source revision matching the release-mode runtime identity;
- the exact immutable image digest and repository application, chart, contract,
  and migration identity;
- direct verified HTTPS with redirects and ambient proxies disabled;
- a bounded non-symlink credential file used only for authenticated
  `GET /v1/system/version` requests;
- a duration from 300 through 3,600 seconds, a target rate from 1 through 250
  requests per second, and concurrency from 1 through 128;
- no more than 250,000 scheduled requests in one run;
- an explicit successful-request objective, scheduler-miss objective, and p95
  and p99 latency objectives; and
- the literal `--allow-traffic` enable flag before any request is sent.

The generator assigns one global monotonic fixed-rate schedule across bounded
worker threads. A request slot that is later than the configured scheduler-lag
limit is counted as missed instead of being emitted as a catch-up burst.
`attemptedRequests + schedulerMissedRequests` therefore always equals the
declared target request count.

## Measurements and status

The report records only aggregate counts, integer basis points, integer
millisecond percentiles, failure-category counts, and pseudonymous SHA-256
bindings. Successful-request attainment uses the scheduled request count as
its denominator, so scheduler misses cannot improve the result. Request
latency percentiles include only responses that passed HTTP, response-contract,
and exact runtime-identity validation; failed requests remain visible through
closed categories.

A report is `qualified` only when all 12 derived checks pass. The semantic
validator recomputes target volume, accounting, basis points, throughput,
ordered percentiles, checks, summary, limitations, and content-derived report
ID. Offline verification additionally rebinds the report to the current clean
checkout, supplied HTTPS base URL, and exact image digest.

Invalid inputs, excessive request volume, unreadable credentials, an invalid
CA, a dirty source tree, or generator failure stop before a report is written.
A completed workload that misses its scheduler, success, identity, or latency
objective produces valid `not-qualified` evidence.

## Data minimization

The report never retains the URL, hostname, IP address, access token,
certificate, response body, tenant, actor, request identifier, trace, raw
exception, or per-request sample. The URL contributes only to a target binding
digest. That digest remains pseudonymous operational evidence and should be
protected with the other customer qualification records.

## Boundary and nonclaims

This is environment-scoped operational evidence, not a control-plane resource.
It has no OpenAPI route and grants no application, SDK, agent, or plugin
authority. Python and TypeScript expose the envelope for qualification tooling
only.

One successful run qualifies one identity read endpoint, one ingress path, one
release, one fixed-rate objective, and one observation window. It does not
qualify resource or investigation reads, writes, database capacity, background
workers, OTLP receivers, ordinary mixed customer traffic, failover, regional
availability, long-window SLO attainment, or production promotion.

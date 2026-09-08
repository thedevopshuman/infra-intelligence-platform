# Local AI FinOps sustained-load qualification

**Status:** Standalone, explicitly invoked local qualification

This gate measures one bounded fixed-rate synthetic AI economics workload
through the disposable Docker reference topology. It exercises both accepted
provider shapes through the OpenTelemetry Collector, isolated receiver,
PostgreSQL usage ledger, asynchronous attribution and cost processing,
Prometheus aggregates, and Grafana dashboard without sending prompts,
responses, or live provider requests.

It is deliberately separate from the short deterministic compatibility
fixture. The checked-in `local-ai-finops-sustained-load-v1` profile fixes the
duration, rate, concurrency, replay fraction, scheduler and drain limits, and
closed completion and latency objectives. The scheduler does not replay late
slots as a catch-up burst, and all original-flow attainment ratios use
scheduled work as their denominator. The semantic maximum is 9,984 sustained
spans because this wrapper first commits the 15-record compatibility fixture and
the rolling allocation exporter fails closed above 10,000 source records.
Replay uses the newest durable tail in original schedule order, so even the
longest 1,800-second profile stays within the receiver's 900-second span-age
limit. Replay begins only after exact original durability
(`usageRecordsBeforeReplay = attemptedSpans`), is paced at the
original total span rate, and starts no new send at or after the shared drain
deadline. It sends exact payloads directly to the isolated receiver with the
matching fixed fixture channel, so every acknowledgement counted as successful
is PostgreSQL commit-bound and observed by the deadline. Explicit rejection,
timeout, or a later acknowledgement is unacknowledged for qualification. An
in-flight client gets at most one request timeout to shut down, and that time is
included in the reported duration; incomplete original durability suppresses
replay and records zero scheduled replay work.

It demonstrates unchanged usage-ledger count, while attribution and cost
measurements demonstrate final completion rather than replay-generation
stability.

## Run the Docker gate

Docker Desktop must be running. Install the normal verification dependencies,
then run:

```bash
make test-ai-finops-sustained-load PYTHON=.venv/bin/python
```

The command uses the checked-in profile at
`contracts/examples/ai-finops-sustained-load-profile.json`, starts an isolated
Compose project, runs for at least the profile's five-minute measurement
window, waits for bounded pipeline drain and dashboard convergence, writes
`dist/ai-finops-sustained-load-qualification-report.json`, and removes its
containers and volumes when it exits.

The wrapper inspects the application and Collector images used by the running
Compose project rather than deriving identity from tags. The report fixes
attribution engine `0.1.0`, cost engine `0.2.0`, and the exact local fixture
attribution-policy and price-catalog IDs and versions. Database measurements
join only those generations inside the run's trace-prefix cohort.

To exercise another reviewed local profile or retain the report elsewhere:

```bash
IIP_AI_FINOPS_SUSTAINED_LOAD_PROFILE=/absolute/path/profile.json \
IIP_AI_FINOPS_SUSTAINED_LOAD_REPORT=/absolute/path/report.json \
  make test-ai-finops-sustained-load PYTHON=.venv/bin/python
```

The profile remains configuration rather than authority: it contains no
endpoint, credential, tenant, provider account, model, price, or customer
identity. Workload generation is enabled only inside the disposable local
runner and is never an API, SDK, agent, or plugin capability.

## Verify retained evidence

Verification is traffic-free. From the exact clean source revision that
produced the report, run:

```bash
IIP_AI_FINOPS_SUSTAINED_LOAD_PROFILE=/absolute/path/profile.json \
IIP_AI_FINOPS_SUSTAINED_LOAD_REPORT=/absolute/path/report.json \
  make verify-ai-finops-sustained-load-report PYTHON=.venv/bin/python
```

The verifier revalidates both schemas, the content-derived profile and report
identities, profile digest, source revision, application, attribution-engine,
cost-engine, fixture-policy, and fixture-catalog versions, both reported image
identities, workload and objective echo, ordered checks, aggregate arithmetic,
and the closed `qualified` status. It does not reconnect to Docker, PostgreSQL,
Prometheus, Grafana, or a provider. The default command therefore structurally
and content-binds the reported image digests but does not independently attest
which application image ran. A caller holding a trusted expected application
digest can add `--image-digest sha256:...` directly to the verifier CLI for an
exact comparison.

## What `qualified` means

`qualified` proves only that one exact source revision met the checked-in
single-host synthetic profile. The minimized report retains aggregate
scheduler, acceptance, persistence, attribution, cost, latency, sampled backlog,
drain, usage-ledger replay, and dashboard results. `pipeline-drain` requires at
least one successful in-workload backlog sample, excludes the final snapshot
from that sample count, and requires zero final attribution and cost backlog. It
also requires replay to stay inside the shared deadline. The Prometheus check
requires exact aggregate convergence and `cohortIsolationPreserved`: no other
`local`-tenant usage row created at or after the database marker may carry a
trace ID outside this run's 96-bit prefix. This protects the global metric
delta from a second durable local workload; it does not claim arbitrary
host/process isolation. The envelope's 2,400,000-millisecond overall and
2,200,000-millisecond stage-latency ceilings keep bounded completed objective
misses representable and are not performance objectives. The report does not
retain tenant, service, model, invocation, trace/span, token, policy/catalog
source hash, price, amount, endpoint, credential, prompt, or response values.

The result is not evidence of customer workload representativeness, live
provider behavior, customer Collector/PKI/network behavior, private prices or
invoice agreement, burst tolerance, injected failure recovery, backend
lifecycle, long-window SLO attainment, or node, zone, and regional HA.

This initial profile is intentionally outside `make verify`,
`make qualify-local-release`, and the 19-input
`ReleaseReadinessReport` `v1alpha1`. It must be calibrated on the supported
`linux/amd64` and `linux/arm64` release environments before it can become a
mandatory release policy. That future promotion requires an explicit
`ReleaseReadinessReport` v2 contract and migration; this change does not
implement or imply it.

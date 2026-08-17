# Durable workflow workers

The API can execute the compatible synchronous endpoint or durably submit background work through `POST /v1/investigation-jobs`. The same worker process also closes expired governed-action execution leases without invoking an executor. The Docker Desktop stack starts one workflow worker for tenant `local`; the Helm worker is opt-in and requires explicit tenant enrollment.

Platform administrators can query `GET /v1/operations/investigations/completion-slo` for an aggregate exact-tenant useful-completion objective. Configure it with `IIP_INVESTIGATION_COMPLETION_SLO_WINDOW_SECONDS`, `IIP_INVESTIGATION_COMPLETION_SLO_MAXIMUM_SECONDS`, `IIP_INVESTIGATION_COMPLETION_SLO_MINIMUM_ATTAINMENT_BASIS_POINTS`, and `IIP_INVESTIGATION_COMPLETION_SLO_MINIMUM_ELIGIBLE_JOBS`. Only durable asynchronous jobs are measured. On-time `completed` jobs succeed; late completion, failure, cancellation, and unfinished mature jobs are misses. Retain job rows for at least the configured window.

## Required configuration

- `IIP_DATABASE_URL`: the same migrated PostgreSQL database as the API.
- `IIP_WORKER_TENANTS`: comma-separated exact tenant IDs. There is no wildcard or implicit all-tenant mode.
- `IIP_WORKER_ID`: stable process identity, normally the pod name.
- `IIP_WORKER_LEASE_SECONDS`: 10–300, default 30.
- `IIP_WORKER_HEARTBEAT_SECONDS`: 0 for tests or less than the lease, default 10.
- `IIP_WORKER_RETRY_SECONDS`: 1–300, default 5.
- `IIP_WORKER_MAX_ATTEMPTS`: 1–20, default 8.
- `IIP_ACTION_RECONCILIATION_BATCH_SIZE`: 1–500 expired action leases per tenant pass, default 100.

The worker uses the same protected policy, evidence-backend, credential-broker, trust, and telemetry configuration as the API. It does not accept actor identity from worker configuration: the immutable accepted request retains the authenticated submitter.

## Operation

Run continuously with `python -m iip.surfaces.worker`. `--once` claims at most one investigation job and scans one bounded action-reconciliation batch for each configured tenant, then exits. SIGTERM stops polling and lets the current bounded operation return; provider calls retain their own deadlines.

Monitor queued age, attempts, heartbeat age, terminal state, and `lastErrorCode` from job status. An expired dispatch claim is safe to reclaim. A live investigation execution returns to the queue until its lease resolves; an expired execution becomes a failed immutable report without replaying evidence calls. Repeated `investigation.runtime.unavailable` or `investigation.retry.exhausted` requires provider/storage diagnosis before resubmitting with a new investigation ID.

Before promotion, run `make test-capacity` and retain its clean-revision aggregate report with release evidence. The default PostgreSQL profile measures large-tenant admission, overload rejection, cross-tenant isolation, live-lease caps, first-pass coverage, and terminal capacity release. Its local timing objectives are regression gates only; follow the [capacity certification runbook](investigation-capacity.md) for customer sizing boundaries.

Never broaden a worker by omitting tenant filters. Enroll tenants through reviewed deployment configuration and use workload identity plus the external credential broker for production evidence access.

Action reconciliation is deliberately non-executing. An expired `executing` lease becomes `manual-reconciliation-required` together with exactly one audit record. The worker does not compose a live action executor, receive Kubernetes action credentials, call an action adapter, retry impact, or change an immutable proposal/approval. The console derives `expired` for unexecuted proposals at read time; an operator must create a new proposal after re-investigating current provider state.

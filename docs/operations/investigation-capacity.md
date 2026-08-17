# Investigation dispatch capacity certification

`make test-capacity` starts a disposable PostgreSQL 18 database in Docker Desktop, applies every packaged migration, and runs the `postgresql-investigation-dispatch-v1` profile. The default profile concurrently exercises 128 tenants, four queued jobs per tenant, and 16 clients. It writes aggregate machine-readable evidence to `dist/investigation-capacity-report.json` and removes stale output before each run.

The gate proves:

- all 512 baseline jobs are admitted for all 128 exact tenants;
- a 16-job burst into a tenant capped at four accepts exactly four and rejects the rest with the stable capacity boundary;
- the four accepted identities remain idempotently retriable while the tenant is full;
- another tenant remains admissible during that overload;
- every baseline tenant receives a first-pass claim while a second live claim is denied at the configured cap of one;
- terminal claims release one outstanding slot and replacement work can use it;
- p95 repository-operation latency remains below five seconds and the complete local profile remains below two minutes.

The timing ceilings are regression tripwires, not advertised production SLOs. The report binds the measured values to the source revision and dirty state, application/Python version, host platform, PostgreSQL version, and latest migration. Only a clean-source report is release evidence.

## Customer qualification

Repeat the profile on the intended database class and network, then extend it with the customer's expected tenant count, arrival bursts, investigation budgets, provider latency distribution, worker replica count, high-availability topology, and failure/recovery cases. Record the profile and result with the release evidence. Do not increase `investigationQueue.maxOutstandingJobsPerTenant` to hide completion-SLO misses; it controls memory/storage exposure, not processing capacity.

The report contains no tenant identifiers, request bodies, evidence, provider output, database URL, credentials, or host name. Failed checks expose stable error codes only. A passing local profile does not certify database failover, autoscaling, regional operation, or a customer's workload.

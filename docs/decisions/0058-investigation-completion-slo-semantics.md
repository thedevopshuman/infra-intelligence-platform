# ADR 0058: Durable investigation-completion SLO windows

**Status:** Accepted

## Context

The control plane durably queues, leases, retries, cancels, and terminalizes asynchronous investigations, but operators cannot yet determine whether accepted investigations produce useful reports within a declared objective. Measuring only terminal latency would let fast failures appear healthy. Measuring worker attempts would over-count retries, and measuring in-process traces would exclude exporter failures and crash recovery.

## Decision

Measure one durable asynchronous investigation job once in a rolling, exact-tenant cohort. The configured window ends at evaluation time and uses the immutable queue acceptance time. A job becomes eligible only after its configured completion deadline has elapsed.

A job meets the objective only when it reaches the durable `completed` state at or before `queuedAt + maximumCompletionSeconds`. A later successful completion is a late miss. Durable `failed` and `cancelled` states are explicit misses, as are jobs that remain queued, running, or cancellation-requested at evaluation time. A terminal transition committed after the evaluation boundary is reconstructed as unfinished, which keeps concurrent evaluation conservative without misclassifying the storage state as corrupt. This prevents a fast failure or cancellation from being reported as useful completion. Retries do not add denominator entries.

Calculate attainment in integer basis points as `floor(withinObjectiveJobs * 10000 / eligibleJobs)`. Report `no-data` for an empty mature cohort, `insufficient-data` below the configured minimum sample, and otherwise `meeting` or `breached` against the configured minimum attainment. Validate tenant, window, counters, and partition arithmetic before returning a report.

Use the application-owned investigation-job repository and existing durable job timestamps. Require `platform-admin` plus policy approval, derive the tenant from authentication, and accept no caller-selected tenant or objective. Return aggregate counts only—never investigation IDs, requests, prompts, evidence, findings, worker identity, provider output, credentials, or exception text.

## Consequences

- retry and crash-recovery attempts cannot inflate the denominator;
- a quick failure cannot satisfy the useful-completion objective;
- customer cancellation remains visible as a conservative miss instead of silently disappearing;
- synchronous reference execution is outside this SLO because it does not cross the durable dispatch boundary;
- retained job rows must cover at least the configured window for a production claim;
- queue-time and execution-time percentile histograms, workload classes, regional objectives, long-term aggregation, and alert routing remain separate work.

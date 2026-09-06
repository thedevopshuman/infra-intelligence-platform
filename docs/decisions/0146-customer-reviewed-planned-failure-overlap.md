# ADR 0146: Qualify customer-reviewed planned failures inside sustained load

**Status:** Accepted

**Date:** 2026-09-10

## Context

ADR 0144 proves a bounded sustained mix of API identity reads, durable OTLP
metric writes, and asynchronous investigations. The customer control-plane,
worker/receiver, and PostgreSQL continuity qualifiers separately prove planned
pod evictions or an externally initiated primary promotion. Running those
qualifiers at different times does not prove that the sustained core path met
its objectives while those planned failures occurred.

The platform must not acquire ambient disruption authority merely to close this
evidence gap. It also cannot decide whether a synthetic traffic mix represents
a customer's business workload. Both the workload selection and permission to
perform planned failures belong to the customer qualification process.

## Decision

1. Add a protected `CustomerFailureOverlapProfile` that binds one exact clean
   release, the already-qualified customer deployment, the sustained workload
   profile, all selected target/environment digests, a customer approval-record
   digest, and bounded freshness, clock-skew, lead-in, recovery, and report
   objectives.
2. Fix the first profile to four customer-approved scenarios: API pod eviction,
   workflow-worker pod eviction, OTLP receiver pod eviction, and PostgreSQL
   primary promotion. Call the selected traffic a customer-approved
   private-pilot core proxy, not representative production traffic.
3. Keep generation of traffic and planned failures in the existing independent
   host-side qualifiers. The new assessor performs no traffic, Kubernetes
   mutation, database operation, provider call, or installation.
4. Require the control-plane, processing, and PostgreSQL qualification windows
   to begin after the selected deployment report and to fit inside one qualified
   sustained-workload window, with independently reviewed pre-failure and
   post-failure load periods.
5. Rebind every source file, the protected profiles, immutable release,
   cluster/context/namespace, API, OTLP, database, and processing/database
   profile digests. Crossed inputs fail without a credible report.
6. Retain only public release identity, timestamps, aggregate window lengths,
   stable results, and pseudonymous digests. Verification is traffic-free and
   requires every exact source artifact plus a clean matching checkout.
7. Require a current qualified overlap report as the ninth ordered source in
   the private-pilot readiness preflight. Bind that aggregate to the exact
   deployment and sustained-workload files already supplied to the preflight.

## Consequences

- A release owner can prove planned failure overlap without adding disruptive
  permissions to the API, worker, receiver, agent, plugin, Helm workloads, or
  SDKs.
- A customer approval record remains external; its digest makes profile review
  auditable without copying identities, tickets, or operating details into the
  transportable report.
- The resulting evidence is stronger than independent synthetic tests but
  remains limited to one cluster, selected targets, planned failures, and a
  private-pilot proxy workload.
- Automatic failover, fencing, split-brain prevention, involuntary node/zone/
  region loss, production volume, long-window SLOs, disaster recovery, and
  partner acceptance remain external gates.

## Alternatives considered

- Adding disruption commands to the sustained-load runner was rejected because
  it would combine traffic credentials with Kubernetes/database mutation
  authority and create a larger failure domain.
- Treating earlier independent continuity reports as overlap evidence was
  rejected because their timestamps need not intersect the sustained window.
- Calling a repository-defined mix representative production traffic was
  rejected because only the customer can approve that judgment.

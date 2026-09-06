# Customer continuity qualification report contract

**Status:** Accepted v1alpha1 contract  
**Schema:** [`customer-continuity-qualification-report.schema.json`](../../contracts/schemas/customer-continuity-qualification-report.schema.json)  
**Example:** [`customer-continuity-qualification-report.json`](../../contracts/examples/customer-continuity-qualification-report.json)

## Purpose

`CustomerContinuityQualificationReport` retains privacy-minimized evidence
that an exact IIP release remained available through its verified external
HTTPS ingress while Kubernetes evicted one ready control-plane API pod and the
Deployment restored redundant capacity.

The report composes two existing boundaries instead of weakening either one:
the direct, no-redirect `IngressAvailabilityQualificationReport` owns external
HTTP measurements and runtime identity, while the continuity harness owns one
explicit-context `policy/v1` Eviction and replacement observation. The
continuity report binds the exact ingress report by content digest.

## Closed profile

`customer-control-plane-pod-eviction-v1` requires:

- clean source matching the release-mode runtime revision;
- verified HTTPS using the system trust store or an explicit CA bundle;
- a credential read from a bounded non-symlink file and used only by the
  existing ingress probe;
- at least two fully ready API replicas, `maxUnavailable: 0`, a matching
  PodDisruptionBudget with `minAvailable >= 1`, and at least one disruption
  currently allowed;
- an exact immutable image digest on the API container;
- at least five minutes of scheduled probing, including 30 seconds before the
  Eviction and 30 seconds after replacement recovery; and
- declared availability, p95 cycle-latency, and replacement-recovery
  objectives.

The harness chooses one ready pod from the exact Deployment selector. It sends
an Eviction with a UID precondition, so a stale observation cannot target a
replacement pod. Every Kubernetes command names the supplied context and
namespace. This command has real disruption authority and therefore requires
the literal `--allow-disruption` flag; verification is offline and has no
Kubernetes authority.

## Status and failure semantics

A report is `qualified` only when all 16 closed checks pass. Availability or
latency misses in the completed ingress evidence, or a replacement that
exceeds the recovery objective, produce `not-qualified` evidence. Unsafe or
ambiguous preconditions, rejected Eviction, failure to recover within the
bounded observation timeout, malformed evidence, source drift, or an ingress
probe that ends before the disruption abort the run with a stable error and
do not create a continuity report.

The report does not convert a failed or local-loopback ingress report into
customer evidence. Offline verification requires the exact referenced
customer-ingress report and recomputes its file digest, identity, objective,
arithmetic, and time overlap.

## Data minimization

The report retains versions, aggregate counters, durations, and SHA-256
bindings. It does not retain the ingress URL, hostname, IP address, Kubernetes
context, namespace, Deployment or pod name/UID, selector, tenant, actor,
credential, certificate, response body, or raw error. Binding digests are
pseudonymous operational evidence and should be protected with other release
records.

## Boundary

This is environment-scoped release evidence, not a control-plane resource. It
has no OpenAPI route and grants no application or plugin authority. Python and
TypeScript expose a report type for qualification automation only.

One successful run qualifies one external route, one Kubernetes cluster, one
release, and one API-pod Eviction. It does not qualify database failure,
worker or OTLP receiver continuity, involuntary node loss, regional failover,
long-window SLO attainment, or production capacity under customer traffic.


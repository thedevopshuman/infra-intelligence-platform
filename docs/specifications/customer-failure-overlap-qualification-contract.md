# Customer failure-overlap qualification contract

**Status:** Accepted `v1alpha1` contract

**Decision:** [ADR 0146](../decisions/0146-customer-reviewed-planned-failure-overlap.md)

**Schemas:**
[`customer-failure-overlap-profile.schema.json`](../../contracts/schemas/customer-failure-overlap-profile.schema.json),
[`customer-failure-overlap-qualification-report.schema.json`](../../contracts/schemas/customer-failure-overlap-qualification-report.schema.json)

**Examples:**
[`customer-failure-overlap-profile.json`](../../contracts/examples/customer-failure-overlap-profile.json),
[`customer-failure-overlap-qualification-report.json`](../../contracts/examples/customer-failure-overlap-qualification-report.json)

## Purpose and authority boundary

`CustomerFailureOverlapProfile` is the protected customer review for one exact
private-pilot core workload proxy and four planned failure scenarios.
`CustomerFailureOverlapQualificationReport` is the minimized, transportable
result proving that the owning qualification windows occurred within the same
sustained-workload window and all source objectives passed.

The assessor is deliberately traffic-free. It cannot evict a pod, initiate a
database promotion, call an AI provider, install a release, or approve a pilot.
The existing host-side qualifiers retain their distinct traffic and disruption
authorities. SDKs expose only offline envelope types.

## Protected profile

The `cfop_<32 hex>` identifier covers all metadata except `id` plus the complete
specification. Operational use requires a regular, non-symlink, current-user
owned file with exact mode `0600`. Review and expiry may span no more than 90
days.

The profile binds:

- exact application/chart/contracts/migration/source/image identity;
- the exact customer deployment report and sustained-workload profile;
- cluster, Kubernetes context, namespace, API, OTLP, and database targets;
- the exact processing and PostgreSQL qualification profiles;
- an external customer approval-record digest; and
- review of data handling, recovery objectives, and the four fixed planned
  scenarios.

The customer approval means only that the selected synthetic core mix is a
suitable proxy for this private pilot. It is not repository-generated evidence
of representative production traffic.

## Source and window semantics

The assessor requires five current source artifacts:

1. `CustomerDeploymentQualificationReport`;
2. `CustomerSustainedWorkloadQualificationReport` plus its protected profile;
3. `CustomerContinuityQualificationReport` for the API pod eviction;
4. `CustomerProcessingContinuityQualificationReport` for workflow-worker and
   OTLP-receiver pod evictions; and
5. `CustomerPostgreSQLContinuityQualificationReport` for primary promotion.

All source reports must identify the same clean release. Their customer target,
context, namespace, and owning-profile digests must form one exact chain through
the protected overlap profile and deployment report.

The control-plane, processing, and PostgreSQL report windows must start after
deployment qualification and be enclosed by the sustained workload, subject to
the reviewed maximum clock skew. The first failure qualification must follow at
least `minimumPreFailureLoadSeconds` of sustained traffic; the last must finish
at least `minimumPostFailureLoadSeconds` before the sustained workload ends.
Each owning source validator independently proves that the planned disruption
occurred and its recovery objective passed.

## Result and verification

Five evidence results and 21 ordered checks determine `qualified` or
`not-qualified`. Unsuccessful, stale, expired, future, or dirty source evidence
is explicit. Malformed documents, crossed releases/targets/profiles, unsafe
files, and source-checkout mismatches fail closed without a credible result.

The report expires at the earliest current profile, profile-age, source-age,
sustained-report, or configured report-validity boundary. Offline verification
rebuilds the complete result from the exact profiles and source files at its
original assessment time, checks the current clean source revision, and emits
no traffic.

The report contains no customer/tenant identity, namespace value, endpoint,
credential, certificate, resource, metric, model, provider payload, request ID,
or per-request sample. Binding digests remain pseudonymous customer evidence
and require protected storage.

## Nonclaims

A qualified result covers one customer-approved private-pilot core proxy, one
release/environment, and planned disruptions only. It does not prove automatic
failover, fencing, split-brain prevention, zero data loss, involuntary failure,
regional availability, disaster recovery, representative production volume,
long-window SLOs, partner acceptance, or production approval.

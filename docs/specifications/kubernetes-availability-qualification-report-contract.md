# Kubernetes availability qualification report contract

**Status:** Accepted v1alpha1 contract
**Schema:** [`kubernetes-availability-qualification-report.schema.json`](../../contracts/schemas/kubernetes-availability-qualification-report.schema.json)
**Example:** [`kubernetes-availability-qualification-report.json`](../../contracts/examples/kubernetes-availability-qualification-report.json)

## Purpose

`KubernetesAvailabilityQualificationReport` retains minimized evidence that
the packaged API, workflow worker, and OTLP receiver preserve their intended
capacity, serving paths, durable metric intake, and workflow processing during
one voluntary worker-node drain in an isolated three-node Kind cluster.

The qualification is narrower than a production availability claim. It proves
the interaction between the chart's surge-first rollout policy, component
PodDisruptionBudgets, hard hostname topology spread, Kubernetes eviction, and
the API and OTLP Services, normalized receiver persistence, and durable worker
dispatch. It does not qualify involuntary node loss, shared database
availability, customer ingress, regional failure, or a long-window SLO.

## Qualification profile

`local-multi-node-kind-v2` has one tainted control-plane node and two worker
nodes. PostgreSQL and the independent probe are pinned to the control-plane
node; all three IIP workloads run two replicas spread across the workers.
Each Deployment must use `maxUnavailable: 0` and `maxSurge: 1`, each
component's PodDisruptionBudget must use `minAvailable: 1`, and each pod
template must use one exact-selector hostname constraint with `maxSkew: 1`,
`minDomains: 2`, and `DoNotSchedule`.

A report exists only for a qualified run. Before disruption, each component
has two ready replicas in two domains and one allowed disruption. While the
selected worker is cordoned and drained, each component has exactly one ready
replica, one unavailable replacement, one occupied domain, and no further
allowed disruption. After uncordon, all three components must return to the
baseline state.

## Probe semantics

The control-plane probe performs authenticated
`GET /v1/system/version` calls through the API Service. Every response must
match the exact application version, chart version, source revision, image
digest, contract version, and required migration under qualification.

The OTLP probe performs authenticated non-empty protobuf metric exports through
the receiver Service. Every payload uses the one allowlisted synthetic metric,
and HTTP 200 is returned only after the receiver has normalized and committed
the artifact to PostgreSQL. Before sampling, the harness also creates the
deterministic synthetic Resource and sends an initial metric; both must commit
successfully. Each of the `baseline`, `disruption`, and `recovery` phases
requires at least 20 successful attempts per probe and permits no failures.

After each topology phase is observed, the harness submits one bounded durable
investigation scoped to that synthetic Resource. The disruption submission
occurs only after every application pod has left the drained node. A phase
passes only when a workflow worker claims the request, records `completed`, and
the API returns its tenant-bound immutable report with a conclusive or
inconclusive outcome. The retained report includes only counts, poll attempts,
and completion milliseconds; it contains no investigation identifier or
report content.

## Data minimization and verification

Raw Kubernetes snapshots and probe state exist only in a mode-restricted
temporary directory and are deleted after the run. The retained report has no
cluster, namespace, node, pod, endpoint, IP, credential, response body, tenant
data, investigation identifier, or raw error. A cluster-binding digest and
target-node digest bind the observation without retaining those identifiers.

The report ID is derived from canonical report content after omitting only the
ID field. Offline verification checks the JSON Schema, source and subject
binding, exact component and phase ordering, expected state transitions,
probe arithmetic, closed checks and limitations, forbidden retained keys, and
the content-derived ID. `--require-clean` additionally binds the report to the
current clean checkout.

## SDK and transport boundary

Python and TypeScript expose an opaque/structural report type so release
automation can consume retained evidence without importing server internals.
The report is not a control-plane resource, is never accepted over the API,
and adds no OpenAPI route or Kubernetes authority.

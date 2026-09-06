# Kubernetes planned-disruption availability qualification

**Status:** Executable local release gate

This gate creates a fresh three-node Kind cluster, installs the current clean
source with one immutable image digest, drains one worker using the Kubernetes
Eviction API, continuously probes the API and commits OTLP metrics, completes a
durable investigation after each topology state, verifies recovery, retains
minimized evidence, and deletes only the cluster it created.

## Prerequisites

- Docker Desktop is running.
- `docker`, `kind`, `kubectl`, `helm`, `openssl`, `rg`, and the pinned Python
  verification environment are available.
- The checkout is clean. The default promotion profile refuses dirty source.
- Enough local capacity is available for three Kind nodes, PostgreSQL, six IIP
  replicas, a migration Job, and one small probe pod.

The harness does not enable or modify Docker Desktop Kubernetes. Kind runs its
own Kubernetes nodes as Docker containers and remains isolated from existing
contexts. The default cluster name is unique and begins with
`iip-availability-`; an existing name is always rejected.

## Run the gate

```bash
make qualify-kubernetes-availability PYTHON=.venv/bin/python
```

The run builds the current application image with its exact version and Git
revision, loads and digest-tags it in every Kind node, and installs the chart
with two API, two worker, and two receiver replicas. PostgreSQL and the probe
stay on the tainted control-plane node, so the selected worker disruption
targets all three application components without disrupting the measurement
process or its shared database.

The v2 processing profile sends a real allowlisted metric on every receiver
probe; receiver success therefore crosses the existing PostgreSQL
commit-before-HTTP-200 boundary. It also submits one bounded investigation
after baseline is stable, a second after the target node is fully drained, and
a third after all components recover. Each request must be claimed by a worker,
complete without failure or cancellation, and return its immutable report.
Service probes keep running while these jobs are processed.

The probe's two random 256-bit Bearer credentials are generated into a
mode-`0600` temporary directory. Only their SHA-256 verifiers enter the API and
receiver configuration. The raw credentials are mounted into the probe from a
temporary Kubernetes Secret and are never printed or retained in the report.
Receiver TLS is disabled only inside this disposable cluster; the separate
receiver compatibility gate owns mTLS, SPIFFE, chain, expiry, and CRL behavior.

On success the report is written to
`dist/kubernetes-availability-qualification-report.json`. The `dist/`
directory is ignored and the evidence remains outside the immutable release
bundle.

## Verify retained evidence

```bash
make verify-kubernetes-availability-report PYTHON=.venv/bin/python
```

Verification is offline and requires qualified evidence bound to the current
clean source, application, chart, and migration. It never reconnects to a
cluster.

Set `IIP_AVAILABILITY_KEEP_CLUSTER=true` only for deliberate local debugging;
the harness prints no credentials, and a kept cluster is not qualified
evidence after manual modification. Set
`IIP_AVAILABILITY_REQUIRE_CLEAN=false` only while developing the harness; the
result records `sourceDirty: true` and is not promotable.

## Failure and cleanup behavior

Every Kubernetes command uses the exact newly created `kind-<cluster>` context.
Cleanup is bounded to that owned Kind cluster and the harness's `mktemp`
directory. It does not delete namespaces from an existing cluster. Probe
failure, a blocked eviction, an unexpected replica/PDB/domain state, missing
recovery, source drift, or malformed evidence fails the gate.

This qualification covers one planned worker drain in a local single-region
topology with a shared PostgreSQL instance outside the disrupted node.
Production still requires the equivalent customer-cluster processing profile,
sustained workload and failure injection, database failover, storage and
regional recovery, and customer SLO evidence.

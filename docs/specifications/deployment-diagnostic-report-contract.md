# Deployment diagnostic report contract

**Status:** v1alpha1 executable point-in-time support evidence

`DeploymentDiagnosticReport` gives an operator one bounded, privacy-minimized
view of an explicitly selected IIP Helm release. It answers whether the
selected control-plane workload is currently available, whether any observed
worker or OTLP receiver rollout needs attention, whether pods expose restart,
unschedulable, or crash-loop signals, and whether all observed application
workloads use the expected chart, application version, and immutable image
digest.

The report is deliberately `point-in-time-support-only`. It is not an
availability SLO, a customer interoperability qualification, a security
assessment, or permission to promote a release.

## Explicit selection and minimization

The generator requires an explicit Kubernetes context, namespace, Helm release
name, and expected image digest. Those values form `targetBindingDigest`; the
context, namespace, release name, registry path, Kubernetes object names, pod
names, node names, labels, annotations, conditions, messages, logs, events,
URLs, credentials, and Secret data do not enter the report.

The command performs only these Kubernetes reads in the selected namespace:

- server version discovery;
- `list` Deployments carrying the exact Helm instance label; and
- `list` Pods carrying the exact Helm instance label.

It does not read Secret or ConfigMap values, inspect logs, execute inside a
pod, use an implicit current context, or mutate the cluster.

## Component states

The three closed component positions are `control-plane-api`,
`workflow-worker`, and `otlp-receiver`.

- `healthy` means the one selected Deployment has positive desired capacity,
  all desired replicas are current, updated, ready, and available, enough
  ready Pods are observed, and no selected Pod is unschedulable or crash
  looping.
- `progressing` means some replica remains available but the rollout has not
  converged.
- `unavailable` means the selected Deployment has no available replica.
- `not-observed` means no unique selected Deployment exists. The two optional
  components may be `not-observed` without implying that they were configured
  or expected. The API may not.

Each observed component separately reports `match`, `mismatch`, or `invalid`
identity. A valid identity comes only from the chart/application labels and
the named application container's immutable image reference.

## Overall status

`healthy` requires all nine checks to pass. A dirty diagnostic checkout,
optional workload rollout, or pod health signal produces
`attention-required`. Missing cluster access, invalid release selection,
unavailable API, or crossed/invalid workload identity produces `blocked`.

The semantic verifier recomputes every check, aggregate counter, and overall
status, then recomputes the target binding and expected repository identity
from the caller's explicit inputs. `--require-clean` additionally binds the
retained report to the current clean checkout. Verification does not requery
the cluster; rerun the generator for a current observation.

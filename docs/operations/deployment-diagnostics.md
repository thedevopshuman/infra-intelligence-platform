# Post-install deployment diagnostics

**Status:** Executable read-only support workflow

Use the deployment diagnostic after a Helm install or upgrade, or as the first
artifact in a support investigation. It summarizes the selected IIP
Deployments and Pods without collecting logs, Kubernetes object names,
customer identifiers, provider messages, URLs, credentials, or Secret values.

## Run the diagnostic

Use the same clean source revision as the installed release and the immutable
control-plane image digest from its verified release manifest:

```bash
IIP_KUBERNETES_CONTEXT=customer-production \
IIP_DEPLOYMENT_NAMESPACE=iip-system \
IIP_DIAGNOSTIC_RELEASE_NAME=iip \
IIP_DIAGNOSTIC_IMAGE_DIGEST=sha256:<64 lowercase hex characters> \
IIP_DEPLOYMENT_DIAGNOSTIC_REPORT=dist/deployment-diagnostic-report.json \
  make diagnose-deployment PYTHON=.venv/bin/python
```

The command always writes a schema-valid report when the target values and
local source identity are valid. It then returns nonzero unless the report is
`healthy` and the checkout is clean, so automation gets a fail-closed result
while the operator retains the evidence needed to troubleshoot.

The Kubernetes identity needs access to the non-resource `/version` endpoint
and namespace-scoped `list` on `deployments.apps` and `pods`. It needs no
Secret, ConfigMap, log, event, pod-exec, mutation, or cluster-wide authority.
The command never falls back to the current context.

## Verify a retained report

Use the same four target values to recompute its privacy-preserving binding:

```bash
IIP_KUBERNETES_CONTEXT=customer-production \
IIP_DEPLOYMENT_NAMESPACE=iip-system \
IIP_DIAGNOSTIC_RELEASE_NAME=iip \
IIP_DIAGNOSTIC_IMAGE_DIGEST=sha256:<64 lowercase hex characters> \
IIP_DEPLOYMENT_DIAGNOSTIC_REPORT=dist/deployment-diagnostic-report.json \
  make verify-deployment-diagnostic-report PYTHON=.venv/bin/python
```

Verification checks the JSON Schema, recomputes every status and aggregate,
recomputes the target binding, and requires the same clean source revision. It
does not claim the cluster is unchanged; generate a new report for a current
observation.

## Stable findings

| Error code | Meaning | First operator action |
| --- | --- | --- |
| `deployment-diagnostic.source.dirty` | The tool checkout contains changes | Rerun from the exact clean release source before retaining evidence. |
| `deployment-diagnostic.cluster.unavailable` | The selected API/version or bounded list calls failed | Check the explicit context, narrow RBAC, and API reachability. |
| `deployment-diagnostic.release.selection-invalid` | The API is absent, a component is duplicated, or an unknown IIP Deployment was selected | Check Helm release state and labels; do not broaden the selector. |
| `deployment-diagnostic.api.not-available` | The API rollout has no complete ready/available desired set | Inspect the customer monitoring system and Kubernetes events under local policy. |
| `deployment-diagnostic.workload.not-healthy` | An observed worker or receiver is progressing or unavailable | Check its rollout and dependency health before retrying. |
| `deployment-diagnostic.identity.mismatch-or-invalid` | An observed workload does not use the expected application/chart/image identity | Stop promotion and reconcile to the verified immutable release. |
| `deployment-diagnostic.pods.attention-required` | Restarts, unschedulable Pods, or crash-looping containers were observed | Use the customer's approved event/log workflow; the report intentionally omits raw detail. |

`not-observed` for the worker or receiver is neutral because those components
are optional. Compare it with the protected values and preflight report before
deciding whether absence is intended.

## Boundary

This snapshot complements, but does not replace, the customer deployment
preflight, external ingress qualification, PostgreSQL recovery evidence,
identity/policy/credential interoperability, OTLP/PKI qualification, capacity
testing, or long-window SLO monitoring. Do not use `healthy` as a production
certification or availability claim.

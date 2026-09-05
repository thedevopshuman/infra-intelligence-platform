# Customer deployment preflight

**Status:** Executable production-profile gate

The preflight catches incomplete production values and missing Kubernetes
dependencies before Helm applies a workload. It supports the read-only core
and AI FinOps V0 profiles. A pass is necessary for a customer installation but
is not production certification.

## Start from the non-secret profiles

The chart ships two composable examples:

- [`production-core.values.yaml`](../../deploy/helm/infra-intelligence/examples/production-core.values.yaml)
  enables the redundant API/worker, external identity/policy/credential
  boundaries, live evidence adapters, telemetry, TLS ingress, retention,
  backup, disruption, and least-authority network settings.
- [`production-ai-finops.values.yaml`](../../deploy/helm/infra-intelligence/examples/production-ai-finops.values.yaml)
  is an overlay that adds metadata-only AI usage intake, mutual-SPIFFE TLS and
  CRL references, redundant receiver replicas, attribution, pricing, saving,
  allocation, and Collector queue/loss monitoring.

Copy and merge the required settings into a protected customer values file.
Replace every example registry, endpoint, identity, and CIDR. Values contain
only configuration and object references; create actual Secret values through
the customer's secret-management workflow. Never pass secrets through Helm
`--set`, a values file, or the preflight command.

The repository regression gate renders and verifies both static profiles:

```bash
make test-deployment-preflight PYTHON=.venv/bin/python
```

Static success produces `configuration-ready`: the complete chart rendered,
all production configuration checks passed, and the cluster checks are
explicitly `not-run`.

## Run against the intended cluster

Select an exact context; the live command never uses an implicit current
context. The namespace must already exist, and the external-secret controller
or customer operator must have created every referenced object. The backup PVC
must be `Bound`.

The invoking identity needs `get` only for the named namespace, Secrets,
ConfigMaps, and PVCs referenced by the rendered profile; it does not need
`list`, `watch`, mutation, pod execution, or cluster-wide authority. Run it
from a trusted operator workstation and remove any temporary qualification
binding after use. Exact Secret-key verification necessarily requires
Kubernetes `get` on each named Secret even though `kubectl` output is projected
before it crosses into the Python process.

```bash
IIP_DEPLOYMENT_PROFILE=production-core-v1 \
IIP_DEPLOYMENT_VALUES=/absolute/protected/customer.values.yaml \
IIP_DEPLOYMENT_NAMESPACE=iip-system \
IIP_KUBERNETES_CONTEXT=customer-production \
make preflight-deployment-live PYTHON=.venv/bin/python
```

For `production-ai-finops-v0`, merge the core and AI overlay into the one
protected values file passed through `IIP_DEPLOYMENT_VALUES`. A successful
cluster run writes `dist/customer-deployment-preflight-report.json` with
`install-ready`. Any unavailable cluster, inaccessible or missing object,
missing required Secret or ConfigMap key, or unbound PVC returns a stable
failure and writes `blocked` evidence when the chart could be rendered.

The Secret read makes `kubectl` emit only object UID, resource version, and key
names through a Kubernetes Go template; no data value crosses into the Python
or report process. The retained report contains only counts and digests; it
omits context, namespace, object names,
keys, endpoints, values paths, CIDRs, tenant IDs, and provider errors.

## Retain release evidence

Run preflight from the clean release checkout and verify that the report still
matches that checkout and exact values generation:

```bash
IIP_DEPLOYMENT_VALUES=/absolute/protected/customer.values.yaml \
IIP_DEPLOYMENT_NAMESPACE=iip-system \
make verify-deployment-preflight-report PYTHON=.venv/bin/python
```

The verifier requires `install-ready`, rejects a dirty or different source
revision, rerenders the chart, and recomputes the ordered values, sanitized
configuration, and dependency-reference digests. If the values, chart,
referenced objects, namespace, or cluster change, generate a new live report.
Store it beside—never inside—the immutable release bundle.

## Required follow-on qualification

Preflight intentionally leaves the following gates explicit:

- organizational artifact signing and trusted publication;
- real customer OIDC browser/issuer behavior;
- policy bundle and credential-broker correctness, rotation, and recovery;
- customer Collector, PKI, CRL lifecycle, buffering, and loss behavior;
- PostgreSQL HA, fencing, storage durability, PITR, and disaster recovery;
- workload-specific capacity plus clean, exact-release external ingress
  availability and latency evidence; use the
  [ingress qualification profile](ingress-availability-qualification.md);
- continuous regional SLO aggregation and customer alert routing;
- for AI FinOps, live Bedrock model/region/streaming behavior, authoritative
  prices, and workload-quality validation of any proposed saving.

Use the specialized compatibility and recovery reports for those boundaries.
Do not interpret `install-ready` as proof that an external service or customer
workload is production-ready.

## Failure and rollback

Preflight makes no cluster changes. Correct the protected values or create the
missing prerequisite through its owning controller, then rerun. Do not bypass
a failed check with local authentication, local policy, static credentials,
mutable image tags, disabled network policy, test AI fixtures, or copied
secrets. Because no install occurs, rollback is simply retaining the last
reviewed values and evidence generation.

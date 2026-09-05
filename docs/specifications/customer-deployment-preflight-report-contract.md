# Customer deployment preflight report contract

**Status:** Accepted v1alpha1 contract
**Schema:** [`customer-deployment-preflight-report.schema.json`](../../contracts/schemas/customer-deployment-preflight-report.schema.json)
**Example:** [`customer-deployment-preflight-report.json`](../../contracts/examples/customer-deployment-preflight-report.json)

## Purpose

`CustomerDeploymentPreflightReport` answers whether one closed production
profile is statically safe to configure and, when run against an explicitly
named Kubernetes context, whether every referenced Secret key, ConfigMap, and
backup claim exists before installation. It does not certify the external
issuer, policy, credential, telemetry, database, provider, or workload behind
those references.

The report is source-bound and configuration-bound. It records the exact
application and chart versions plus SHA-256 digests of the ordered values
files, sanitized rendered deployment profile, namespace, cluster binding, and
dependency observations. It never records a values filename, Kubernetes
context, namespace, dependency name, endpoint, CIDR, tenant, or secret value.

## Profiles

- `production-core-v1` requires immutable image selection, redundant API and
  worker replicas, explicit worker enrollment, external PostgreSQL with
  controlled migrations, OIDC, external policy and credential broker, TLS
  ingress, least-authority network policy, a disruption budget, scheduled
  backup, evidence retention, outbound OTLP telemetry, and live metric, log,
  and Kubernetes Event evidence backends.
- `production-ai-finops-v0` adds tenant-bound AI usage intake, redundant
  mutual-SPIFFE receiver replicas with a current-CRL reference, attribution,
  pricing, saving, allocation reporting, and the customer-Collector queue/loss
  objective. Test pricing and attribution fixtures are forbidden in both
  production profiles.

The check identifiers and order are closed. Adding a required check is an
additive contract and profile revision; weakening a named profile requires a
new profile name and decision record.

## Modes and status

- `static` renders the complete chart and evaluates its sanitized deployment
  profile. Cluster and dependency checks are `not-run`. If every static check
  passes, status is `configuration-ready`.
- `cluster` requires an explicit context and namespace. It verifies API access,
  binds the report to the namespace UID and Kubernetes version, and checks the
  existence and exact required key set of every referenced dependency. A PVC
  must be `Bound`. Only a fully passing cluster run is `install-ready`.
- Any failed check makes the report `blocked`.

`install-ready` means only that the chart configuration and prerequisite
objects passed this pre-install gate. `qualificationBoundary` is permanently
`pre-install-only`, and `customerQualificationRequired` enumerates the
interoperability, resilience, signing, and workload evidence that this report
cannot establish.

## Dependency minimization

The Helm chart emits a non-secret deployment-profile ConfigMap. It contains
configuration modes, enablement flags, replica counts, and Kubernetes object
references already visible in rendered workload manifests. The preflight
process uses those references but retains only:

- configured, observed, missing, invalid, and unavailable counts;
- a digest of exact intended references and required keys; and
- for a cluster run, a digest of observed object UIDs, resource versions,
  required-key intersections, and PVC phase.

Secret commands make `kubectl` emit only metadata and key names across the
subprocess boundary; the orchestration process and report never receive data
values. A successful empty `--ignore-not-found` result is counted as missing;
authorization, tool, transport, or malformed-response failures are counted as
unavailable. Missing keys and unbound claims are invalid. All use stable
aggregate results without provider or Kubernetes error text.

## Verification

The offline verifier derives status and summary counts, validates the exact
profile check and external-requirement sequences plus stable result codes,
recomputes the content-derived identifier over all minimized evidence, binds
the report to the current revision and dirty state, rerenders the current
chart, and recomputes every static check plus the ordered values,
configuration, and dependency-binding digests. `--require-clean` rejects
uncommitted source. `--require-install-ready` additionally rejects static,
blocked, or otherwise incomplete evidence.

The cluster is intentionally not re-read by offline verification. A changed
cluster or dependency set requires a fresh cluster-mode report.

## Compatibility

This additive `v1alpha1` report does not change the release qualification,
runtime identity, Helm values, or external compatibility contracts. It may be
stored beside release and customer-environment evidence but must not be placed
inside or used to rewrite an immutable release bundle.

# Customer deployment preflight report contract

**Status:** Accepted v1alpha1 contract
**Schema:** [`customer-deployment-preflight-report.schema.json`](../../contracts/schemas/customer-deployment-preflight-report.schema.json)
**Example:** [`customer-deployment-preflight-report.json`](../../contracts/examples/customer-deployment-preflight-report.json)

## Purpose

`CustomerDeploymentPreflightReport` answers whether one closed production
profile is statically safe to configure and, when run against an explicitly
named Kubernetes context, whether every referenced Secret key, ConfigMap, and
backup claim exists before installation. It also checks read-only discovery of
the configured `PrometheusRule` API and target rule namespace. It does not
certify the external issuer, policy, credential, telemetry, database, provider,
or workload behind those references.

The report is source-bound and configuration-bound. It records the exact
application and chart versions plus SHA-256 digests of the ordered values
files, sanitized rendered deployment profile, namespace, cluster binding, and
dependency observations. It never records a values filename, Kubernetes
context, namespace, dependency name, endpoint, CIDR, tenant, or secret value.

## Profiles

- `production-core-v2` requires immutable image selection, redundant API and
  worker replicas, explicit worker enrollment, external PostgreSQL with
  hostname-verified TLS, an exact customer-owned CA Secret/key dependency, and
  controlled migrations. It also requires OIDC, external policy and credential
  broker, TLS ingress, least-authority network policy, component-specific
  disruption budgets, hard two-domain single-skew topology spreading,
  scheduled backup, evidence retention, outbound OTLP telemetry, and live
  metric, log, and Kubernetes Event evidence backends. It requires the
  supported bounded operational-alert policy, explicit rule-selector labels,
  and target namespace.
- `production-ai-finops-v1` adds tenant-bound AI usage intake, redundant
  mutual-SPIFFE receiver replicas with their own disruption budget and a
  current-CRL reference, attribution, pricing, saving, allocation reporting,
  and the customer-Collector queue/loss objective. Test pricing, attribution,
  and model-suitability fixtures are forbidden in both production profiles.

Generation accepts only these current profiles. Offline contract validation
continues to accept historical `production-core-v1` (26 checks) and
`production-ai-finops-v0` (32 checks) reports. Those historical shapes omit
`database-transport-security` and cannot satisfy a current production
preflight. Current core and AI reports contain 27 and 33 checks respectively.

The check identifiers and order are closed. `pod-disruption-budget` covers the
API, worker, and every enabled receiver; it accepts only a minimum that both
preserves one replica and permits one voluntary disruption.
`hard-topology-spread` requires the chart-generated component selectors,
`maxSkew: 1`, `minDomains: 2`, and `DoNotSchedule`. Adding a required check is
an additive contract and profile revision; weakening a named profile requires
a new profile name and decision record.

`database-transport-security` follows `external-database` in both current
profiles. It passes only when the sanitized rendered profile declares
`database.transportSecurity.mode: verify-full`, supplies non-empty
`caExistingSecret` and `caKey` references, and contains the exact
`database-ca` Secret dependency with that name and sole required key. The
preflight never reads or retains the CA value. This check proves only the
declared packaged-client transport posture; certificate validity, rotation,
database availability, and continuity remain customer qualification concerns.

`operational-alert-policy` requires metric export plus the fixed
`monitoring.coreos.com/v1` and
`otel-prometheus-underscore-no-suffix-v1` profiles, a resolved valid namespace,
at least one rule-selector label, and bounded component-heartbeat lookback and
pending durations. Helm additionally rejects a heartbeat lookback shorter than
two metric-export intervals or two exporter-health cycles. The live
`operational-alert-api` and
`operational-alert-namespace` checks use Kubernetes discovery and exact-name
lookup only. They do not prove that a Prometheus instance selects or evaluates
the rules.

## Modes and status

- `static` renders the complete chart and evaluates its sanitized deployment
  profile. Cluster and dependency checks are `not-run`. If every static check
  passes, status is `configuration-ready`.
- `cluster` requires an explicit context and namespace. It verifies API access,
  binds the report to the namespace UID and Kubernetes version, and checks the
  existence and exact required key set of every referenced dependency. A PVC
  must be `Bound`. It also requires the namespaced PrometheusRule API and the
  configured rule namespace to exist. Only a fully passing cluster run is
  `install-ready`.
- Any failed check makes the report `blocked`.

`install-ready` means only that the chart configuration and prerequisite
objects passed this pre-install gate. `qualificationBoundary` is permanently
`pre-install-only`, and `customerQualificationRequired` enumerates release
signing and vulnerability qualification plus the interoperability, resilience,
and workload evidence that this report
cannot establish. The existing `customer-workload-slo` requirement includes
the separate clean-release external
[ingress availability qualification](ingress-availability-qualification-report-contract.md);
preflight cannot satisfy it from chart values or cluster objects.
`customer-operational-alert-routing` separately preserves Prometheus rule
selection/evaluation, independent Collector/backend monitoring, Alertmanager
routing, contact delivery, recovery, and escalation as customer-owned live
evidence.

## Dependency minimization

The Helm chart emits a non-secret deployment-profile ConfigMap. It contains
configuration modes, enablement flags, replica counts, and Kubernetes object
references already visible in rendered workload manifests, including the
database CA Secret name and required key but never its value. The operational
alert section also contains its fixed profiles, resolved namespace, and only
the selector-label count—not label values. The preflight
process uses those references but retains only:

- configured, observed, missing, invalid, and unavailable counts;
- a digest of exact intended references and required keys; and
- for a cluster run, a digest of observed object UIDs, resource versions,
  required-key intersections, and PVC phase.

Secret commands make `kubectl` emit only metadata and key names across the
subprocess boundary; the orchestration process and report never receive data
values. Alert discovery is bounded to the exact group/version and checks only
resource name, kind, and namespaced scope; namespace lookup emits only its UID,
which is not retained. A successful empty `--ignore-not-found` result is
counted as missing; authorization, tool, transport, or malformed-response
failures are counted as unavailable. Missing keys and unbound claims are
invalid. All use stable aggregate results without provider or Kubernetes error
text.

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

This additive `v1alpha1` revision advances generation to the v2/v1 profiles and
retains offline schema and semantic validation for historical v1/v0 reports.
It strengthens the production Helm values and preflight checks without
changing release qualification, runtime identity, or external compatibility
contracts. It may be stored beside release and customer-environment evidence
but must not be placed inside or used to rewrite an immutable release bundle.

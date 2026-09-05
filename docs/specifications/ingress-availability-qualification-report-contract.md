# Ingress availability qualification report contract

**Status:** Accepted v1alpha1 contract
**Schema:** [`ingress-availability-qualification-report.schema.json`](../../contracts/schemas/ingress-availability-qualification-report.schema.json)
**Example:** [`ingress-availability-qualification-report.json`](../../contracts/examples/ingress-availability-qualification-report.json)

## Purpose

`IngressAvailabilityQualificationReport` retains aggregate evidence that an
external client reached one IIP deployment through its intended ingress,
observed process liveness and dependency readiness, authenticated to the
control plane, and received the exact expected runtime identity within a
declared availability and latency objective.

The probe uses only `GET /healthz`, `GET /readyz`, and authenticated
`GET /v1/system/version`. It never invokes provider integrations or mutation
routes. A sample succeeds only when all three responses are successful and
contract-valid and the runtime identity matches the selected profile.

## Qualification levels

- `local-loopback` permits plain HTTP only for `localhost`, `127.0.0.1`, or
  `::1`, and requires a development runtime identity. It proves the harness
  and local route composition, not a customer ingress or release.
- `customer-ingress` requires HTTPS verified with the system trust store or an
  explicit CA file, a release-mode runtime, the current clean source revision,
  the current application and chart versions, and an explicitly expected
  immutable image digest. At least 100 samples are required.

The client disables ambient HTTP proxies and redirects. The Bearer credential
is read from a bounded file and is attached only to the runtime-identity
request. It is never accepted through an argument or environment variable.

## Measurements and status

The objective declares sample count, availability in integer basis points,
maximum successful-cycle p95 latency, per-request timeout, and interval.
Measurements retain only aggregate successes/failures, integer p95 values,
and closed failure-category counts. A cycle with any unavailable, malformed,
redirected, unauthenticated, or identity-mismatched response is a failed
sample. No successful cycle means p95 is `null`.

The report is `qualified` only when all ten closed checks pass. Customer
qualification additionally requires clean source, verified HTTPS, at least
100 samples, release identity, and an immutable image digest. A local report
can never be promoted to customer evidence by editing its fields.

## Data minimization and binding

The report contains no URL, hostname, IP address, certificate, tenant or actor
identity, credential, response body, resource data, HTTP header, or raw error.
`targetBindingDigest` is SHA-256 over the canonical base URL and distinguishes
the measured target without retaining it. Treat the digest as pseudonymous
deployment evidence rather than a secret or a public identifier.

The content-derived report ID binds metadata and the complete specification.
Offline verification checks exact keys, arithmetic, check order and error
codes, identity/profile relationships, objective results, report ID, and the
current Git state. `--require-clean` and `--require-qualified` are promotion
gates; they do not recontact the target.

## Boundary

This report is environment-scoped operational evidence, not a control-plane
resource. It remains outside OpenAPI and the public SDKs and is stored beside,
never inside, the immutable release bundle. One bounded run does not establish
long-term or regional SLO attainment, alert delivery, DNS/certificate expiry
monitoring, or customer workload capacity.

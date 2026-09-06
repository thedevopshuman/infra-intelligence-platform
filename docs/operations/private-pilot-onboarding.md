# Private-pilot onboarding and handoff

**Status:** Executable technical workflow; external ownership required

**Date:** 2026-09-06

This is the ordered handoff for the
[first usable private-pilot release](../product/private-pilot-v1.md). It does
not replace the owning runbooks and never turns a successful technical check
into customer acceptance or production approval.

## 0. Establish accountable owners

Before moving artifacts or credentials, create a customer-owned pilot plan
outside Git. Record:

- customer product, platform, security, identity, database, telemetry, and
  FinOps owners;
- platform release, support, and security owners;
- private support and security paths, participants, service hours, severity
  definitions, response objectives, escalation, and stop authority;
- approved Kubernetes context/namespace, data region, retention/deletion
  policy, cost ceiling, test model/operation, and planned-disruption window;
- success measures, exit date, rollback owner, and evidence custodian.

Never store those contacts, customer identifiers, credentials, private targets,
protected profiles, or live evidence in this repository. If an owner, private
channel, rollback path, or customer approval is missing, stop onboarding.

## 1. Freeze and verify one release

Build the clean candidate and retain its environment evidence using the
[release procedure](release-artifacts.md). A customer pilot must use the
organizationally published and signed image-index digests and verified customer
archive; an unsigned local candidate is development evidence only.

Verify the transported bundle before using any artifact:

```bash
IIP_RELEASE_BUNDLE=/absolute/path/to/iip-<version>-<revision> \
  make verify-release-bundle PYTHON=.venv/bin/python
```

The bundle includes a checksum-bound private-pilot operating-handoff archive.
Extract documentation into a new empty directory without elevated privileges;
do not execute content from an unverified archive.

## 2. Prepare customer-owned dependencies

Provision and review the boundaries named by the
[Helm deployment guide](helm-deployment.md): PostgreSQL, OIDC, external policy,
credential broker, TLS/ingress, Secret delivery, backup storage, a customer
OpenTelemetry Collector and backend, and—when enabled—Grafana and the
production price catalog. IIP does not provision customer identity, cloud
credentials, PKI roots, provider accounts, or a production telemetry backend.

Copy example profiles outside the repository, replace every placeholder, make
protected files owner-readable only, and review all authority tuples. Never
reuse local fixture identities, certificates, catalog data, prices, or example
digests in a customer environment.

## 3. Preflight before installation

Run the exact-context
[customer deployment preflight](customer-deployment-preflight.md). It must say
`install-ready` for the current clean source, chart, values digest, context,
namespace, and protected-values generation. This proves prerequisite presence,
not interoperability.

For the production profiles, it also requires the bounded alert policy and
proves that the configured PrometheusRule API and target namespace exist. It
does not prove Prometheus selection or notification delivery.

Install the verified chart by immutable image digest. Run migrations through
the chart-owned migration job; never start serving a newer runtime against an
unqualified schema. Confirm all three workloads report the expected release
identity and then retain one
[privacy-minimized deployment diagnostic](deployment-diagnostics.md).

## 4. Qualify the live boundaries

Run each owning workflow from the same clean release checkout and retain the
minimized reports outside Git. The required order is:

1. customer OIDC issuer and browser/session behavior;
2. customer external policy allow/deny cases;
3. customer credential-broker least-authority issuance and denials;
4. pinned customer Collector/OTLP delivery and queue/loss observation;
5. customer ingress/API continuity, worker/receiver continuity, and PostgreSQL
   promotion/recovery;
6. aggregate customer deployment qualification and a fresh diagnostic;
7. post-deployment bounded control-plane load;
8. customer-approved sustained core-workload and planned-failure overlap;
9. production price-catalog qualification, live Bedrock compatibility, AI
   FinOps prerequisite aggregation, and the same-invocation ledger-to-dashboard
   flow.

Optional repository, action-provider, or additional telemetry integrations
must pass their own qualification before the pilot relies on them. A passing
local fixture does not substitute for a selected customer system.

## 5. Assess pilot admission

Run the [customer pilot readiness workflow](customer-pilot-readiness.md) with
the exact nine evidence inputs and the approved protected profile. Confirm the
result is current and says `design-partner-candidate`. Independently record the
customer/platform go decision, accepted limitations, support readiness, cost
boundary, and change window in the external pilot plan.

No report in the repository authorizes provider spend, disruption, mutation,
credential issuance, publication, or production use. Those actions remain
with their named owners.

## 6. Operate the bounded pilot

- Pin the admitted digests and reject unreviewed upgrades or configuration
  drift.
- Route IIP operational and AI economics signals through the customer
  Collector; watch exporter delivery and Collector queue/loss, not just local
  acceptance.
- Install and test the optional [operational alert policy](operational-alerts.md)
  or an equivalent backend-native adapter, including one synthetic firing and
  recovery through the customer's private route.
- Review freshness, query/receiver availability, investigation completion, AI
  coverage, unknown pricing/attribution, and evidence-backed savings on the
  agreed cadence.
- Keep prompts/responses disabled and reject content-bearing GenAI spans.
- Retain protected profiles and environment reports under customer access and
  deletion policy; share only the minimized packet authorized for the private
  channel.
- Re-run affected qualification after a release, issuer/key/certificate,
  policy/catalog, Collector/backend, database, cluster, or protected-profile
  change.

Use the [feedback boundary](private-pilot-feedback.md) for weekly product review
and [SUPPORT.md](../../SUPPORT.md) for incident handoff.

## 7. Triage, rollback, and security

For a product symptom, collect a new deployment diagnostic and compare exact
release identity before collecting broader data. Use stable error codes and
report digests; do not copy raw Kubernetes events/logs, OTLP payloads, database
dumps, provider content, or secrets into tickets.

Pause new provider calls or pilot traffic at the customer application/traffic
owner when the cost or data boundary is uncertain. Disable IIP intake/export
independently; the instrumentation path is fail-open and must not become an
application availability dependency. Roll application versions only through a
qualified N-1 procedure, and do not reverse database migrations by deleting
data. Follow [SECURITY.md](../../SECURITY.md) for a suspected vulnerability.

## 8. Close or decommission

At the planned exit—or immediately when the stop authority decides—stop pilot
traffic, remove optional instrumentation, revoke workload/provider/session
credentials in their owning systems, remove IIP workloads through the approved
Helm process, and apply the customer data deletion/retention policy to the
database, backups, telemetry, profiles, and reports. Confirm that removing IIP
does not affect the customer application's provider path.

Complete the feedback exit review, inventory any retained legal/audit evidence,
and record the decision to expand, repeat, pause, or end. Do not describe a
private-pilot result as production or public-release certification.

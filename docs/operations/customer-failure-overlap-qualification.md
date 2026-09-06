# Customer failure-overlap qualification

**Status:** Executable, customer-approved private-pilot gate

This workflow proves that the already-qualified sustained core traffic window
enclosed planned API, worker/receiver, and PostgreSQL continuity qualifications.
The aggregator sends no traffic and performs no disruption. Run the existing
qualifiers concurrently under the customer's change process, then aggregate
their exact evidence.

## Safety and prerequisites

- Complete customer deployment qualification first.
- Have the customer approve the sustained workload profile as a suitable
  private-pilot core proxy, the four planned scenarios, data handling, recovery
  objectives, exact environment, and time window. Retain the approval record
  separately and place only its SHA-256 digest in the protected profile.
- Run every qualifier from the same clean source revision used for the installed
  immutable image. Synchronize qualification hosts to the reviewed clock-skew
  limit.
- Use each owning runbook's separate credentials, least authority, monitoring,
  explicit traffic/disruption switches, and rollback procedure.
- Do not combine Kubernetes, database, API, and receiver credentials in one
  process or file.

## Prepare the profile

Copy the example outside the repository and replace every illustrative digest,
release value, timestamp, and objective. `deploymentReportDigest` is the
SHA-256 digest of the exact customer deployment report. The remaining bindings
come from that report, the selected sustained profile, and the owning continuity
profiles/reports.

For example, calculate a source-file digest without copying its contents into
the profile preparation log:

```bash
sha256sum /evidence/customer-deployment-qualification-report.json \
  | awk '{print "sha256:" $1}'
```

On macOS, use `shasum -a 256` in place of `sha256sum`.

```bash
cp contracts/examples/customer-failure-overlap-profile.json \
  /secure/qualification/customer-failure-overlap.json
chmod 600 /secure/qualification/customer-failure-overlap.json
```

Remove `metadata.id`, calculate it, insert the returned value, and repeat until
the command returns the same ID:

```bash
PYTHONPATH=scripts:src:sdks/python/src .venv/bin/python \
  scripts/assess_customer_failure_overlap.py profile-id \
  --profile /secure/qualification/customer-failure-overlap.json
```

Changing any selection requires a new customer review and content-derived ID.

## Coordinate the evidence window

Start `make qualify-customer-sustained-workload` on its dedicated qualification
host. After the configured lead-in has elapsed, run these existing workflows
using their own protected inputs and explicit enable switches:

1. `make qualify-customer-continuity`
2. `make qualify-customer-processing-continuity`
3. `make qualify-customer-postgresql-continuity`

All three complete report windows must fit before the sustained runner's final
post-failure interval. The PostgreSQL qualifier observes a customer/operator
initiated promotion; it does not initiate one. If any source report is
`not-qualified`, preserve it for diagnosis and repeat only after a newly
approved window is scheduled.

## Assess and verify

```bash
IIP_CUSTOMER_FAILURE_OVERLAP_PROFILE=/secure/qualification/customer-failure-overlap.json \
IIP_CUSTOMER_SUSTAINED_WORKLOAD_PROFILE=/secure/qualification/customer-sustained-workload.json \
IIP_CUSTOMER_DEPLOYMENT_QUALIFICATION_REPORT=/evidence/customer-deployment-qualification-report.json \
IIP_CUSTOMER_SUSTAINED_WORKLOAD_REPORT=/evidence/customer-sustained-workload-qualification-report.json \
IIP_CONTINUITY_REPORT=/evidence/customer-continuity-qualification-report.json \
IIP_PROCESSING_REPORT=/evidence/customer-processing-continuity-qualification-report.json \
IIP_CUSTOMER_POSTGRESQL_REPORT=/evidence/customer-postgresql-continuity-qualification-report.json \
IIP_CUSTOMER_FAILURE_OVERLAP_REPORT=/evidence/customer-failure-overlap-qualification-report.json \
  make assess-customer-failure-overlap PYTHON=.venv/bin/python
```

A completed but unsuccessful or stale source produces minimized
`not-qualified` evidence and a nonzero exit. Crossed or unsafe input produces no
credible report.

From the same clean checkout, with both protected profiles current:

```bash
make verify-customer-failure-overlap-report PYTHON=.venv/bin/python
```

Verification performs no traffic. Retain the report with all exact source
artifacts, while storing the two protected profiles and external customer
approval record under their separate access policies.

## Interpretation

`qualified` means the selected customer-approved private-pilot core proxy met
its own objectives while all four planned failure scenarios and recovery
checks were exercised inside its window. It does not certify production load,
automatic failover/fencing, involuntary infrastructure loss, regional recovery,
long-window SLOs, design-partner acceptance, or production operation.

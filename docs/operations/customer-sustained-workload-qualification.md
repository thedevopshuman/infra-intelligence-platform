# Customer sustained workload qualification

**Status:** Executable, explicitly enabled customer-environment gate

This gate runs a bounded fixed-rate mix of exact-release API reads, durable OTLP
metric writes, and asynchronous investigations against one installed customer
release. Run it after customer deployment qualification and before using
sustained capacity as design-partner evidence.

## Safety and prerequisites

- Obtain customer approval for the exact environment, release, selected tenant,
  existing resource, metric, investigation budget, rates, concurrency,
  objectives, and time window.
- Run from the clean checkout used to build the installed immutable image.
- Keep independent ingress, API, receiver, worker, PostgreSQL, Collector, and
  cluster monitoring active. The report deliberately retains no raw telemetry.
- Use distinct, short-lived API and receiver Bearer credentials in regular
  non-symlink mode-`0600` files. The API actor needs only the selected tenant's
  resource read, runtime identity, investigation submit/status, and report read
  permissions. The receiver credential and mTLS identity must be bound to the
  selected metrics channel.
- Use a public client certificate and a protected mode-`0600` private key.
  Provide explicit CA files for private trust. There is no insecure TLS mode.
- Ensure the profile approval remains valid beyond the entire run plus intended
  report lifetime.

The checked example schedules 1,800 probe cycles and 15 investigations over 15
minutes. Every probe cycle makes one API request and one OTLP write. Begin with
that rate unless the customer capacity plan approves another bounded profile.

## Prepare the protected profile

Copy the example outside the repository, replace every example value, remove
the example `metadata.id`, and protect the file before calculating its ID:

```bash
cp contracts/examples/customer-sustained-workload-profile.json \
  /secure/qualification/customer-sustained-workload.json
chmod 600 /secure/qualification/customer-sustained-workload.json
```

After editing the complete profile, calculate and insert the content-derived
identifier:

```bash
PYTHONPATH=scripts .venv/bin/python \
  scripts/qualify_customer_sustained_workload.py profile-id \
  --profile /secure/qualification/customer-sustained-workload.json
```

Changing any protected selection invalidates the ID and requires review,
updated timestamps, and a new ID. The profile accepts direct HTTPS origins only;
do not add API paths, credentials, queries, or fragments.

## Run

```bash
IIP_CUSTOMER_SUSTAINED_WORKLOAD_ALLOW_TRAFFIC=true \
IIP_CUSTOMER_SUSTAINED_WORKLOAD_PROFILE=/secure/qualification/customer-sustained-workload.json \
IIP_CUSTOMER_SUSTAINED_WORKLOAD_API_TOKEN_FILE=/secure/temporary/iip-api-token \
IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_TOKEN_FILE=/secure/temporary/iip-otlp-token \
IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_CLIENT_CERT_FILE=/secure/pki/collector-client.crt \
IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_CLIENT_KEY_FILE=/secure/pki/collector-client.key \
IIP_CUSTOMER_SUSTAINED_WORKLOAD_API_CA_FILE=/secure/pki/api-ca.pem \
IIP_CUSTOMER_SUSTAINED_WORKLOAD_OTLP_CA_FILE=/secure/pki/receiver-ca.pem \
make qualify-customer-sustained-workload PYTHON=.venv/bin/python
```

The literal enable flag is checked before profile or credential I/O. The runner
also rejects path overlap, symlinks, permissive protected files, stale approval,
dirty source, release mismatch, invalid TLS material, excessive volume, and
combined concurrency above 96.

The result is written to
`dist/customer-sustained-workload-qualification-report.json`. A completed run
that misses an objective writes valid `not-qualified` evidence and exits
non-zero. An unsafe setup or generator failure does not create a report.

## Interpret the result

- Probe `successBasisPoints` uses all scheduled cycles, including scheduler
  misses. Each API success is an exact immutable-release identity response;
  each receiver success crosses the documented PostgreSQL commit boundary.
- Workflow completion uses all scheduled submissions. Rejected, failed,
  cancelled, timed-out, malformed, or scheduler-missed work reduces attainment.
- Scheduler misses mean the host could not issue the selected workload on time.
  The runner skips late slots instead of issuing a catch-up burst.
- Latency percentiles describe successful operations only. Diagnose them with
  aggregate failure counts and the independent customer telemetry kept outside
  this minimized report.
- `qualified` means all 22 checks passed for this exact selection and window.
  It does not mean the workload is representative without separate customer
  review.

## Verify retained evidence

From the same clean source checkout, with the protected profile still current:

```bash
IIP_CUSTOMER_SUSTAINED_WORKLOAD_PROFILE=/secure/qualification/customer-sustained-workload.json \
make verify-customer-sustained-workload-report PYTHON=.venv/bin/python
```

Verification sends no traffic and needs no credential or private key. It
recomputes schema and semantic invariants, content identities, expiry, source
and release identity, and both target digests. Retain the minimized report with
customer qualification evidence, store the protected profile separately, and
destroy temporary credentials according to customer policy.

## Nonclaims and follow-up

This first profile excludes provider/inference calls, UI traffic, arbitrary
queries, actions, failure injection, database promotion, Collector queue
recovery, node/zone/region failure, and long-window SLOs. Customer workload
representativeness, a failure-overlap profile, and production operating
acceptance remain separate qualification work.

# Customer control-plane load qualification

**Status:** Executable customer-environment gate

This gate applies a bounded fixed-rate authenticated read workload to the
runtime identity endpoint of one exact release through customer HTTPS ingress.
Run it after deployment preflight, post-install diagnostics, ingress
qualification, and customer continuity qualification have passed.

## Safety and prerequisites

- Obtain approval for the exact target, request rate, duration, concurrency,
  and observation window. This command creates real external traffic.
- Run from the clean checkout used to build the deployed release.
- Supply the exact immutable OCI image digest configured in Helm.
- Use a short-lived read-only IIP credential in a regular, non-symlink file.
  The file must be readable only by the invoking user where the operating
  system supports POSIX modes.
- Start conservatively and compare the declared objective with the customer
  capacity plan. Defaults produce 3,000 authenticated reads over five minutes.
- Keep independent service, ingress, database, and cluster monitoring active
  during the run. This report intentionally stores no raw telemetry.

The command refuses HTTP, URL credentials, query strings, fragments, redirects,
ambient proxies, dirty source, malformed release identity, and more than
250,000 scheduled requests. There is no insecure TLS mode and no implicit
enablement: `IIP_CONTROL_PLANE_LOAD_ALLOW_TRAFFIC` must equal `true`.

## Run

```bash
IIP_CONTROL_PLANE_LOAD_ALLOW_TRAFFIC=true \
IIP_CONTROL_PLANE_LOAD_BASE_URL=https://iip.example.com \
IIP_CONTROL_PLANE_LOAD_TOKEN_FILE=/secure/temporary/iip-load-token \
IIP_CONTROL_PLANE_LOAD_IMAGE_DIGEST=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
make qualify-control-plane-load PYTHON=.venv/bin/python
```

The defaults declare:

- 300 seconds;
- 10 requests per second;
- concurrency 8;
- at least 99.90% successful scheduled requests;
- at most 0.10% scheduler misses;
- p95 no greater than 2,000 ms and p99 no greater than 5,000 ms;
- a 2,000 ms request timeout; and
- a 1,000 ms maximum scheduler lag before a slot is skipped rather than
  emitted late.

Override these with the corresponding `IIP_CONTROL_PLANE_LOAD_*` Make
variables only after reviewing the customer objective. For private ingress
trust, set `IIP_CONTROL_PLANE_LOAD_CA_FILE` to the CA bundle.

The result is written to
`dist/control-plane-load-qualification-report.json`. A completed workload can
return a non-zero exit status while still retaining a valid `not-qualified`
report; unsafe input and generator failures do not create a report.

## Verify retained evidence

From the same clean release checkout, provide the exact target and image digest:

```bash
IIP_CONTROL_PLANE_LOAD_BASE_URL=https://iip.example.com \
IIP_CONTROL_PLANE_LOAD_IMAGE_DIGEST=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
make verify-control-plane-load-report PYTHON=.venv/bin/python
```

Verification performs no network request. It revalidates the contract and
derived arithmetic, then recomputes the current source, release identity, and
pseudonymous target binding. Retain the report with customer qualification
evidence and remove the temporary credential after the run.

## Interpret and follow up

- `scheduler-attainment` failures mean the load generator could not deliver
  the declared schedule without late catch-up; do not interpret the observed
  service rate as the target rate.
- `successful-request-attainment` includes scheduler misses and all failed
  HTTP attempts in the objective denominator.
- `exact-release-identity` failures mean at least one validated response did
  not identify the expected immutable release, or no successful response was
  observed.
- latency objectives describe the successful identity reads only. Review the
  failure counts and independent telemetry before diagnosing a tail-latency
  miss.

Do not extrapolate the result to writes, PostgreSQL, investigation dispatch,
workers, receivers, mixed endpoints, failure recovery, regions, or long-window
SLOs. Those require separate workload and continuity evidence.

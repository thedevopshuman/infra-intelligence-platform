# External ingress availability qualification

**Status:** Executable customer-environment qualification profile

The external probe verifies that one expected IIP release is reachable through
the customer ingress. It checks liveness, database/schema readiness, control
plane authentication, runtime identity, aggregate availability, and p95 cycle
latency. It runs outside the chart so an unavailable cluster or ingress cannot
silence the observer with the workload it is measuring.

## Prepare the credential

Use a short-lived, read-only control-plane identity able to call
`GET /v1/system/version`. Store it in a file readable only by the probe user;
never place it in an argument, environment variable, values file, shell
history, or report.

```bash
umask 077
read -rs SHORT_LIVED_TOKEN
printf '%s\n' "$SHORT_LIVED_TOKEN" > /secure/temporary/iip-probe-token
unset SHORT_LIVED_TOKEN
```

The command reads a 32–8192 character Bearer value from a non-symlink file.
It sends the credential only to `/v1/system/version`; liveness and readiness
requests are unauthenticated.

## Run the customer profile

Run from the clean checkout that produced the deployed release. Supply the
immutable control-plane image index digest reported by the verified release
manifest and configured in Helm:

```bash
IIP_INGRESS_BASE_URL=https://iip.example.com \
IIP_INGRESS_TOKEN_FILE=/secure/temporary/iip-probe-token \
IIP_INGRESS_IMAGE_DIGEST=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
make qualify-ingress-availability PYTHON=.venv/bin/python
```

The default profile takes 100 samples at one-second intervals, requires at
least 99.90% successful cycles, and requires successful-cycle p95 latency at
or below 2,000 ms. Override the bounded objective variables only with a
reviewed customer SLO. To use a private CA, additionally set
`IIP_INGRESS_CA_FILE` to the exact CA bundle. There is no insecure TLS mode.

The process disables ambient proxies and redirects, accepts a root-only base
URL without user information/query/fragment, bounds every response to 64 KiB,
and never follows a redirect carrying authentication. It writes a report even
when measured objectives fail, then exits nonzero.

## Verify and retain evidence

```bash
make verify-ingress-availability-report PYTHON=.venv/bin/python
```

This requires the retained report to be qualified, clean, and bound to the
current source/application/chart/migration identity. Verification is offline;
it does not recontact or mutate the target. Store the report beside the
release qualification and deployment preflight reports, never inside the
immutable release bundle. Remove the temporary token after the run.

The report retains an endpoint digest, release identity, objective, aggregate
path success/failure and latency, closed failure categories, and check status.
It retains no endpoint, hostname, certificate, credential, tenant, actor,
header, response body, provider data, or raw error.

## Local harness profile

`local-loopback` permits HTTP only for a loopback host and requires a
development runtime identity. The unit gate exercises this profile against the
real IIP HTTP handler:

```bash
make test-ingress-availability PYTHON=.venv/bin/python
```

That result proves the probe and local route composition only. It is not
customer-ingress or release evidence.

## Remaining operational boundary

One bounded qualification run does not establish a long-window or regional
SLO. Production operations still schedule probes from independent failure
domains, aggregate the emitted outcome in the customer's telemetry backend,
monitor DNS and certificate expiry, define multi-window burn policy, and route
notifications through customer-owned systems. The probe intentionally does
not call identity-provider, policy, credential-broker, telemetry-backend, or
model-provider APIs because readiness and synthetic health must not create
ambient external authority.

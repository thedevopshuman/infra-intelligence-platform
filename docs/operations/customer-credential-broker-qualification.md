# Customer credential-broker qualification

**Status:** External customer-environment gate

**Decision:** [ADR 0133](../decisions/0133-customer-credential-broker-authority-qualification.md)

**Contract:** [Customer credential-broker qualification contracts](../specifications/customer-credential-broker-qualification-report-contract.md)

This runbook sends one reviewed least-authority request and six single-field
denial mutations directly to a customer credential broker through IIP's
production client. It observes short-lived lease issuance but never prints or
retains the workload identity or issued provider credential.

## Prepare

1. Run the local real-TLS client regression first with
   `make test-credential-broker PYTHON=.venv/bin/python`.
2. Copy
   `contracts/examples/customer-credential-broker-qualification-profile.json`
   outside the repository. Replace its endpoint and authority values, objectives,
   and review time. Preserve the seven-case order and exact single-field
   relationships. Set mode `0600`.
3. Use a read-only logical credential reference with the narrowest provider
   scopes needed for this observation. Inform the broker and credential owners
   that two approved leases will be issued.
4. Project or export the approved workload-identity token to a separate
   mode-`0600` regular file. Do not put it on the command line, in the profile,
   Helm values, or shell history.
5. Export the broker's current CA chain to a separate PEM file. The qualifier
   binds its bytes in the report and never permits insecure TLS.
6. Run from a clean checkout at the source revision represented by the exact
   immutable image digest. Confirm the broker audit path is active before the
   observation.

## Run

```bash
IIP_CUSTOMER_CREDENTIAL_BROKER_PROFILE=/protected/customer-credential-broker-profile.json \
IIP_CUSTOMER_CREDENTIAL_BROKER_ENDPOINT=https://credential-broker.example.com \
IIP_CUSTOMER_CREDENTIAL_BROKER_WORKLOAD_TOKEN_FILE=/protected/workload-token \
IIP_CUSTOMER_CREDENTIAL_BROKER_CA_FILE=/protected/credential-broker-ca.pem \
IIP_CUSTOMER_CREDENTIAL_BROKER_IMAGE_DIGEST=sha256:... \
IIP_CUSTOMER_CREDENTIAL_BROKER_ALLOW_OBSERVATION=true \
  make qualify-customer-credential-broker PYTHON=.venv/bin/python
```

The target verifies the separately supplied endpoint against the protected
profile and writes
`dist/customer-credential-broker-qualification-report.json`. Output contains
only a stable status and path. Environment proxies and redirects are disabled.

A well-formed run with a protocol, authority, or lease mismatch writes a valid
`not-qualified` report and exits nonzero. Unsafe files, malformed or stale
profiles, future review time, dirty source, invalid CA/configuration, or crossed
bindings fail without creating evidence. Never broaden broker policy to make a
qualification pass.

## Verify retained evidence

Keep the minimized report with the exact protected profile and non-secret
endpoint, CA, and image inputs. From the same clean revision:

```bash
IIP_CUSTOMER_CREDENTIAL_BROKER_PROFILE=/protected/customer-credential-broker-profile.json \
IIP_CUSTOMER_CREDENTIAL_BROKER_ENDPOINT=https://credential-broker.example.com \
IIP_CUSTOMER_CREDENTIAL_BROKER_CA_FILE=/protected/credential-broker-ca.pem \
IIP_CUSTOMER_CREDENTIAL_BROKER_IMAGE_DIGEST=sha256:... \
  make verify-customer-credential-broker-qualification-report PYTHON=.venv/bin/python
```

Verification is offline. It rebinds current source, application version, image,
endpoint, profile, authority set, and CA bytes. It never needs the workload
identity or provider credential.

## Complete the production broker gate

Before production approval, separately retain customer evidence for:

- workload-identity issuer, audience, subject, federation, rotation,
  disablement, and revocation-propagation objectives;
- provider-credential issuance, rotation, revocation, emergency access, and
  least-authority review;
- broker DNS, network policy, certificates, dependency health, zone/region
  topology, failover, recovery, and sustained availability/latency;
- certificate-chain rotation and expiry response;
- audit minimization, delivery, retention, alerting, SIEM reconciliation, and
  investigation access; and
- any non-Bearer provider credential schemes required by selected adapters.

The customer report supplements the local compatibility report. Neither report
is permission to enable live actions or an assertion that the broker is a
production-grade credential issuer.

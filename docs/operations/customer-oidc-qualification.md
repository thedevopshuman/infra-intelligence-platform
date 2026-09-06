# Customer OIDC prerequisite qualification

**Status:** External customer-environment gate

**Decision:** [ADR 0131](../decisions/0131-customer-oidc-prerequisite-qualification.md)

**Contract:** [Customer OIDC qualification contracts](../specifications/customer-oidc-qualification-report-contract.md)

This runbook checks a selected customer issuer and deployed IIP release without
placing credentials in reports or automating a human login. It uses one
operator-supplied, short-lived access token only to prove exact claim mapping,
deployed API authentication, and runtime identity. It performs browser CORS and
discovery prerequisite checks but does not call the authorization or token
endpoint.

## Prepare

1. Register the static console as a public client using Authorization Code and
   `S256` PKCE, the exact HTTPS `/console` redirect, no client secret, and the
   minimum approved scopes.
2. Copy `contracts/examples/customer-oidc-qualification-profile.json` outside
   the repository. Replace the example identity, issuer, audience, endpoints,
   claim names, public-client values, and objectives. Set the file mode to
   `0600`.
3. Obtain one short-lived access token for the exact qualification identity
   through the customer's ordinary issuer process. Write only the token plus
   an optional final newline to a separate mode-`0600` regular file. Never put
   it on the command line.
4. Export the API and issuer CA chains to explicit PEM files. Do not substitute
   insecure TLS flags. The qualifier does not use environment HTTP proxies and
   does not follow redirects.
5. Run from a clean checkout at the exact source revision embedded in the
   deployed image. Confirm the immutable image digest from the verified release
   manifest.
6. Inform the qualification identity owner: the test reads `/v1/session` and
   `/v1/system/version`, sends two token-endpoint `OPTIONS` requests, and sends
   a deliberately signature-tampered token to `/v1/session`. It does not create,
   alter, disable, or log out a user.

## Run

```bash
IIP_CUSTOMER_OIDC_PROFILE=/protected/customer-oidc-profile.json \
IIP_CUSTOMER_OIDC_API_BASE_URL=https://iip.example.com \
IIP_CUSTOMER_OIDC_ACCESS_TOKEN_FILE=/protected/access-token \
IIP_CUSTOMER_OIDC_API_CA_FILE=/protected/iip-api-ca.pem \
IIP_CUSTOMER_OIDC_ISSUER_CA_FILE=/protected/issuer-ca.pem \
IIP_CUSTOMER_OIDC_IMAGE_DIGEST=sha256:... \
IIP_CUSTOMER_OIDC_ALLOW_IDENTITY_OBSERVATION=true \
  make qualify-customer-oidc PYTHON=.venv/bin/python
```

The command writes
`dist/customer-oidc-qualification-report.json`. A completed observation prints
only its status and output path; an unsafe or invalid run prints one stable
error code. It never prints the access token, expected identity, issuer, or
endpoints.

A well-formed run that observes a missing or mismatched capability writes a
valid `not-qualified` report and exits nonzero so the finding can be retained.
Malformed, unsafe, dirty-source, unavailable-transport, or crossed target
inputs fail without manufacturing a report.

Treat a failure as an interoperability finding. Do not weaken issuer, audience,
algorithm, claim, origin, CA, redirect, token-lifetime, or release checks to
make an environment pass. Update the protected profile only after the identity
and platform owners review the intended configuration.

## Verify retained evidence

Keep the minimized report with the protected profile and exact non-secret API
target/image inputs. From the same clean revision:

```bash
IIP_CUSTOMER_OIDC_PROFILE=/protected/customer-oidc-profile.json \
IIP_CUSTOMER_OIDC_API_BASE_URL=https://iip.example.com \
IIP_CUSTOMER_OIDC_IMAGE_DIGEST=sha256:... \
  make verify-customer-oidc-qualification-report PYTHON=.venv/bin/python
```

Verification performs no network or identity-provider operation. It validates
the complete report semantics and rebinds the source revision, image, target,
and protected profile. A changed profile, target, image, report, or checkout
invalidates the evidence.

## Complete the interactive identity gate

Before production approval, the customer must separately exercise and retain
evidence for:

- login, exact redirect registration, consent, and every required MFA path;
- authorization-code exchange and browser behavior using the deployed console;
- session lifetime, reauthentication, logout, and multi-tab behavior;
- user and group disablement plus emergency token/key revocation latency;
- current/next signing-key and certificate rotation;
- issuer outage, cache-expiry failure, recovery, DNS/network policy, and
  availability objectives; and
- each supported user class, tenant mapping, and role mapping, including denial
  cases.

The local `make test-oidc` fixture remains useful regression evidence for the
shipped verifier and browser implementation. It is not a substitute for this
customer prerequisite report or the remaining interactive tests.

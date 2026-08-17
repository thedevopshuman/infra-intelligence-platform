# Credential broker compatibility report contract

**Status:** `v1alpha1`
**Machine contract:** `contracts/schemas/credential-broker-compatibility-report.schema.json`

This report is executable, source-bound evidence that the external credential-broker client satisfies the repository's local TLS and workload-identity profile. `make test-credential-broker` generates it outside the control-plane API by exercising a disposable, policy-constrained broker fixture in Docker.

The closed profile checks CA-verified HTTPS, denial with an untrusted CA, signed workload JWT authentication, exact issuer/audience/subject enforcement, exact-scope lease issuance, tenant and scope-escalation denial, token rotation without a client restart, previous-token revocation, value-minimized audit completeness, broker-outage failure, recovery, and secret redaction. Check order and identifiers are fixed so release automation can compare evidence without interpreting prose.

`compatible` means every check passed for the exact source revision, application/runtime versions, Docker server, and declared profile. A dirty-source report is useful during development but is not release evidence. The local fixture is not a production credential issuer and the result is not proof that a customer's OIDC issuer, SPIFFE deployment, cloud federation, secret manager, network, audit sink, or highly available broker is compatible.

The report contains no workload token, JWT signing key, lease-derivation key, provider lease, tenant identifier, credential reference, broker URL, certificate key, request body, or raw upstream error. Failed checks may expose only stable error codes.

This operational report is intentionally not exposed by OpenAPI or either SDK. The SDKs continue to consume public control-plane contracts; the protected request/lease protocol and its local compatibility evidence remain deployment boundaries.

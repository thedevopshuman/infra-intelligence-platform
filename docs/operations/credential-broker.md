# External credential broker

**Status:** Executable client, local TLS compatibility profile, and deployment boundary; external issuer required

The external broker client exchanges authenticated, exact request scope for a short-lived provider Bearer lease. It is shared by the Prometheus, Kubernetes Event evidence, and explicitly selected Kubernetes action adapters and implements the protected [credential lease contract](../specifications/credential-lease-contract.md). [ADR 0022](../decisions/0022-external-workload-identity-credential-broker.md) fixes its authority and secret-handling semantics.

The repository does not ship a production credential issuer. The customer or platform operator must provide an HTTPS broker that validates workload identity and every request field, resolves the logical credential reference, issues only the requested authority, audits issuance without secret material, and supports revocation/rotation.

## Runtime selection

Static provider-specific JSON remains the default development mode:

```text
IIP_CREDENTIAL_BROKER_MODE=static
```

Select the external client and provide non-secret protected configuration with:

```text
IIP_CREDENTIAL_BROKER_MODE=external-http
IIP_CREDENTIAL_BROKER_CONFIG_JSON=<deploy/credential-broker/external-http.example.json>
```

The example fixes an HTTPS endpoint, optional absolute CA bundle path, explicit workload-identity token path, request/response bounds, maximum lease lifetime, and clock-skew tolerance. It contains neither the workload token nor any provider credential.

When external mode is selected, provider-specific static credential JSON is not required. Integration registries still contain logical `credentialRef` values and all endpoint/query/resource allowlists. The same broker object is composed into every selected adapter.

## Broker HTTP behavior

The client sends `POST /v1/credential-leases` with the exact protected request contract. It authenticates that call with `Authorization: Bearer <workload identity>`, read afresh from the configured file. It accepts a lease only from a direct `200 application/json` response, treats only HTTP `403` as an explicit authority denial, never follows redirects, and ignores environment proxy configuration. HTTP `401`, other non-success statuses, TLS/transport errors, and invalid responses remain fail-closed availability errors and cannot satisfy a negative authority test.

The broker response must:

- echo the request ID;
- use the `bearer` scheme;
- contain an ASCII non-whitespace secret between 16 and 16,384 bytes;
- declare issuance within the configured clock-skew tolerance;
- remain valid through the original provider-call deadline; and
- expire no later than `maxLeaseSeconds` after issuance.

The client does not retry or cache in this slice. A broker, identity, TLS, parsing, scope, deadline, or lease error fails the provider request closed. Response bodies and exception text are never surfaced.

## Helm workload identity

Configure values similar to:

```yaml
credentialBroker:
  mode: external-http
  externalHttp:
    configJson: >-
      {"endpoint":"https://credential-broker.security.svc:8443","caBundlePath":"/var/run/iip-credential-broker-ca/ca.crt","workloadIdentityTokenPath":"/var/run/iip-credential-broker-identity/token","requestTimeoutSeconds":5,"maxResponseBytes":65536,"maxLeaseSeconds":900,"maxClockSkewSeconds":30}
    audience: iip-credential-broker
    tokenExpirationSeconds: 600
    caBundleExistingSecret: iip-credential-broker-ca
    caBundleSecretKey: ca.crt

networkPolicy:
  enabled: true
  credentialBrokerEgress:
    enabled: true
    cidr: 10.96.42.18/32
    port: 8443
```

The chart explicitly projects a service-account token with the configured audience and lifetime. `automountServiceAccountToken` remains false, so the pod receives no general ambient Kubernetes API token. The CA Secret mounts read-only at `/var/run/iip-credential-broker-ca/ca.crt`.

The broker must validate the token issuer, exact audience, expected service-account subject, and token expiry. A projected token authenticates the IIP workload only; it does not authorize a tenant, integration, credential reference, provider, or scope by itself. Those checks remain broker policy.

## Local conformance

The default `make verify` gate uses an in-memory transport and temporary rotating identity-token file. It checks schema compatibility, exact request construction, rotation, response correlation, expiry/lifetime/skew rules, redaction, stable errors, and shared composition without contacting an issuer.

Run the isolated real-transport profile with Docker Desktop:

```bash
make test-credential-broker
```

The target generates an ephemeral CA/server certificate chain and signed test-only workload tokens, starts a non-root read-only broker fixture over HTTPS, and exercises the production external client. It proves issuer/audience/subject/expiry enforcement, exact-scope issuance, cross-tenant and scope-escalation denial, rotation without a client restart, previous-token revocation, untrusted-CA denial, value-minimized audit completeness, stable fail-closed outage behavior, recovery, and secret redaction. It writes source-bound aggregate evidence to `dist/credential-broker-compatibility-report.json`, then removes the isolated Compose project. The fixture has no product bootstrap path and is not a production credential issuer.

Before production, repeat an environment-specific interoperability gate against the selected customer issuer, broker, identity system, audit sink, certificate lifecycle, and availability topology. Passing the local profile does not qualify those external systems.

Use the [customer credential-broker qualification](customer-credential-broker-qualification.md)
to prove the selected endpoint's basic protocol, CA trust, exact-authority
issuance, and single-field authority denials through the production client.
That prerequisite remains deliberately narrower than lifecycle, HA, audit, and
recovery certification.

## Rollback

Changing `IIP_CREDENTIAL_BROKER_MODE` requires a process restart. In development, rollback to `static` only with protected provider-specific credential documents. Production rollback must use the deployment's approved secret-management procedure; never copy leased or long-lived credentials into a ConfigMap, values file, command line, log, or public request.

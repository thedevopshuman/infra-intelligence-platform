# Customer credential-broker qualification contracts

**Status:** `v1alpha1` executable customer-environment evidence

**Decision:** [ADR 0133](../decisions/0133-customer-credential-broker-authority-qualification.md)

**Machine contracts:**
`contracts/schemas/customer-credential-broker-qualification-profile.schema.json`
and
`contracts/schemas/customer-credential-broker-qualification-report.schema.json`

These contracts qualify one customer credential broker's basic protocol and
authority enforcement through IIP's production external client. They neither
model nor operate the customer's issuer, secret manager, broker policy, or
provider credential lifecycle and are not served by the control-plane API.

## Protected profile

`CustomerCredentialBrokerQualificationProfile` is a mode-`0600` operator
input. It binds one root HTTPS endpoint and exactly seven ordered cases. The
first requests a short-lived lease with the approved tenant, actor,
integration, provider, logical credential reference, and ordered scope set.
The remaining cases change exactly one authority dimension and must be denied:

1. tenant;
2. actor;
3. integration;
4. provider;
5. one additional scope; and
6. logical credential reference.

This closed relationship prevents an apparently successful report from using
unrelated allow and deny requests. The workload-identity token is a separate
mode-`0600` regular file. The CA bundle and immutable image digest are separate
inputs. No token, issued credential, or credential value is permitted in the
profile or committed example.

The profile also fixes request/response limits, maximum lease and clock-skew
bounds, a request deadline, maximum observed latency, and maximum profile age.
The request deadline cannot exceed the maximum accepted lease lifetime.

## Minimized report

`CustomerCredentialBrokerQualificationReport` retains:

- exact application, contract, source-revision, and immutable-image identity;
- SHA-256 bindings for the endpoint, complete protected profile, ordered
  authority set, and selected CA bundle;
- only review/start/completion times, fixed case/outcome/request counts,
  minimum remaining lease lifetime, and maximum request latency;
- fixed CA-verified, no-redirect, no-proxy, read-per-request identity and
  Bearer-lease behavior;
- eighteen ordered checks and a derived summary; and
- five mandatory limitations covering identity and provider-credential
  lifecycle, broker/PKI availability, audit delivery, and non-Bearer leases.

The `ccbq_` identifier is content-derived. Semantic validation recomputes check
errors, summary, status, time/count relationships, profile-age and latency
results, lease coverage, mandatory safety checks, and report identity. Offline
verification additionally recomputes every digest from the current protected
inputs.

The report rejects fields named for endpoints, tenants, actors, integrations,
providers, scopes, credential references, secrets, tokens, authorization, or
URLs. It never retains the workload-token digest, provider-lease digest,
request IDs, raw broker documents, or upstream error details.

## Qualification boundary

`qualified` means the production client obtained valid bounded Bearer leases
for two observations of the exact reviewed authority and received an explicit
HTTP `403` for every single-field authority mutation within the declared
latency objective. Authentication failure, other upstream status, outage, TLS
or transport failure, and invalid output do not count as authority denials. A
lease only reaches this comparison after the client has validated request
correlation, response shape, issuance time, expiry, deadline coverage, and
maximum lifetime.

The result does not prove workload-token rotation/revocation/federation,
provider-secret rotation or revocation, non-Bearer credentials, sustained
availability, broker or PKI failover, audit delivery, or emergency access.
Those outcomes require separate customer evidence.

Python and TypeScript SDKs expose both shapes for release tooling. They do not
load protected files, call the broker, issue credentials, or grant authority.

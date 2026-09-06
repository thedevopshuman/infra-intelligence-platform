# ADR 0133: Qualify customer credential-broker authority without importing issuer ownership

**Status:** Accepted
**Date:** 2026-09-08

## Context

ADRs 0022 and 0077 define the external workload-identity credential-broker
client and prove its TLS, correlation, short-lease, rotation, revocation, audit,
outage, and recovery behavior against a disposable local fixture. That evidence
does not prove that a selected customer broker accepts IIP's protocol or
enforces the exact tenant, actor, integration, provider, scope, and logical
credential-reference tuple.

IIP must not become a credential issuer, copy customer secrets into test
profiles, or retain customer authority values in a shareable qualification
report. Broker HA, workload federation, credential rotation, audit export, and
certificate operations also require customer-owned control planes that a
portable host-side check cannot safely orchestrate.

## Decision

Add a protected `CustomerCredentialBrokerQualificationProfile` and minimized
`CustomerCredentialBrokerQualificationReport` with the closed
`customer-credential-broker-authority-prerequisites-v1` qualification.

1. A mode-`0600` profile selects one root HTTPS broker endpoint and exactly
   seven ordered cases: one issued exact-authority request followed by
   single-field tenant, actor, integration, provider, scope-escalation, and
   credential-reference denials.
2. The approved workload-identity token is supplied only through a separate
   mode-`0600` regular file. The selected CA bundle and immutable application
   image digest are also separate inputs. The token and its digest are never
   retained.
3. The host-side qualifier invokes the production
   `ExternalHttpCredentialBroker`. The client reads workload identity for every
   request, verifies the selected CA, refuses redirects, disables environment
   proxies, bounds request/response size and time, correlates responses, and
   validates Bearer lease lifetime.
4. Every authority denial must be an explicit HTTP `403`, surfaced as
   `credential.broker.request.denied`. Authentication failure, outage, TLS or
   transport failure, any other status, and malformed output cannot satisfy a
   negative authority case. A returned lease for any denied mutation is a
   qualification failure. The exact issued request is repeated without
   requiring its secret value to remain stable.
5. A reachable but incompatible broker produces a valid `not-qualified`
   report. Unsafe inputs, future review time, dirty source, crossed endpoint,
   profile, CA, source, or image binding fail without manufacturing evidence.
6. The report retains source and image identity; hashes of the endpoint,
   profile, authority set, and CA bundle; aggregate counts and timing; fixed
   client behavior; stable checks; and mandatory limitations. It retains no
   endpoint, authority tuple, workload identity, issued credential, request,
   response, or upstream error.
7. Offline verification rebinds the report to the same clean source, protected
   profile, endpoint, CA bundle, and immutable image.
8. The customer deployment aggregate consumes the qualified report, binds its
   endpoint/profile/authority-set/CA digests, and orders its observation after
   policy qualification and before disruption workflows. It never reads the
   workload token or requests another lease.

The profile is provider-neutral at the broker protocol boundary. It does not
select Vault, SPIRE, a cloud secret manager, or any provider-specific issuer.

## Consequences

- A customer can prove basic IIP protocol and authority interoperability before
  enabling provider-backed evidence or actions.
- The qualification intentionally obtains short-lived credentials for its two
  accepted requests. Operators must use a read-only, least-authority logical
  credential reference and approve the observation window.
- Workload-identity and provider-credential lifecycle, non-Bearer credentials,
  broker/certificate/network HA and recovery, emergency access, and audit/SIEM
  delivery remain separate production gates.
- The local compatibility report remains the regression proof for rotation,
  revocation, and outage mechanics and is not replaced by this customer gate.

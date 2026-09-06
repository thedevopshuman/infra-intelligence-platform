# ADR 0132: Qualify customer policy decisions without importing policy authority

**Status:** Accepted
**Date:** 2026-09-08

## Context

ADRs 0037 and 0079 establish the replaceable `external-http` policy-decision
port and prove its fail-closed behavior against a disposable real-TLS fixture.
That evidence does not prove that one customer's endpoint accepts IIP's exact
request envelope or that a reviewed customer bundle returns the intended allow
and deny decisions from one immutable snapshot.

Importing OPA, Cedar, or another provider's bundle format would move policy
ownership into IIP. Retaining raw decisions would expose customer identity,
roles, resource scope, and authorization logic. Availability, credential
rotation, break-glass governance, and audit delivery also require authorities
that a portable repository tool must not assume.

## Decision

Add a protected `CustomerPolicyQualificationProfile` and a minimized
`CustomerPolicyQualificationReport` with the closed
`customer-policy-engine-bundle-prerequisites-v1` qualification.

1. A mode-`0600` profile selects one HTTPS endpoint, tenant, actor, ordered role
   set, and two to 64 reviewed policy cases. Cases are ordered and include at
   least one allow and one deny expectation from one exact immutable snapshot.
2. The service credential is a separate mode-`0600` bearer-token file. The CA
   bundle is a separate regular file. Neither enters the profile or report.
3. The host-side qualifier invokes the production `ExternalHttpPolicyDecisionPoint`.
   Its client verifies the selected CA, rejects redirects, disables environment
   proxies, bounds requests and responses, and rereads the credential for every
   decision.
4. Every returned decision must bind the canonical input digest and exact
   tenant before the adapter releases it. The qualifier then compares allowed,
   reason, and snapshot fields with the protected expectation and repeats the
   first case to detect a bundle change during the observation.
5. A valid capability mismatch or unavailable endpoint produces a retained
   `not-qualified` report. Unsafe files, malformed profiles, future review
   times, dirty source, crossed verification inputs, or invalid output fail
   without manufacturing evidence.
6. The report retains only source and image identity; profile, endpoint, case,
   and snapshot-set digests; counts; bounded latency; fixed client behavior;
   stable checks; and explicit limitations. It retains no endpoint, tenant,
   actor, role, action, resource, expected result, reason, snapshot, token, or
   raw provider response.
7. Offline verification rebinds the report to a clean exact source, immutable
   image, protected profile, and separately supplied endpoint.

The profile is intentionally provider-neutral. A customer policy service or
adapter may be implemented with OPA, Cedar, or another engine as long as it
implements the existing versioned request and decision contracts.

## Consequences

- A customer can prove selected bundle behavior through the shipped production
  adapter without granting IIP policy-management authority.
- Qualification remains narrow: it proves only the reviewed cases in one
  bounded observation. It does not prove complete action coverage or policy
  correctness beyond those cases.
- Policy-engine HA and network failover, credential rotation/revocation,
  bundle review/change control, emergency access, and audit/SIEM delivery
  remain separately owned production gates.
- The local compatibility report remains useful adapter regression evidence;
  it is not replaced by customer bundle evidence.

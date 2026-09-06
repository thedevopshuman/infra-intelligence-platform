# External authorization policy boundary

**Status:** Accepted production-facing boundary
**Date:** 2026-08-17

Authentication establishes an actor, tenant, and roles; it never grants a use
case by itself. Every application port continues to call the
`PolicyDecisionPoint` with the authenticated actor, one closed action name, and
the exact tenant-scoped resource input.

The local profile uses the deliberately small `AllowTenantPolicy`. A deployed
profile may set `IIP_POLICY_MODE=external-http` and provide bounded configuration
through `IIP_POLICY_CONFIG_JSON`:

```json
{
  "endpoint": "https://policy.example.internal/v1/data/iip/decision",
  "caBundlePath": "/var/run/iip-policy-ca/ca.crt",
  "bearerTokenPath": "/var/run/iip-policy-token/token",
  "timeoutSeconds": 5,
  "maxResponseBytes": 65536
}
```

The adapter posts one canonical object:

```json
{
  "input": {
    "apiVersion": "iip.platform/v1alpha1",
    "kind": "PolicyDecisionRequest",
    "metadata": {
      "tenantId": "tenant-a",
      "actorId": "operator-17"
    },
    "spec": {
      "action": "resource:read",
      "roles": ["developer"],
      "resource": {"tenantId": "tenant-a"}
    }
  }
}
```

The configured service must return a versioned `PolicyDecision` under `result`
with exactly `allowed`, the request's canonical `inputDigest`, a stable
`reasonCode`, and a tenant-bound immutable `policySnapshotRef`. This shape can
be produced by OPA, Cedar-based services, or another policy product without
putting vendor types in the application layer. Governed action proposal,
approval, and execution records preserve the returned snapshot reference
together with their canonical policy-input digest.

The endpoint is TLS-only, redirects are refused, request and response sizes are
bounded, and optional CA and token material are read from protected files. The
token is read for each decision so rotation does not require a restart. A
cross-tenant input is denied before transport. Timeout, network failure,
malformed output, wrong-tenant snapshot, or credential failure returns the
stable denied decision `policy.unavailable`; provider exception text is never
returned or logged by the adapter.

This completes the replaceable production policy integration boundary. Policy
bundle content, change review, emergency access, availability objectives, and
the chosen customer's policy service remain deployment and operating-model
decisions.

AI invocation qualification uses the closed `ai-economics:qualify` action,
separate from the ordinary `ai-economics:read` allocation/finding permission.
Its resource contains only the authenticated tenant and tenant-bound
correlation digest. Production policy bundles should grant it only to the
platform-admin principals running reviewed end-to-end qualifications.

The Docker-backed gate in [ADR 0079](../decisions/0079-executable-policy-engine-compatibility-evidence.md)
uses this production adapter against a real TLS fixture and emits source-bound
evidence for exact input, digest and snapshot binding, bearer rotation and
revocation, redirect and bounded-response denial, outage recovery, audit
minimization, and redaction. It qualifies the adapter, not a customer bundle.

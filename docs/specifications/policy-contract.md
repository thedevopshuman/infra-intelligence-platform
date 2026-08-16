# Policy decision contracts

**Status:** v1alpha1

`PolicyDecisionRequest` carries the authenticated actor and tenant, the closed
application action name, roles derived by the authenticator, and the exact
tenant-scoped resource input used by the application port. Payload identity can
never construct this document at the HTTP surface.

`PolicyDecision` binds allow or deny, a stable reason code, and an immutable
tenant policy snapshot to the canonical SHA-256 digest of that exact request.
The external adapter rejects a response whose tenant or `inputDigest` differs,
so an old decision cannot be replayed for another actor, action, resource, or
role set.

The HTTPS adapter uses an OPA-compatible `{"input": <PolicyDecisionRequest>}`
request wrapper and expects `{"result": <PolicyDecision>}`. The platform
contract remains vendor-neutral; a policy product or adapter is responsible for
producing the closed result shape. Missing, malformed, oversized, unavailable,
or wrong-scope responses deny closed as `policy.unavailable` and do not grant a
snapshot.

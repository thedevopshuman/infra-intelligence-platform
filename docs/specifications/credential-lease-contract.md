# Credential lease request and response contracts

**Status:** v1alpha1 protected service protocol

**Machine contracts:**

- `contracts/schemas/credential-lease-request.schema.json`
- `contracts/schemas/credential-lease.schema.json`

These contracts define the protected exchange between an infrastructure adapter and a separately operated credential broker. They are not a public control-plane API: no user, plugin, agent, prompt, evidence provider, or SDK client can call this route through IIP, and lease responses must never enter evidence, events, logs, reports, examples containing real values, or durable platform storage.

## Request authority

The control plane constructs `CredentialLeaseRequest` from an already-authorized application port value. Tenant and actor come from authenticated request context; integration, provider, scopes, logical `credentialRef`, and deadline come from the selected protected integration and adapter. The external broker authenticates the control-plane workload independently and remains responsible for checking every asserted field against its own policy.

`requestId` correlates one exchange and must be echoed by the response. It is not an action idempotency key. `requestedAt` is the control-plane clock at dispatch. Scopes are exact, nonempty, unique, bounded, and provider-specific; the broker must not return broader authority than requested.

The request contains no provider credential. The `Authorization` header used to authenticate the broker call carries a separately projected workload-identity token with a broker-specific audience and is not part of the JSON contract.

## Lease response

The first version permits only Bearer leases. `issuedAt` and `expiresAt` are mandatory. The client requires the lease to cover the original request deadline, rejects issuance unreasonably outside its clock-skew window, and rejects lifetimes above its configured maximum. A missing, expired, overlong, mismatched, malformed, redirected, or non-TLS response fails closed.

`secret` is a write-only transport field. Its committed example is an explicit non-credential sentinel. Implementations must redact representations, avoid exception/body logging, keep the decoded value inside the requesting adapter, and discard references after the provider call. The control plane never returns a lease over its OpenAPI surface.

## Transport binding

The reference client sends `POST /v1/credential-leases` with `application/json`, accepts a lease only from a direct `200 application/json` response, verifies TLS against system trust or an explicitly configured CA bundle, refuses redirects, disables environment proxies, and bounds time and response bytes. An explicit HTTP `403` becomes the stable `credential.broker.request.denied` authority result. Authentication failures, other upstream status codes, TLS failures, transport failures, and invalid responses remain indistinguishable fail-closed availability errors; upstream status, body, and exception text are never exposed.

Authentication to the broker uses a token file path from protected runtime configuration. The file is read for each request so projected workload-identity rotation does not require restarting the control plane. The reference Helm profile uses an explicit Kubernetes `serviceAccountToken` projection while keeping ambient service-account-token automount disabled.

## Compatibility

Additive fields may be introduced within `v1alpha1` only when old clients can ignore neither authority nor secret-handling semantics. A new credential scheme, delegated scope, secret delivery mechanism, or authority rule requires a new version and migration decision.

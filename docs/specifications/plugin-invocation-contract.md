# Plugin invocation contracts

**Status:** v1alpha1

`PluginInvocation` is one host-issued call over an already authorized
`PluginSession`. It binds the request ID, authenticated tenant and actor, session,
deadline, manifest digest, granted capability, declared method, and method input.
The capability token itself never enters the document, plugin process
environment, log stream, or public API.

The host validates the invocation against the session and manifest before a
container starts. The owning application integration must subsequently validate
the method input and returned output against the interface's declared schemas
before the output can enter an application port. The generic runner accepts only
a bounded JSON object and does not treat it as trusted provider data.

`PluginInvocationResult` is created by the host, not trusted from the plugin. A
successful result contains the method output, its canonical digest, wall time,
and exact captured output bytes. A failed or cancelled result contains a stable
error code and usage, but no output or output digest. Raw stderr, provider
exceptions, credentials, and stack traces never cross this contract. Both the
successful and failed examples are normative schema fixtures.

Invocation and session timestamps must be ordered as `createdAt <= deadline <=
session.expiresAt`. Tenant, session, request, plugin identity, and manifest digest
must agree across all documents.

Before starting a container, a durable runner atomically claims the exact-tenant
request ID and binds it to the persisted session plus the canonical invocation
digest. An exact retry returns a stored terminal result without execution even
after its deadline; expiry prevents new impact, not read-only recovery. An
unfinished claim returns `plugin.request.reconciliation-required`; it is never
automatically replayed because the prior impact is unknown. Reusing an ID for
different content or another session returns `plugin.request.conflict`.

The session request limit is enforced in the same serialized transaction across
runner replicas and restarts. A host runtime failure is stored as a terminal
failed result. A failure to persist that terminal result leaves the claim
ambiguous and therefore non-replayable. [ADR 0064](../decisions/0064-durable-plugin-invocation-ownership.md)
defines these ownership semantics. The [plugin invocation lifecycle
contract](plugin-invocation-lifecycle-contract.md) adds durable status,
cooperative cancellation, and administrator-only post-deadline reconciliation.
`spec.mediationGrants` is an additive request-scoped extension for the
[host-mediated read protocol](plugin-mediation-contract.md). It is covered by
the invocation digest and carries no endpoint, credential reference, or secret.
`spec.actionMediationGrants` is the distinct additive extension for
[proposal-only action mediation](plugin-action-mediation-contract.md). It is
also covered by the durable invocation digest and can grant only action type,
target, dry-run policy, expiry, and count upper bounds. It never contains an
approval, executor, credential, or mutation endpoint.

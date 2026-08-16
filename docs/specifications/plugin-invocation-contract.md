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
and exact captured output bytes. A failed or cancelled result contains only a
stable error code. Raw stderr, provider exceptions, credentials, and stack traces
never cross this contract.

Invocation and session timestamps must be ordered as `createdAt <= deadline <=
session.expiresAt`. Tenant, session, request, plugin identity, and manifest digest
must agree across all documents. The standalone runner rejects duplicate request
IDs within its process. A production workflow must additionally claim and store
requests durably before enabling any side-effecting plugin capability.

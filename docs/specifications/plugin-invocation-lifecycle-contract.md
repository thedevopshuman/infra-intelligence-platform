# Plugin invocation lifecycle contracts

**Status:** v1alpha1

The plugin invocation lifecycle exposes durable host facts without giving a
plugin access to the control plane. `PluginInvocationStatus` identifies the
exact tenant, session, plugin version, canonical request digest, claim time, and
execution deadline. Its state is one of `claimed`, `cancellation-requested`,
`succeeded`, `failed`, or `cancelled`.

`GET /v1/plugin-invocations/{invocationId}/status` resolves only within the
authenticated tenant and requires the `plugin:get-invocation` policy action. A
terminal status contains a provider-neutral result reference. The reference is
an identifier, not a bearer credential and not a direct storage URL.

## Cancellation

`PluginInvocationCancellationRequest` records an authenticated actor's intent
to stop a claimed invocation. Tenant and actor fields must match credential-
derived identity, the path and body invocation IDs must match, and policy must
allow `plugin:cancel-invocation`. The first accepted request atomically changes
the status to `cancellation-requested` and appends an audit record. Repeated
requests return the current status and do not replace the first intent.

Cancellation is cooperative. The host runner polls the durable flag, kills the
isolated container, and commits a host-created `cancelled` result with
`plugin.runtime.cancelled`. The plugin never receives a credential or cancellation
endpoint. If normal output races with an accepted intent, the ledger rejects the
success and the runner commits cancellation. A request alone is not evidence
that impact stopped; only terminal `cancelled` state closes the invocation.

## Reconciliation

`PluginInvocationReconciliationRequest` closes a claim whose outcome is unknown
without replaying it. The actor must have `platform-admin`, policy must allow
`plugin:reconcile-invocation`, and `requestedAt` must be at or after the original
deadline. A live-deadline request returns
`plugin.reconciliation.deadline-live`.

Reconciliation atomically stores a terminal host result with
`plugin.execution.outcome-unknown`, updates status, and appends an audit record.
An outstanding cancellation intent produces `cancelled`; otherwise the state is
`failed`. This result deliberately does not claim whether external impact
occurred. Existing terminal state is returned idempotently and is never
overwritten.

The normative files are:

- `contracts/schemas/plugin-invocation-status.schema.json`
- `contracts/schemas/plugin-invocation-cancellation-request.schema.json`
- `contracts/schemas/plugin-invocation-reconciliation-request.schema.json`
- their same-named examples under `contracts/examples/`

[ADR 0065](../decisions/0065-plugin-invocation-cancellation-and-reconciliation.md)
defines the authority and race semantics.

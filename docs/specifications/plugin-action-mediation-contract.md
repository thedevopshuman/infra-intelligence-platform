# Plugin action mediation contracts

**Status:** v1alpha1 private runner protocol

Plugin action mediation lets an isolated, untrusted plugin ask the host to
create one ordinary governed action proposal. It grants no approval or
execution authority. The protocol shares the invocation-local Unix socket and
no-network relay used for read mediation, but it uses distinct kinds and grants
so read and action authority cannot be confused.

`PluginActionMediationGrant` is host-created and bound to one invocation,
authenticated tenant and actor, expiry, an allowlist of action types and target
resource UIDs, a dry-run policy, a request count, and a maximum proposal
lifetime. It contains no credential, endpoint, integration secret, approval, or
execution role. The grant is covered by the durable invocation digest and must
be a subset of the complete signed manifest's `permissions.actions`.

`PluginActionMediationRequest` is untrusted plugin output. It selects one grant,
completed investigation, allowed target, closed action type, closed parameters,
and dry-run mode. It cannot choose an expiry or idempotency key. The first
profile supports only `kubernetes.restart-workload` with namespace, workload
kind, and workload name; the governed action service revalidates all three
against the current canonical resource.

The host derives an idempotency key from plugin, invocation, and request
identity. It evaluates `plugin:propose-action` over value-minimized digests,
records `plugin-action-proposal-intent` before calling the ordinary action
service, and derives an expiry no later than the grant or invocation deadline.
The action service then independently requires a completed, non-failed
investigation with `propose` authority and scope containing the target.

`PluginActionMediationResponse` returns only the proposal ID, digest, expiry,
and dry-run flag, or a stable `plugin.action.*` failure. Provider details,
policy documents, investigation content, credentials, approval methods, and
execution capabilities never cross the socket. A repeated identical request is
idempotent; conflicting reuse fails closed.

The normative files are:

- `contracts/schemas/plugin-action-mediation-grant.schema.json`
- `contracts/schemas/plugin-action-mediation-request.schema.json`
- `contracts/schemas/plugin-action-mediation-response.schema.json`
- their same-named examples under `contracts/examples/`

There is no new OpenAPI operation. Operators review, approve, and execute the
result through the existing authenticated action APIs and SDKs. Python plugins
use `PluginMediationClient.propose_action`; TypeScript exposes transport-neutral
contract types.

The executable `host-mediated-action-proposal` compatibility profile runs that
Python SDK call inside the signed no-network container, through the trusted
relay and real governed action service. Its positive row additionally proves
the resulting proposal is pending approval and that no approval or execution
record was created. This is local protocol conformance, not live customer or
mutation-environment certification.

[ADR 0070](../decisions/0070-proposal-only-plugin-action-mediation.md) defines
the authority and separation-of-duties boundary; [ADR 0071](../decisions/0071-executable-action-provider-compatibility-profile.md)
defines its executable compatibility evidence.

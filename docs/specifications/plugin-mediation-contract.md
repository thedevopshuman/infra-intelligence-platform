# Plugin mediation contracts

**Status:** v1alpha1 private runner protocol

The plugin mediation contracts let an isolated plugin request bounded provider
data without receiving network access or a credential. They are public plugin
SDK contracts but are not control-plane HTTP endpoints. The host exposes the
protocol only on a fresh Unix-domain socket mounted into one invocation.
The portable Docker profile implements the mount with a fresh volume and a
digest-pinned, no-network host relay so it also works under Docker Desktop's
Linux VM. The relay has no policy, credential, or provider authority of its own.

`PluginMediationGrant` is host-created request-scoped authority metadata. It is
bound to one invocation, authenticated tenant and actor, integration, provider,
manifest-declared destination and logical credential name, exact path templates,
query-key allowlist, broker scopes, expiry, request count, and response-byte
limit. It deliberately excludes the provider URL, CA configuration,
`credentialRef`, and credential value. The grant is included in the invocation
and therefore covered by its canonical durable claim digest.

`PluginMediationRequest` is untrusted plugin output. It can select only a grant,
`GET`, one absolute path, and allowlisted query values. It has no scheme, host,
port, headers, redirect option, method override, request body, credential field,
or action semantics. Each template segment such as `{namespace}` matches exactly
one safe path segment; it cannot match a slash, empty segment, `.` or `..`.

`PluginMediationResponse` is host-created. Success contains only a bounded JSON
object or array plus its canonical digest and byte count. Provider headers,
cookies, redirects, status bodies, certificate details, broker responses, and
credentials never cross the socket. Failures use stable
`plugin.mediation.*` codes. All provider content remains untrusted input to the
plugin and the plugin's eventual result remains untrusted input to the host.

The host validates the grant against the signed manifest and a protected grant
binding, evaluates `plugin:mediate-read` policy for the exact path and query
digest, commits an audit intent before egress, obtains one deadline-bound lease
through the protected credential broker, and performs a direct TLS-verified
JSON request with redirects disabled. A broker or audit failure prevents the
provider call; provider failure never exposes upstream detail.

The normative files are:

- `contracts/schemas/plugin-mediation-grant.schema.json`
- `contracts/schemas/plugin-mediation-request.schema.json`
- `contracts/schemas/plugin-mediation-response.schema.json`
- their same-named examples under `contracts/examples/`

There is intentionally no OpenAPI operation for this protocol. Python plugins
may use `PluginMediationClient`; TypeScript exposes transport-neutral contract
types only.

[ADR 0066](../decisions/0066-host-mediated-plugin-read-connectivity.md) defines
the isolation and authority boundary.

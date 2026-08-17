# Policy engine compatibility report

**Status:** v1alpha1 operational evidence

`PolicyEngineCompatibilityReport` is source-bound evidence that the production
`external-http` policy adapter passed the closed local interoperability profile
against a real TLS service. It records the source revision, dirty state, runtime
versions, fixed profile parameters, exact ordered checks, and a derived summary.

The profile proves CA validation, bearer authentication and rotation, the exact
OPA-compatible input wrapper, allow and deny results, canonical input-digest and
tenant snapshot binding, redirect/content-type/response-size rejection,
revocation, fail-closed outage behavior, recovery, minimized audit output, and
secret redaction. A compatible report has all 18 checks passed.

The report contains no endpoint, token, actor, tenant, resource, request body,
decision body, certificate private key, or provider error. It qualifies the
platform adapter and local fixture only. A customer must rerun equivalent checks
against its selected engine, policy bundle, credentials, certificate chain,
network controls, availability design, audit destination, and change process.

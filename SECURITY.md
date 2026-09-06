# Security policy

Infrastructure Intelligence Platform is pre-release software. The repository
does not yet accept general production deployments and does not publish a
staffed vulnerability-reporting address or response-time commitment.

## Report safely

Do not open a public issue containing exploit details, credentials, customer
data, infrastructure identifiers, private endpoints, prompts or responses,
raw telemetry, protected profiles, or diagnostic artifacts. A private
design-partner pilot must establish a named security owner and a private
reporting and escalation path in its customer-owned pilot plan before
installation.

Until that path exists, do not send sensitive details to the project and do
not start the pilot. A non-sensitive report may identify the affected version,
public contract, and stable error code, but must not include material that
could identify a customer or enable exploitation. See [SUPPORT.md](SUPPORT.md)
for the ordinary support boundary.

When a private report is authorized, preserve the exact release identity,
UTC observation window, relevant minimized report digests, and a concise impact
description. Rotate or revoke exposed credentials through the owning customer
system; IIP does not own customer identity, PKI, secret-store, provider, or
policy-engine lifecycles.

## Supported scope

Only the exact candidate and environment admitted by the
[private-pilot onboarding workflow](docs/operations/private-pilot-onboarding.md)
are in private-pilot scope. A branch tip, local development stack, unsigned
bundle, expired qualification report, or modified artifact is not an admitted
candidate. Public production support, severity response objectives, embargo
handling, coordinated disclosure, and supported-version windows remain
organizational decisions required before public launch.

The baseline threat and authority model is documented in
[security, tenancy, and authority](docs/architecture/security-tenancy.md).


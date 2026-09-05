# GitHub context compatibility report contract

**Status:** v1alpha1 local compatibility evidence

**Machine contract:** `contracts/schemas/github-context-compatibility-report.schema.json`

**Example:** `contracts/examples/github-context-compatibility-report.json`

`GithubContextCompatibilityReport` records what the shipped read-only GitHub
context adapter proved against a disposable GitHub Contents-shaped HTTPS
fixture. It is an operational qualification artifact, not a control-plane API
response and not an SDK model.

## Binding

Metadata binds the report to one source revision and records whether that
source was dirty. The content-derived report ID changes when metadata or the
profile changes. A release-retained report must be clean-current; a dirty
report remains useful only while developing the harness.

The closed profile records the application and Python versions, host platform,
GitHub REST API version, request-scoped credential mode and scope, immutable
commit-plus-blob revision mode, 1 MiB file ceiling, direct connection mode,
and redirect denial. It deliberately contains no endpoint, IP address, tenant,
actor, integration, repository, path, certificate, token, or document content.

## Checks

The ordered check set covers:

- trusted and untrusted CA behavior;
- Bearer authentication and the exact API-version header;
- immutable commit `ref`, encoded path, returned path, size, and Git blob
  binding;
- exact credential-broker scope and tenant isolation before broker or network
  access;
- redirect and response-size denial;
- provider outage failure and recovery; and
- minimized report output.

`status`, `summary`, and the report ID are derived. Failed checks must carry a
stable error code, and a report with any failed check is `incompatible`.

## Claim boundary

This report qualifies the adapter and its local real-TLS wire profile. It does
not qualify a customer's GitHub organization, GitHub Enterprise version,
GitHub App installation, credential issuer, repository permissions, network,
rate limit, content policy, or availability objective. Those require a clean
customer-environment report using the same protected configuration boundary.

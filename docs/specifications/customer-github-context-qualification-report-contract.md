# Customer GitHub context qualification contracts

**Status:** v1alpha1 executable customer prerequisite evidence

`CustomerGithubContextQualificationProfile` is a protected reviewed selection
for one real GitHub Cloud or GitHub Enterprise Server repository document.
`CustomerGithubContextQualificationReport` is the corresponding minimized,
source-bound observation. Neither contract grants authority and neither
contains a credential value.

## Protected profile

The profile binds:

- one service mode: `github-cloud` or `github-enterprise-server`;
- the exact tenant, actor, integration, resource UID, logical reference, and
  document kind supplied to the provider-neutral context port;
- the exact HTTPS endpoint, GitHub API version, logical credential reference,
  repository owner/name, forty-character commit SHA, repository-relative path,
  and expected Git blob SHA;
- request timeout, encoded response limit, decoded document limit, request
  deadline, read-latency objective, and profile age.

GitHub Cloud is restricted to `https://api.github.com` and system trust.
GitHub Enterprise Server uses a direct HTTPS endpoint ending in `/api/v3`; an
explicit host-side CA file is required exactly when the protected runtime
configuration declares a custom CA path. The host path can differ from the
container path because the report binds both the unchanged configuration
document and CA bytes rather than treating a filesystem location as trust
identity.

The selected integration snapshot must contain exactly one integration, one
repository, and one document for this V1 profile. The document must bind only
the selected resource. Broader installations qualify additional documents in
separate runs rather than converting one successful read into aggregate proof.

The profile and integration snapshot are mode `0600`, regular, current-user
owned files. Credential-shaped fields are rejected from the profile.

## Credential prerequisite

The qualifier verifies a current, qualified
`CustomerCredentialBrokerQualificationReport` against its protected profile,
endpoint, CA, image, clean source, and exact file bytes. Its `exact-authority`
case must equal the GitHub query's tenant, actor, integration and credential
reference, provider `github`, and sole scope `repository:contents:read`.

The shared authority grammar accepts two- or three-segment scopes with at most
64 characters per segment. Wildcards and a fourth segment remain invalid.

## Live observation

After all static bindings pass, the qualifier uses the production external
credential-broker client and production GitHub context adapter to issue one
bounded request. The adapter disables environment proxies and redirects,
verifies TLS, requests an exact commit through GitHub's repository Contents
API, checks response path and size, decodes UTF-8 content, recalculates the Git
blob SHA, and returns the provider-neutral context result.

The report is `qualified` only when all twenty ordered checks pass, including:

- current profile, clean source, immutable image, protected files, and exact
  configuration/endpoint/API bindings;
- a qualified exact-authority credential-broker prerequisite;
- successful workload identity and provider credential use over verified,
  direct HTTPS;
- one complete document at the exact commit, logical reference, resource,
  kind, path, and expected blob;
- bounded latency and a content-free minimized output.

A GitHub, broker, TLS, response, or document failure produces
`not-qualified`. Raw error text and provider responses never enter the report.

## Retained bindings and limitations

The report retains only release identity, timestamps, counts, latency, closed
status/check identifiers, fixed limitations, and SHA-256 bindings for the
profile, integration snapshot, endpoint, repository, document, GitHub CA,
credential-broker report/profile/authority/endpoint/CA, and observed revision.
It deliberately omits all protected values and document content.

The fixed limitations keep GitHub App and credential lifecycle, organization
governance, sustained rate limits and secondary throttling, certificate/network
and service HA, and every additional repository/document/API outside the
claim.

Schemas and examples:

- `contracts/schemas/customer-github-context-qualification-profile.schema.json`
- `contracts/schemas/customer-github-context-qualification-report.schema.json`
- `contracts/examples/customer-github-context-qualification-profile.json`
- `contracts/examples/customer-github-context-qualification-report.json`
- `contracts/examples/customer-credential-broker-qualification-profile-github-context.json`

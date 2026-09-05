# ADR 0106: Read protected GitHub context at immutable revisions

**Status:** Accepted
**Date:** 2026-09-06

## Context

The first `ContextDocumentsBackend` reads an allowlisted local volume. That is
useful for examples and synchronized content, but it does not let a customer
bind an investigation directly to reviewed repository content. Adding paths,
repositories, refs, endpoints, or credentials to the public context request
would give callers provider authority and couple the application contract to
GitHub.

GitHub's REST Contents endpoint can return a file as Base64 JSON and accepts a
`ref`; GitHub also requires a valid `User-Agent` and recommends an explicit
`X-GitHub-Api-Version`. The response is untrusted network data and an ordinary
branch or tag can move between reads.

## Decision

1. Add a GitHub adapter behind the existing provider-neutral
   `ContextDocumentsBackend`. Do not change the public context request/result,
   OpenAPI, SDK, domain, or application ports.
2. Keep tenant, integration, API origin/version, credential reference,
   repository owner/name, exact 40-character commit SHA, paths, logical
   references, resource bindings, timeouts, response limits, and optional CA
   path in protected administrator configuration. Public callers continue to
   select only logical reference IDs and closed document kinds.
3. Use only `GET /repos/{owner}/{repo}/contents/{path}?ref={commit}`. Encode the
   path, send the configured API version, refuse HTTP, URL credentials, query
   configuration, redirects, and ambient proxies, and validate TLS with system
   trust or the configured CA bundle.
4. Resolve one request-scoped Bearer lease through the existing
   `CredentialBroker` using the exact tenant, actor, integration, credential
   reference, deadline, provider `github`, and scope
   `repository:contents:read`. The static mapping is development-only.
5. Accept only a non-empty Base64 UTF-8 `file` response no larger than 1 MiB.
   Require exact path and size, recompute the Git blob object ID, and retain a
   commit-plus-blob revision. Ignore additive response fields and never follow
   `download_url`.
6. Pass content through the existing mandatory redaction, immutable Evidence,
   `trust: untrusted`, and `instructionPolicy: data-only` boundary. Provider
   failures expose only the ordinary stable Evidence error.
7. Retain a source-bound `GithubContextCompatibilityReport` from a disposable
   real-TLS fixture. The profile proves direct TLS, authentication, immutable
   ref and response binding, credential scope, tenant isolation, limits,
   redirect denial, outage behavior, and recovery without retaining endpoint,
   tenant, repository, path, or credential values.

## Consequences

- investigations can use reviewed remote runbooks and source without mounting
  a repository or widening the public query language;
- a branch update does not silently change evidence collected for an accepted
  integration generation;
- every repository revision change remains an administrator configuration
  operation and the resulting artifact records both commit and blob identity;
- GitHub Enterprise can use an HTTPS API base path and private CA, while
  network egress remains an explicit customer CIDR/port decision;
- the 1 MiB Contents JSON profile intentionally rejects large files, binary
  files, directory traversal, archives, recursive trees, Git LFS objects, and
  arbitrary code search;
- the local fixture is not a claim of customer GitHub/GitHub Enterprise App,
  rate-limit, proxy, repository-policy, or availability interoperability.

## References

- [GitHub REST repository contents](https://docs.github.com/en/rest/repos/contents)
- [GitHub REST API versions](https://docs.github.com/en/rest/about-the-rest-api/api-versions)
- [GitHub REST request headers](https://docs.github.com/en/rest/using-the-rest-api/getting-started-with-the-rest-api)

# ADR 0135: Qualify one immutable customer GitHub context read

**Status:** Accepted

**Date:** 2026-09-08

## Context

ADR 0106 introduced a protected GitHub Contents adapter and local real-TLS
compatibility evidence. That proves the shipped client behavior, but it does
not prove that a customer's GitHub Cloud or GitHub Enterprise Server endpoint,
CA trust, API version, repository permissions, pinned revision, or production
credential broker work together. A successful credential lease alone also
does not prove that GitHub accepts the resulting credential for the reviewed
repository.

The adapter's `repository:contents:read` authority additionally revealed that
the shared external-broker grammar accepted only two-part scopes. The declared
three-part GitHub scope could therefore work with the development static broker
but not with the production external broker.

## Decision

1. Add a separately enabled customer qualifier for exactly one integration,
   repository, and document. It uses the production
   `GithubContextDocumentsBackend`, not a qualification-specific GitHub client.
2. Require a protected profile and protected integration snapshot that bind
   tenant, actor, integration, resource, logical reference, endpoint, API
   version, credential reference, repository, commit, path, expected Git blob,
   response bounds, deadline, and latency objective.
3. Support the exact GitHub Cloud endpoint or the GitHub Enterprise Server
   `/api/v3` endpoint. Use system trust for GitHub Cloud; allow either system
   trust or an explicit CA for Enterprise Server.
4. Require a current, qualified `CustomerCredentialBrokerQualificationReport`
   whose issued base case exactly matches the GitHub request and
   `repository:contents:read` scope. The live read then uses the same external
   broker client and protected workload-identity file.
5. Extend the shared credential-broker and plugin-mediation scope grammar to
   two or three bounded segments. This admits the already documented GitHub
   scope without admitting wildcards, empty segments, arbitrary whitespace, or
   unbounded scope strings.
6. Require a clean source revision and immutable application image digest. The
   generated report binds the exact profile, configuration, endpoint,
   repository, document, CA, broker prerequisite, broker authority, and
   observed revision by SHA-256 digest.
7. Retain no endpoint, tenant, actor, integration, credential reference,
   repository name, commit, path, blob SHA, title, locator, credential, or
   document content in the report. A failed upstream read becomes stable
   `not-qualified` checks without retaining response or exception detail.
8. Keep this integration-specific report outside the generic customer
   deployment aggregate. Customers that do not use GitHub must not be required
   to configure it; future integration-readiness profiles may consume it
   explicitly.

## Consequences

Operators can prove one real, immutable, least-authority customer repository
read and can rebind the minimized result offline to every non-secret and
protected input. GitHub content remains untrusted evidence and still passes
through the ordinary redaction and Evidence pipeline during investigations;
qualification does not persist the document.

The report does not qualify GitHub App installation, credential lifecycle,
organization governance, branch protection, sustained rate limits, secondary
throttling, network or service HA, certificate rotation, or any additional
repository, document, provider API, or service-catalog backend.

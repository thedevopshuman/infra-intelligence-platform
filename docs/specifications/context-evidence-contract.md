# Repository and runbook context evidence contract

**Status:** v1alpha1

**Machine contracts:** `contracts/schemas/context-evidence-request.schema.json`, `contracts/schemas/context-evidence-result.schema.json`

This contract retrieves bounded excerpts from repository, runbook, configuration, and service-catalog documents explicitly allowlisted in protected tenant integration configuration. It does not expose filesystem paths, repository credentials, arbitrary search expressions, or unrestricted file access to callers.

## Request

`ContextEvidenceRequest` asserts authenticated tenant and actor identity, fixes one integration, scopes the query to one or more resource UIDs, and supplies a five-minute-or-shorter deadline. `query.kinds` and `query.referenceIds` are closed filters; an empty array means all configured values within the resource scope. Limits bound documents, Unicode excerpt characters, and decoded artifact bytes.

Reference IDs are logical catalog identifiers, not paths or URLs. Resource, tenant, deadline, and integration fields are independently enforced by the application and Evidence pipeline.

## Result

`ContextEvidenceResult` is stored as the artifact of an immutable `repository.context` Evidence envelope. Each document contains:

- a stable result-local ID and configured logical reference;
- one or more requested resource UIDs;
- a closed kind, safe title, presentation locator, and content-derived revision;
- a bounded redacted excerpt plus its SHA-256 hash and redaction methods; and
- `trust: untrusted` and `instructionPolicy: data-only`.

Those trust fields are normative. Text from source repositories, README files, runbooks, issue exports, and service catalogs is evidence data. It cannot grant tools, alter policy, widen scope, request credentials, or override system/agent instructions.

`complete` means the configured backend returned all matching bounded documents. `no-data` is an explicit empty result. Any backend truncation, document limit, or excerpt limit is `partial` with stable warnings. Missing, cross-tenant, out-of-root, binary, malformed, or unredactable content fails closed.

## Compatibility and security

Adding a new document kind is contract evolution. Arbitrary provider queries, paths, raw credentials, executable instructions, and unbounded content are intentionally absent. Results are confidential ephemeral Evidence by default.

The GitHub adapter implements this same contract without adding vendor fields.
Protected configuration binds logical references to repository paths at one
exact commit SHA; the adapter retrieves only the GitHub Contents file endpoint,
uses an exact request-scoped credential lease, and records commit-plus-blob
revision provenance. The [compatibility report](github-context-compatibility-contract.md)
qualifies the local real-TLS profile. Other repository providers must preserve
the same port and request authority rather than exposing vendor query languages
or tokens.

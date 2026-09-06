# AI savings finding read contract

**Status:** `v1alpha1`
**Machine contracts:** `contracts/schemas/ai-savings-finding-page.schema.json`, `contracts/schemas/ai-savings-finding.schema.json`, `contracts/schemas/page-info.schema.json`, and `contracts/schemas/error.schema.json`

## Purpose

The read model exposes deterministic AI economics findings already committed by
the savings engine. It does not evaluate a rule, retrieve prompt or response
content, query a provider, or authorize an action.

`GET /v1/ai/economics/savings-findings` requires authenticated
`ai-economics:read` authority. Tenant identity comes only from the verified
actor. The caller supplies canonical UTC `start` and `end` timestamps for the
half-open `evaluatedAt` interval `[start, end)`, plus optional `limit` and
`cursor`. The interval may not exceed 31 days and `limit` is `1..100` with a
default of 20.

## Ordering and pagination

Items are ordered by `metadata.evaluatedAt` descending and then
`metadata.id` descending. The opaque `p1.` cursor binds its position to the
authenticated tenant and exact requested interval. A cursor cannot be reused
for another tenant or time scope. Invalid, non-canonical, or crossed cursors
fail with `pagination.cursor_invalid`; partial pages are never inferred from a
malformed storage result.

The response repeats the exact interval in `spec.scope`, records response time
in `metadata.generatedAt`, and uses the shared page information contract.
Every item remains a complete immutable `AiSavingsFinding` so its rule version,
observation, calculation status, advisory recommendation, and evidence
references stay auditable.

## Privacy and authority

- Findings contain normalized metadata, quantities, calculated cost, and
  opaque ledger/Evidence identifiers. They contain no prompts, responses, tool
  content, credentials, or source payloads.
- `calculated` money remains an estimate tied to stored cost records;
  `unpriced` and `unresolved` are displayed without substitution or guessing.
- Every recommendation has `requiresValidation: true`. Reading a finding does
  not grant proposal, approval, execution, provider, or Evidence-read
  authority.
- Evidence identifiers may be passed to their separately authorized owning read
  surfaces. This endpoint does not dereference them.
- Stored items are revalidated for contract, tenant, interval, and ordering
  before being returned. Corrupt or unavailable storage fails closed as
  `storage.unavailable`.

## Stable failures

- `400 request.invalid` for malformed or out-of-bound query parameters;
- `400 pagination.cursor_invalid` for invalid or cross-scope cursors;
- `401 authentication.required` for missing or invalid credentials;
- `403 policy.denied` when `ai-economics:read` is denied;
- `503 storage.unavailable` when the ledger cannot produce a trustworthy page.

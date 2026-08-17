# Evidence retention report contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/evidence-retention-report.schema.json`

`EvidenceRetentionReport` describes the deployment-owned lifecycle policy and current artifact-byte state for one authenticated tenant. It never deletes or rewrites the immutable Evidence envelope, content hash, storage reference, investigation citations, or audit history.

## Semantics

An artifact is eligible when its effective expiry is at or before `metadata.evaluatedAt`. `handling.expiresAt`, when present, is authoritative. Otherwise the deployment duration for `ephemeral`, `standard`, or `extended` is added to `metadata.recordedAt`. `legal-hold` artifacts never become eligible, even if malformed input contains an expiry.

The public endpoint is observe-only. `mode=observe` does not mutate storage, so `expired` is zero and `remainingEligible` equals `eligible`. The non-interactive worker produces internal `mode=expire` reports after deleting at most `policy.batchSize` artifact bodies in one tenant transaction. In an expire report, `eligible = expired + remainingEligible`. `storedBefore` counts artifact bodies present before evaluation; metadata records whose bodies were already expired are not counted.

`disabled` means automatic expiration is not enabled. `cleanup-required` means policy is enabled and eligible bytes remain. `current` means policy is enabled and none remain. The SHA-256 policy digest binds the enabled flag, class durations, and batch size without disclosing credentials or infrastructure details.

An `auditRef` exists exactly when the pass expired at least one artifact. The referenced record contains aggregate counts and policy digest only—never evidence IDs, artifact data, content hashes, source locators, queries, actor credentials, provider output, or exception text.

## Authority and errors

`GET /v1/operations/evidence/retention` requires Bearer authentication, the `platform-admin` role, and policy approval for `evidence-retention:read`. The tenant comes only from authenticated context and the endpoint accepts no query parameters. Worker expiration uses the fixed `iip-evidence-retention` system actor, the `system-retention` role, the exact enrolled tenant, and policy action `evidence-retention:expire`.

| HTTP status | Code | Meaning |
| --- | --- | --- |
| `400` | `request.invalid` | Any query field was supplied. |
| `401` | `authentication.required` / `authentication.invalid` | Bearer authentication failed. |
| `403` | `policy.denied` | The administrator role or policy authorization is absent. |
| `503` | `evidence.retention.unavailable` | Storage returned unavailable, corrupt, or inconsistent state. |

Automatic expiration is disabled by default. Enabling it is an explicit customer deployment decision requiring a reviewed backup, legal-hold, and data-retention policy.

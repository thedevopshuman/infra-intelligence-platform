# Evidence contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/evidence.schema.json`

Evidence is an immutable, tenant-scoped record describing one source artifact used to support or refute an investigation hypothesis. The contract carries provenance, handling metadata, and a content digest; artifact bytes remain in the evidence store and never travel in this envelope.

## Identity and tenancy

- `metadata.id` is an opaque platform-generated `evd_` identifier. It is unique within a tenant and is never derived from sensitive source content.
- `metadata.tenantId` is a consistency assertion. Authenticated transport context remains authoritative, and a mismatch fails closed.
- Repository keys, storage references, caches, and access decisions include the tenant even when an identifier appears globally unique.
- Evidence is immutable. A corrected retrieval or changed artifact produces a new evidence record and digest rather than overwriting the prior record.

## Provenance and time

`spec.source` identifies the provider, configured integration, and credential-free source locator. The locator describes where the artifact originated; it is not an access token and must never contain credentials or signed query parameters.

The three timestamps have distinct meanings:

| Field | Meaning |
| --- | --- |
| `observedAt` | When the represented fact occurred or was observed at the source. |
| `retrievedAt` | When the platform retrieved the source artifact. |
| `metadata.recordedAt` | When the immutable evidence record was committed. |

Application validation requires `observedAt <= retrievedAt <= recordedAt`. Resource references identify the tenant-scoped graph subjects to which the evidence applies. A secret-free normalized query may be retained for replay and audit.

## Artifact and content digest

`artifact.storageRef` is a logical evidence-store reference, not a provider URL. `contentHash` is `sha256:` plus the lowercase SHA-256 digest of the redacted, decoded artifact bytes. `encoding` describes the stored representation; consumers decode before verifying the digest. `sizeBytes` is the decoded byte count and is bounded to limit ingestion abuse.

The normalized `summary` is for retrieval and display. It cannot replace the artifact, and consumers treat both source material and summaries as untrusted input. Reports cite the evidence ID rather than copying artifact content.

## Handling and access

Redaction occurs before hashing and persistence. `redaction.status` is `applied` when one or more declared methods transformed the artifact, or `not-required` when policy determined that no transformation was needed. Credentials and raw secrets are prohibited regardless of status.

Sensitivity and retention class are policy inputs, not self-authorizing labels. Reading an evidence record or artifact requires tenant, actor, resource-scope, and sensitivity checks. Expiry makes the artifact unavailable under retention policy but does not silently rewrite historical report citations; consumers surface an unavailable-evidence state.

## Validation invariants

In addition to JSON Schema validation:

- authenticated tenant context must equal `metadata.tenantId`;
- every resource reference must resolve within that tenant or remain an explicit unresolved reference;
- timestamps must be monotonic as defined above;
- the stored artifact must match both `contentHash` and `sizeBytes` after decoding;
- `applied` redaction requires at least one method; and
- locators, queries, summaries, and handling metadata must pass secret and size checks.

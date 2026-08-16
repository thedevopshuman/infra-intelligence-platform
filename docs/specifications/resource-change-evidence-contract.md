# Resource change evidence request and result contracts

**Status:** v1alpha1

**Machine contracts:**

- `contracts/schemas/resource-change-evidence-request.schema.json`
- `contracts/schemas/resource-change-evidence-result.schema.json`

These contracts turn immutable resource observations into bounded, normalized “what changed?” Evidence. The first provider reads the platform's own accepted observation history, so a deployment image, replica target, configuration, relationship, status, creation, or deletion change can be cited without trusting a mutable current-state snapshot.

## Identity and bounds

Authenticated actor and tenant context is authoritative. Request identity fields are consistency assertions, and all resource references resolve inside that tenant before history is read. The initial query range is limited to seven days and 32 resources. It independently bounds returned changes, observations scanned per resource, decoded artifact bytes, and a deadline no more than five minutes after request time.

`integrationId` identifies the evidence source for provenance. The built-in provider uses `platform-history`; it receives no provider credential and cannot leave the tenant-scoped repository port. Future deployment-system adapters may reuse the normalized result only if they preserve the same authority and completeness semantics.

## Change semantics

Only accepted observations can produce changes. Stale and conflicting observations remain in the audit timeline but never become statements about effective state. A change record contains its kind, affected resource, observation and recording time, before/after observation hashes, changed JSON Pointer paths, and source cursor provenance. It deliberately omits before/after values, commit messages, and provider text; those could contain secrets or hostile instructions and are not required to establish that a field changed.

An empty `changeKinds` list means all declared kinds. Image and desired-replica paths are classified separately because they are high-value deployment signals. Other attributes, labels, and specification fields are configuration; relationships and status use dedicated kinds. A lifecycle transition to `deleted` is classified as deletion.

`complete` means every history page within the configured scan bound was processed and at least one matching change was returned. `no-data` means a complete scan found no matching change. `partial` carries `observation-limit`, `change-limit`, or both and may contain zero changes; absence from a partial result is never evidence that no change occurred.

## Privacy and investigation use

The normalized artifact is internal sensitivity with ephemeral retention. It passes the common redaction, hashing, and atomic Evidence store even though it contains paths rather than values. Investigations and user interfaces cite the Evidence ID. A later investigation-selection unit may correlate change kinds to root-cause classes, but must assess only the committed artifact and must preserve partial/no-data uncertainty.

## HTTP and SDK binding

`POST /v1/evidence/changes/queries` accepts `ResourceChangeEvidenceRequest` and returns the immutable Evidence envelope with `201 Created`. Python and TypeScript SDKs expose request/result types and the collection operation. Invalid contracts return `change.request.invalid`; policy, deadline, provider, and storage failures use the stable Evidence error set.

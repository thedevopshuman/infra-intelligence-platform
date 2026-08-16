# Resource and deployment change evidence

**Status:** Executable built-in provider

The platform can answer “what changed shortly before this incident?” from its own accepted immutable resource observations. No extra database, provider token, or Kubernetes permission is required beyond an already-running resource collector and the caller's tenant-scoped Evidence authorization.

## Collect changes

Submit a `ResourceChangeEvidenceRequest` to `POST /v1/evidence/changes/queries`. Use `integrationId: platform-history` for the built-in provider, one to 32 known resource UIDs, a time window no longer than seven days, and explicit scan/result/byte bounds. An empty `changeKinds` list selects every declared kind.

```bash
curl -X POST http://127.0.0.1:8080/v1/evidence/changes/queries \
  -H 'content-type: application/json' \
  -H "authorization: Bearer $IIP_DEV_BEARER_TOKEN" \
  --data @contracts/examples/resource-change-evidence-request.json
```

The response is an Evidence envelope. The normalized artifact stays in the tenant-scoped Evidence store and records changed paths, hashes, time, and source cursor provenance. It never copies changed values. This makes image, scaling, status, relationship, configuration, creation, and deletion activity useful for correlation without turning the result into a second copy of customer configuration.

## Completeness and sizing

Set `maxObservationsPerResource` from expected collection frequency and the selected range. When a resource has more retained observations than the bound, the result is `partial` with `observation-limit`. When matching changes exceed `maxChanges`, it is `partial` with `change-limit`. A partial result with zero changes does not mean no change occurred.

The current repository port scans from the oldest retained observation. For high-frequency production sources, use a bound that covers the investigation window and monitor partial-result frequency. A future time-indexed store query can optimize this without changing the contract.

## Security notes

- Authenticated actor and tenant identity override payload assertions.
- Only accepted observations become changes; stale/conflicting records remain audit history.
- The provider uses no ambient credential and cannot query another tenant.
- Changed paths and provider documents remain untrusted input and still pass redaction before hashing and persistence.
- Artifact bytes are internal until the platform adds a sensitivity-aware artifact-read contract.

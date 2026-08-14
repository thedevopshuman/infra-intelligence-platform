# Resource query contract

**Status:** v1alpha1

**Machine contracts:** `contracts/schemas/resource-neighborhood.schema.json`, `contracts/schemas/resource-timeline.schema.json`, `contracts/schemas/page-info.schema.json`, and `contracts/schemas/error.schema.json`

The resource query API exposes the two views required for initial infrastructure reasoning: the current graph around a resource and the immutable observation history of one resource. Both are read-only, tenant-scoped application use cases. Payload tenant fields and pagination cursors never grant access.

## Neighborhood

`ResourceNeighborhood` is a depth-one projection of the latest accepted resource state. `nodes` always contains the root resource. It also contains any page-edge endpoints that resolve to current resources in the same tenant. Unresolved provider references remain on edges without inventing placeholder resources.

Edges have canonical orientation. An `outbound` resource relationship points from the observed resource to its target; an `inbound` relationship points from its target to the observed resource. `observedResourceUid` preserves which latest projection asserted the edge. `direction` in the query is relative to the requested root. Exact relationship-type filters are ORed together and become part of the cursor scope.

The current view changes as observations arrive. Pagination is deterministic over the graph visible for each request, but it is not snapshot isolation across requests. Consumers needing a frozen graph must retain the returned resources and edges or use a future snapshot API.

## Timeline

`ResourceTimeline` returns immutable observation records in ascending storage-offset order. Accepted, stale, and conflicting observations remain distinguishable. Exact duplicate retries do not create another timeline item. The embedded canonical resource preserves provider observation time and cursor; `recordedAt` states when the platform durably recorded it.

The offset is a tenant-internal replay position, not a globally meaningful event identity. It can contain gaps because storage offsets are shared across tenants and resources.

## Pagination

`PageInfo.limit` is the applied page size, from 1 through 100. `hasMore: true` requires `nextCursor`; a terminal page prohibits it. Cursors are opaque `p1` tokens bound to tenant, resource UID, query kind, direction, and relationship filters. They contain no credentials and provide continuation, not authorization. Malformed, non-canonical, wrong-query, or wrong-tenant cursors fail with `pagination.cursor_invalid`.

Neighborhood cursors continue after a deterministic relationship edge ID. Timeline cursors continue after an exclusive observation offset. A client must follow the returned cursor without decoding or modifying it.

## Errors

External failures use the shared `Error` body and stable codes:

| HTTP status | Code | Meaning |
| --- | --- | --- |
| `400` | `request.invalid` | Query fields, limits, UID, or filters are invalid. |
| `400` | `pagination.cursor_invalid` | Cursor is malformed or does not belong to this exact query scope. |
| `403` | `policy.denied` | Authenticated actor cannot read the tenant-scoped resource. |
| `404` | `resource.not_found` | The resource is not visible in the actor's tenant. |
| `503` | `storage.unavailable` | The authoritative adapter is temporarily unavailable. |

Provider exceptions, SQL text, other-tenant existence, and stack traces never appear in the response.

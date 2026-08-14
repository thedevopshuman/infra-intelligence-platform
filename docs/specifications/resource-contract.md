# Resource contract

**Status:** v1alpha1  
**Machine contract:** `contracts/schemas/resource.schema.json`

A resource is the canonical, tenant-scoped representation of one infrastructure object. Each submission is an observation; the graph projects the latest accepted observation while preserving provenance and history through events.

## Identity

The platform UID is deterministic from:

```text
sha256(tenant_id + unit-separator + provider + unit-separator + type + unit-separator + external_id)
```

The first 32 lowercase hexadecimal characters are prefixed with `res_`. This makes retries and independent observers converge. `displayName`, labels, health, and location are never identity inputs.

The same external object appearing through two providers does not automatically merge. Identity reconciliation creates an explicit `same_as` relationship with confidence and provenance.

## Envelope

- `apiVersion` and `kind` select the contract.
- `metadata` owns platform scope, UID, observation time, and low-cardinality labels.
- `spec` owns source identity, display fields, provider attributes, and relationships.
- `status` owns normalized health and lifecycle projections.

Provider attributes are namespaced by the observation source and must exclude secrets. Consumers cannot assume an attribute exists across resource types.

## Relationships

Relationships are typed edges from the observed resource to a target provider identity or platform UID. Common vocabulary begins with `owns`, `contains`, `runs_on`, `routes_to`, `depends_on`, `reads_from`, `writes_to`, `deployed_from`, and `same_as`.

An edge has observation provenance and time in storage even though the compact ingress contract inherits those from the resource observation. Disappearance requires an explicit reconciliation event; absence in one partial observation is not deletion.

## Write semantics

- Upsert is idempotent for the same identity and observation version.
- Older observations may be retained but cannot replace a newer latest projection unless a source-specific reconciliation rule allows it.
- Deletion is a lifecycle observation/event, not physical removal.
- Tenant mismatch between authenticated context and payload fails closed.


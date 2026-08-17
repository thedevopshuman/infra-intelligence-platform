# Investigation signal catalog contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/investigation-signal-catalog.schema.json`

`InvestigationSignalCatalog` lets an operator review reusable evidence candidates once instead of requiring every caller to hand-author provider-neutral Kubernetes Event, context, resource-change, metric, and log selections. One profile is bound to exactly one tenant. Profile IDs and semantic versions identify operator intent; content digests bind the exact profile and the subset frozen into an accepted investigation.

The catalog contains logical integration IDs, closed query fragments, bounded limits, root-cause applicability, and deterministic interpretations. It cannot contain credentials, endpoints, vendor query languages, tenant wildcards, actor identity, resource scope, time scope, or execution authority. The first catalog profile intentionally excludes absolute, rolling, and seasonal metric baselines because their validity depends on each request's time range; callers may continue to declare those explicitly.

## Resolution and authority

The catalog is protected deployment configuration, not an API write surface. At most one profile may exist for a tenant. When an investigation omits or supplies an empty candidate list for a signal, the control plane may fill that signal from the tenant's profile. A non-empty caller list wins for that signal, so catalog resolution never mixes opaque caller and operator candidates.

Before durable dispatch, the server validates the caller request, resolves only the authenticated tenant's profile, freezes generated candidates into the accepted `InvestigationRequest`, and writes a server-owned `catalogSnapshot`. The snapshot records the profile identity, full-profile digest, generated selection references, and resolved-subset digest. A background worker verifies the frozen subset digest without consulting mutable current configuration.

Catalog candidates remain candidates, not grants. `evidenceTypes`, `allowedTools`, tool/evidence budgets, root-cause matching, per-provider policy, integration allowlists, credential leases, deadlines, and tenant/resource scope are still enforced. The report's `signalPlan` marks every candidate origin as `request` or `protected-catalog` and includes only digest/provenance metadata—never the protected query or provider configuration.

Malformed, duplicate-tenant, oversized, or semantically invalid catalogs fail at composition. Clients must not send `catalogSnapshot`; public execution rejects it because only the trusted preparation boundary can create one.

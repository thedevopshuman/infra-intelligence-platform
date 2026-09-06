# Customer PostgreSQL continuity qualification report contract

**Status:** `v1alpha1` executable customer-environment evidence

**Decision:** [ADR 0130](../decisions/0130-customer-postgresql-primary-promotion-continuity.md)

**Machine contracts:**
`contracts/schemas/customer-postgresql-continuity-profile.schema.json` and
`contracts/schemas/customer-postgresql-continuity-qualification-report.schema.json`

`CustomerPostgreSQLContinuityQualificationReport` is a minimized external
artifact proving that one explicit PostgreSQL endpoint returned as a writable
primary on a strictly newer WAL timeline while the exact platform release
recovered within declared application-traffic limits. It is not served by the
control-plane API and grants no database or provider authority.

## Protected input

`CustomerPostgreSQLContinuityProfile` is a mode-0600 operator input. It binds an
approved qualification tenant, actor, existing Resource, allowlisted synthetic
metric, bounded investigation policy, database name, observer role, and minimum
PostgreSQL major version. Credentials remain in separate protected files. The
retained report contains only a digest of this profile.

The observer role needs `CONNECT` to the selected database and permission to
execute the read-only built-in functions used by the probe. It does not need
table access, `pg_monitor`, `REPLICATION`, superuser, promotion, or provider API
authority. Environments that revoke public execution from these built-ins must
grant only the named functions required by the query.

## Closed observation

The three ordered phases are:

1. `baseline`: a writable-primary/TLS observation, bounded API and non-empty
   OTLP samples, and one completed investigation;
2. `promotion-observation`: an investigation submitted before the external
   promotion, continuous application probes, repeated read-only reconnects,
   and observation of a writable primary on a strictly higher WAL timeline;
3. `recovery`: clean bounded application probes and a newly completed
   investigation, followed by a stable final database observation.

The nineteen checks are ordered and derived. Structural, arithmetic, timing,
availability, outage, workflow, TLS, primary-state, timeline, source, and target
claims are recomputed rather than trusted from recorded check labels. A
qualified result requires all checks to pass.

The report retains timeline numbers because they are the portable promotion
proof. It hashes the explicit database target and observed server identities.
It never retains database host/address, port-bearing URL, database/user,
credential, certificate, tenant/actor/Resource values, raw row, response body,
or error detail.

## Claim boundary

The result proves a planned PostgreSQL primary promotion through one stable
endpoint. It does not prove why promotion occurred, which node or zone became
primary, provider fencing, split-brain prevention, synchronous replication,
zero acknowledged-write loss, a regional RPO/RTO, automatic failover, failback,
or disaster recovery. Pair it with the provider's topology and control-plane
evidence before making those broader claims.

Python and TypeScript SDKs expose the protected profile and minimized report as
transport types. Neither SDK receives promotion or database authority.

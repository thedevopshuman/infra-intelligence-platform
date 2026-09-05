# ADR 0097: Prove packaged N-1 upgrade and application rollback compatibility

**Status:** Accepted  
**Date:** 2026-09-05

## Context

Fresh install and same-version Helm upgrade tests do not prove that customer
data survives an application and schema change. The release bundle also needs
an executable link to the last supported version rather than an assertion that
append-only migrations are probably compatible.

## Decision

1. A release-candidate gate accepts an explicit ancestor revision and an
   absolute path to the verified target bundle. It refuses non-Kind clusters,
   a non-ancestor source, the same application version, a target without a
   newer migration, or a bundle whose revision differs from `HEAD`.
2. The gate builds the ancestor image with its exact release revision, installs
   the ancestor chart by immutable digest, and verifies its authenticated
   runtime identity and required migration.
3. It commits one tenant-scoped Resource through the public authenticated API,
   upgrades with the target bundle's packaged chart and OCI index digest, and
   requires the same Resource after the migration and rollout.
4. It rolls the application back to the ancestor Helm revision without
   reversing the forward-only database migration. The ancestor must become
   ready against the newer schema and return the same tenant data.
5. A final packaged upgrade must be idempotent: the target identity and data
   return, and every target migration appears exactly once.

## Consequences

The supported N-1 pair has executable local evidence for immutable artifacts,
runtime identity, forward schema compatibility, data preservation, application
rollback, and re-upgrade. The gate does not claim uninterrupted availability,
database-engine failover, customer workload compatibility, or permission to
reverse a schema migration; those remain environment-specific promotion gates.

# ADR 0053: Authenticated runtime version identity

**Status:** Accepted

## Context

Operators can verify release bundles and pin Helm workloads to an OCI digest, but the running console cannot state which application, contracts, storage requirement, source revision, chart, or digest produced the process answering a request. Mutable tags and browser asset filenames are insufficient evidence, while an unauthenticated diagnostic endpoint would unnecessarily advertise deployment fingerprints.

## Decision

Expose a tenant-contextual, authenticated runtime version report containing only non-secret identity facts. The application owns the report shape. The composition root supplies the package version, public contract version, newest packaged PostgreSQL migration, and validated optional build/deployment metadata.

Release image construction embeds the exact committed source revision. Helm supplies its chart version and configured image digest to application workloads. Development builds identify themselves as development and omit unverifiable fields. The service never guesses a digest from a tag, queries a container runtime socket, or exposes host and provider details.

## Consequences

- customers can copy exact runtime identity from the console or SDK;
- support and upgrade checks can distinguish application, chart, contract, and storage versions;
- release and Helm provenance remain connected to the running process;
- raw Docker development runs honestly show unavailable chart/digest evidence;
- authentication limits passive deployment fingerprinting;
- replica and worker aggregation remains a later operational-inventory concern.

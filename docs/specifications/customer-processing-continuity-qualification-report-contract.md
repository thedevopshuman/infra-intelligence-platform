# Customer processing continuity qualification contracts

**Status:** Accepted `v1alpha1` contract
**Decision:** [ADR 0129](../decisions/0129-customer-worker-receiver-processing-continuity.md)

## Purpose

The protected `CustomerProcessingQualificationProfile` declares the exact
tenant/actor, existing synthetic Resource, allowlisted metric, and bounded
investigation surface used by a customer-run processing test. It is operational
input, not public evidence, and may contain customer identifiers.

`CustomerProcessingContinuityQualificationReport` is the privacy-minimized
output. It binds one clean source revision, immutable application image,
customer Kubernetes target, HTTPS control-plane target, mutual-TLS receiver
target, and protected profile without retaining their raw values.

## Qualification profile

`customer-worker-receiver-pod-eviction-v1` performs four ordered phases:

1. baseline;
2. worker pod Eviction;
3. receiver pod Eviction; and
4. recovered capacity.

Each phase makes at least the declared number of authenticated runtime-identity
and non-empty OTLP metric attempts. Each also submits one bounded durable
investigation and requires its tenant-bound immutable report. The two
disruption submissions occur only after the original pod is no longer Ready
and the Deployment has fewer Ready replicas than desired.

Worker and receiver Evictions are sequential. Before each mutation the
qualifier requires an exact immutable image, at least two fully Ready replicas,
a zero-unavailable rollout, a matching PDB with `minAvailable` below desired,
and at least one allowed disruption. Every Eviction carries the exact observed
pod UID as a deletion precondition. Full capacity and a different Ready pod UID
must return within the declared recovery objective before continuing.

## Security and privacy

The API endpoint must be verified HTTPS. The OTLP endpoint must be verified
HTTPS with a client certificate and separate channel Bearer credential. The
client disables proxy discovery and redirects. Credential, certificate, CA,
and profile files must be regular non-symlink files and are never copied into
the report.

The report retains aggregate counts, bounded durations, software identity, and
pseudonymous SHA-256 bindings. It excludes tenant, actor, Resource, endpoint,
context, namespace, Deployment, Pod, UID, selector, credential, certificate,
response body, and raw error values. Its identifier is derived from canonical
metadata and spec content after omitting only the identifier.

## Non-claims

A qualified report proves one customer-cluster run with two sequential pod
Evictions and synthetic qualification traffic. It does not prove shared
database failover, involuntary node/zone/region loss, simultaneous failures,
sustained representative load, or a long-window production SLO.

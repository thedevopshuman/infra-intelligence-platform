# Customer deployment qualification report contract

**Status:** `v1alpha1`

**Machine contract:** `contracts/schemas/customer-deployment-qualification-report.schema.json`

`CustomerDeploymentQualificationReport` is the additive customer-environment
profile anticipated by ADR 0120. It binds four independently owned reports for
one exact application, chart, source revision, immutable image digest, and
explicitly selected Kubernetes cluster:

1. a cluster-mode `CustomerDeploymentPreflightReport`;
2. a post-install `DeploymentDiagnosticReport`;
3. a customer-profile `IngressAvailabilityQualificationReport`; and
4. the `CustomerContinuityQualificationReport` that already binds that ingress
   probe to one PDB-governed API-pod Eviction.

The ordered `evidence` list retains each report ID, SHA-256 digest, observed
status, and pass/reject result. Crossed release identities, sources, target
bindings, or the nested continuity-to-ingress digest chain are invalid input;
they do not become a partially successful report. A valid but blocked,
unhealthy, stale, out-of-order, or unsuccessful input produces
`not-qualified` evidence.

The health observation must strictly follow the continuity window. Every
observation must be within
`maximumEvidenceAgeSeconds` when the aggregate is created. Generation also
re-observes the namespace UID and Kubernetes server through the exact context
and requires the resulting cluster binding to equal the live preflight.

The report contains public versions, timestamps, counts, content digests, and
stable status/error codes. It does not contain context, namespace, release,
Deployment or Pod names, customer identifiers, URLs, repository locations,
credentials, Secret values, or raw provider/Kubernetes errors. `cdq_` is a
content-derived identifier over the complete metadata and specification.

`qualified` means only `single-cluster-control-plane-v1`: live dependencies
were present, the installed components were healthy after a successful
external API-pod disruption window, and every artifact described the same
release and cluster. The fixed `limitations` list keeps publication/signature/
vulnerability,
database HA/DR, worker/receiver continuity, integrations, live AI/provider
pricing, regional capacity/SLO, design-partner, legal, brand, and governance
gates outside this claim.

This operational evidence is not an API resource and is not added to the
control-plane OpenAPI surface. Python and TypeScript SDKs expose its transport
shape so release tooling can exchange it without importing implementation
classes.

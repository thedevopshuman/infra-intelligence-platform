# Customer deployment qualification report contract

**Status:** `v1alpha1`

**Machine contract:** `contracts/schemas/customer-deployment-qualification-report.schema.json`

`CustomerDeploymentQualificationReport` is the additive customer-environment
profile anticipated by ADR 0120 and extended by ADRs 0129, 0130, and 0131. It
binds seven independently owned reports for
one exact application, chart, source revision, immutable image digest, and
explicitly selected Kubernetes cluster:

1. a cluster-mode `CustomerDeploymentPreflightReport`;
2. a post-install `DeploymentDiagnosticReport`;
3. a customer-profile `IngressAvailabilityQualificationReport`;
4. a `CustomerOidcQualificationReport` that binds one customer issuer's
   verifier and browser prerequisites to the same API target;
5. the `CustomerContinuityQualificationReport` that already binds that ingress
   probe to one PDB-governed API-pod Eviction; and
6. a `CustomerProcessingContinuityQualificationReport` that proves durable
   metric intake and investigation completion across sequential worker and
   receiver pod Evictions; and
7. a `CustomerPostgreSQLContinuityQualificationReport` that proves the stable
   writer endpoint advanced to a writable PostgreSQL timeline while the
   application recovered within its declared limits.

The ordered `evidence` list retains each report ID, SHA-256 digest, observed
status, and pass/reject result. Crossed release identities, sources, target
bindings, the nested continuity-to-ingress digest chain, the OIDC report's
API/profile/issuer-metadata bindings, the processing report's
API/OTLP/profile/component bindings, or the database report's
API/OTLP/context/namespace/profile/target bindings are invalid input; they do not
become a partially successful report. A valid but blocked,
unhealthy, stale, out-of-order, or unsuccessful input produces
`not-qualified` evidence.

The OIDC prerequisite window must complete before the control-plane continuity
window, the control-plane window must complete before the processing window, the
processing window must complete before database promotion observation, and the
health observation must follow all continuity workflows. Every
observation must be within
`maximumEvidenceAgeSeconds` when the aggregate is created. Generation also
re-observes the namespace UID and Kubernetes server through the exact context
and requires the resulting cluster binding to equal the live preflight.

The report contains public versions, timestamps, counts, content digests, and
stable status/error codes. It does not contain context, namespace, release,
Deployment or Pod names, customer identifiers, URLs, repository locations,
credentials, Secret values, or raw provider/Kubernetes errors. `cdq_` is a
content-derived identifier over the complete metadata and specification.

`qualified` means only `single-cluster-database-oidc-prerequisites-v4`: live
dependencies were present; one customer issuer's discovery, JWKS, CORS,
claims, deployed session, and release prerequisites passed; the installed
components were healthy after successful sequential API, worker, and receiver
pod-disruption workflows; durable synthetic intake and investigation
completion passed; a real PostgreSQL timeline promotion was observed; and every
artifact described the same release and environment. The fixed `limitations`
list keeps interactive OIDC/MFA/session/logout/revocation/rotation/HA,
publication/signature/vulnerability, database topology/fencing/RPO and regional
DR, representative load, other integrations, live AI/provider pricing,
regional capacity/SLO, design-partner, legal, brand, and governance gates
outside this claim.

This operational evidence is not an API resource and is not added to the
control-plane OpenAPI surface. Python and TypeScript SDKs expose its transport
shape so release tooling can exchange it without importing implementation
classes.

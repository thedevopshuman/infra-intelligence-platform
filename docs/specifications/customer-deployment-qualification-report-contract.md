# Customer deployment qualification report contract

**Status:** `v1alpha1`

**Machine contract:** `contracts/schemas/customer-deployment-qualification-report.schema.json`

`CustomerDeploymentQualificationReport` is the additive customer-environment
profile anticipated by ADR 0120 and extended by ADRs 0129, 0130, 0131, 0132,
0133, and 0134. It binds ten independently owned reports for
one exact application, chart, source revision, immutable image digest, and
explicitly selected Kubernetes cluster:

1. a cluster-mode `CustomerDeploymentPreflightReport`;
2. a post-install `DeploymentDiagnosticReport`;
3. a customer-profile `IngressAvailabilityQualificationReport`;
4. a `CustomerOidcQualificationReport` that binds one customer issuer's
   verifier and browser prerequisites to the same API target;
5. a `CustomerPolicyQualificationReport` that binds reviewed allow and deny
   cases from one immutable customer policy snapshot to the same release;
6. a `CustomerCredentialBrokerQualificationReport` that binds the selected
   endpoint, protected authority profile, ordered authority set, and CA bundle
   to exact issuance and denial behavior through the production client;
7. a `CustomerOtlpReceiverQualificationReport` that binds the same API and
   OTLP targets to a pinned official Collector delivering metrics, logs, and a
   metadata-only GenAI span through verified client mTLS and separate channel
   credentials;
8. the `CustomerContinuityQualificationReport` that already binds that ingress
   probe to one PDB-governed API-pod Eviction; and
9. a `CustomerProcessingContinuityQualificationReport` that proves durable
   metric intake and investigation completion across sequential worker and
   receiver pod Evictions; and
10. a `CustomerPostgreSQLContinuityQualificationReport` that proves the stable
   writer endpoint advanced to a writable PostgreSQL timeline while the
   application recovered within its declared limits.

The ordered `evidence` list retains each report ID, SHA-256 digest, observed
status, and pass/reject result. Crossed release identities, sources, target
bindings, the nested continuity-to-ingress digest chain, the OIDC report's
API/profile/issuer-metadata bindings, the policy report's exact source/image
and endpoint/profile/snapshot-set bindings, the processing report's
credential-broker endpoint/profile/authority-set/CA bindings, the receiver
report's API/OTLP/profile/signal/trust/client-certificate bindings, the processing
report's API/OTLP/profile/component bindings, or the database report's
API/OTLP/context/namespace/profile/target bindings are invalid input; they do not
become a partially successful report. A valid but blocked,
unhealthy, stale, out-of-order, or unsuccessful input produces
`not-qualified` evidence.

The OIDC prerequisite window must complete before the policy observation, the
policy observation must complete before the credential-broker observation,
the credential-broker observation must complete before the receiver observation,
the receiver observation must complete before the control-plane continuity window,
the control-plane window must complete before the processing window, the
processing window must complete before database promotion observation, and the
health observation must follow all workflows. Every
observation must be within
`maximumEvidenceAgeSeconds` when the aggregate is created. Generation also
re-observes the namespace UID and Kubernetes server through the exact context
and requires the resulting cluster binding to equal the live preflight.

The report contains public versions, timestamps, counts, content digests, and
stable status/error codes. It does not contain context, namespace, release,
Deployment or Pod names, customer identifiers, URLs, repository locations,
credentials, Secret values, or raw provider/Kubernetes errors. `cdq_` is a
content-derived identifier over the complete metadata and specification.

`qualified` means only
`single-cluster-database-identity-policy-broker-receiver-prerequisites-v7`: live
dependencies were present; one customer issuer's discovery, JWKS, CORS,
claims, deployed session, and release prerequisites passed; reviewed policy
allow and deny cases remained bound to one immutable snapshot; the installed
credential broker issued only the reviewed exact authority and denied six
single-field mutations; a pinned official Collector delivered all three
supported signal types to the exact installed receiver; the installed components were healthy after successful sequential API, worker, and receiver
pod-disruption workflows; durable synthetic intake and investigation
completion passed; a real PostgreSQL timeline promotion was observed; and every
artifact described the same release and environment. The fixed `limitations`
list keeps interactive OIDC/MFA/session/logout/revocation/rotation/HA, complete
policy coverage and policy lifecycle/HA/audit, broker identity and provider
credential lifecycle/HA/audit/non-Bearer schemes, permanent Collector
configuration/queue recovery and PKI lifecycle/HA, publication/signature/
vulnerability, database topology/fencing/RPO and regional DR, representative
load, other integrations, live AI/provider pricing, regional capacity/SLO,
design-partner, legal, brand, and governance gates outside this claim.

This operational evidence is not an API resource and is not added to the
control-plane OpenAPI surface. Python and TypeScript SDKs expose its transport
shape so release tooling can exchange it without importing implementation
classes.

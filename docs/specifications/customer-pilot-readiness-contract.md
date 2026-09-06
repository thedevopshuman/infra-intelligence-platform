# Customer pilot readiness contracts

**Status:** `v1alpha1`

**Decision:** [ADR 0143](../decisions/0143-customer-pilot-readiness-aggregation.md)

The customer pilot readiness contracts answer whether one exact published,
organizationally signed release has the complete current evidence required to
enter a private AI FinOps design-partner evaluation. They do not approve or
start that evaluation and do not claim production or public-launch readiness.

## Protected profile

`CustomerPilotReadinessProfile` fixes:

- application and chart versions, clean source revision, release-manifest
  digest, and both OCI index digests;
- organizational signature-policy and publication-target-set digests;
- selected customer cluster, environment, and control-plane target digests;
  and
- profile, foundation-evidence, customer-evidence, clock-skew, and report
  validity limits.

The profile can link otherwise pseudonymous customer evidence and must be an
owner-only regular file. Its `cprp_` identifier is content-derived from the
review timestamps and complete specification.

## Transportable report

`CustomerPilotReadinessReport` binds seven owning artifacts:

1. `ReleaseReadinessReport` with `locally-qualified` status;
2. `ReleasePublicationReport` with `published-unsigned` status;
3. `ReleaseSignatureVerificationReport` with organizational keyless
   `verified` status;
4. `CustomerDeploymentQualificationReport` with `qualified` status;
5. `ControlPlaneLoadQualificationReport` with `qualified` status and a window
   starting after deployment qualification;
6. `CustomerAiFinopsPrerequisiteReport` with `prerequisites-ready` status; and
7. `CustomerAiFinopsFlowQualificationReport` with `qualified` status.

The release manifest, source, versions, published index digests, signed index
digests, installed image, target identity, customer environment, exact input
file digests, and validity windows must agree. The `cpr_` report identifier is
content-derived from the complete minimized artifact. A candidate report can
never outlive the reviewed profile, its profile-age limit, either evidence-age
limit, either expiring AI FinOps input, or its own configured validity period.
Status failure and temporal freshness are reported independently.

`design-partner-candidate` means only that this closed preflight passed. Three
external gates always remain: the partner must actually operate and accept the
product, the customer must qualify its longer-running production operating
profile, and public launch needs accepted license/legal/brand/governance
decisions.

## Machine contracts

- `contracts/schemas/customer-pilot-readiness-profile.schema.json`
- `contracts/schemas/customer-pilot-readiness-report.schema.json`
- `contracts/examples/customer-pilot-readiness-profile.json`
- `contracts/examples/customer-pilot-readiness-report.json`

The Python and TypeScript SDKs expose only the protected profile and minimized
report transport shapes. They do not expose source-report internals, run a
provider call, publish or sign an image, generate load, or grant promotion
authority.

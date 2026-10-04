# Customer pilot readiness contracts

**Status:** `v1alpha2`

**Decisions:** [ADR 0143](../decisions/0143-customer-pilot-readiness-aggregation.md),
[ADR 0145](../decisions/0145-require-sustained-workload-before-private-pilot.md),
[ADR 0146](../decisions/0146-customer-reviewed-planned-failure-overlap.md),
and
[ADR 0152](../decisions/0152-require-operational-alert-qualification-before-private-pilot.md)

The customer pilot readiness contracts answer whether one exact published,
organizationally signed release has the complete current evidence required to
enter a private AI FinOps design-partner evaluation. They do not approve or
start that evaluation and do not claim production or public-launch readiness.

## Protected profile

`CustomerPilotReadinessProfile` fixes:

- application and chart versions, clean source revision, release-manifest
  digest, and both OCI index digests;
- organizational signature-policy and publication-target-set digests;
- selected customer cluster, namespace, environment, control-plane target,
  OTLP target, sustained-workload/failure-overlap profile digests, and the
  operational-alert profile and complete binding-set digests; and
- profile, foundation-evidence, customer-evidence, clock-skew, and report
  validity limits.

The `operationalAlertBindingSetDigest` is the SHA-256 digest of the
RFC 8785/JCS-equivalent canonical JSON serialization of the complete
`CustomerOperationalAlertQualificationReport.spec.bindings` object. It binds
the alert environment, release, target, profile, route, and trust inputs as one
set; exact cluster, namespace, and Prometheus relationships are additionally
checked directly rather than inferred from this digest.
The three new protected fields are `namespaceBindingDigest`,
`operationalAlertProfileDigest`, and `operationalAlertBindingSetDigest`.

The profile can link otherwise pseudonymous customer evidence and must be an
owner-only regular file. Its `cprp_` identifier is content-derived from the
review timestamps and complete specification. Its required
`qualificationLevel` is `customer-ai-finops-design-partner-v2`.

## Transportable report

`CustomerPilotReadinessReport` binds ten owning artifacts:

1. `ReleaseReadinessReport` with `locally-qualified` status;
2. `ReleasePublicationReport` with `published-unsigned` status;
3. `ReleaseSignatureVerificationReport` with organizational keyless
   `verified` status;
4. `CustomerDeploymentQualificationReport` with `qualified` status;
5. `ControlPlaneLoadQualificationReport` with `qualified` status and a window
   starting after deployment qualification;
6. `CustomerSustainedWorkloadQualificationReport` with `qualified` status, an
   exact protected profile, matching API/OTLP targets, and a window starting
   after deployment qualification;
7. `CustomerFailureOverlapQualificationReport` with `qualified` status, the
   selected protected overlap profile, and exact bindings back to the same
   deployment and sustained-workload files;
8. `CustomerAiFinopsPrerequisiteReport` with `prerequisites-ready` status;
9. `CustomerAiFinopsFlowQualificationReport` with `qualified` status; and
10. `CustomerOperationalAlertQualificationReport` with `qualified` status,
    the exact protected alert profile, and the complete `ai-finops-v0` rule
    set.

The deployment, prerequisite, and same-invocation subjects must all bind the
current `production-ai-finops-v1` deployment profile. Historical v0 artifacts
remain readable at their owning transport boundaries but cannot satisfy this
current v2 pilot admission profile.

The release manifest, source, versions, published index digests, signed index
digests, installed image, target identity, customer environment, sustained
workload/overlap profiles, API/OTLP targets, alert cluster and namespace,
operational-alert profile/binding set, exact input file digests, and validity
windows must agree. The alert report must carry the same release and migration
as the deployment, its Prometheus target must equal the same-invocation AI
FinOps target, and its observation must start after deployment qualification.
The aggregate `customerEnvironmentSetDigest` includes the namespace plus the
operational-alert profile and binding-set digests.

Every source-report binding equals its evidence-item digest. The `cpr_` report
identifier is content-derived from the complete minimized artifact. A
candidate report can never outlive the reviewed profile, its profile-age
limit, either evidence-age limit, the sustained, failure-overlap, AI FinOps,
or operational-alert inputs, or its own configured validity period. Status
failure and temporal freshness are reported independently. The alert source
adds the stable `customer-operational-alerts`,
`operational-alert-environment-chain`, and
`post-deployment-operational-alert-window` checks, for 24 ordered checks in the
v2 report. It retains the exact source file as
`customerOperationalAlertReportDigest` and exposes only the aggregate
`operationalAlertStartedAt` and `operationalAlertQualifiedAt` timestamps from
that source.

`design-partner-candidate` means only that this closed preflight passed. Three
external gates always remain: the partner must actually operate and accept the
product, the customer must qualify its longer-running production operating
profile, and public launch needs accepted license/legal/brand/governance
decisions. The failure-overlap input proves only the customer-approved
private-pilot core proxy and planned scenarios; it does not qualify
representative production traffic or involuntary failures.

The earlier `customer-ai-finops-design-partner-v1` profile and nine-input
report are historical, narrower artifacts. They cannot satisfy current pilot
admission and must not be relabeled. Migration requires a new v2 protected
profile, a fresh post-deployment `ai-finops-v0` operational-alert report, all
ten exact source files, and a newly assessed report. The profile and report
envelope advance to `iip.platform/v1alpha2`; their embedded
`subject.contractsApiVersion` remains `iip.platform/v1alpha1` because it
identifies the deployed platform contract consumed by the v1 sources. Current
schema and offline verification reject v1 or mixed-semantic evidence.

## Machine contracts

- `contracts/schemas/customer-pilot-readiness-profile.schema.json`
- `contracts/schemas/customer-pilot-readiness-report.schema.json`
- `contracts/examples/customer-pilot-readiness-profile.json`
- `contracts/examples/customer-pilot-readiness-report.json`

The Python and TypeScript SDKs expose only the protected profile and minimized
report transport shapes. They do not expose source-report internals, run a
provider call, publish or sign an image, generate load, configure monitoring,
create a synthetic alert, or grant pilot/promotion authority.

The checked-in profile and report examples demonstrate the current closed
shapes and their internal profile/report digest relationships. Upstream source
report IDs and file digests in those two examples are pseudonymous illustrative
values, not a runnable ten-file admission bundle. The executable fixture in
`tests/test_customer_pilot_readiness.py` constructs and verifies the complete
cross-bound chain; a real assessment always recalculates every supplied file.

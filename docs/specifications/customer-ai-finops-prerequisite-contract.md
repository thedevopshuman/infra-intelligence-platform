# Customer AI FinOps prerequisite contracts

**Status:** `v1alpha1`

These operational contracts bind the independently generated evidence needed
before running a customer AI FinOps V0 end-to-end qualification. They do not
represent usage, cost, invoices, or proof that a single invocation traversed
the complete path.

## Protected profile

`CustomerAiFinopsPrerequisiteProfile` is a reviewed, non-transportable input.
Its schema is
[`customer-ai-finops-prerequisite-profile.schema.json`](../../contracts/schemas/customer-ai-finops-prerequisite-profile.schema.json)
and its shape is illustrated by
[`customer-ai-finops-prerequisite-profile.json`](../../contracts/examples/customer-ai-finops-prerequisite-profile.json).

The profile selects:

- exact application, chart, source revision, and immutable image identity;
- one customer environment binding;
- `aws.bedrock` `ConverseStream` with a direct provider request, asynchronous
  OpenTelemetry delivery, and metadata-only persistence;
- one exact tenant-bound catalog and qualification-policy generation;
- provider-published or operator-managed calculated-estimate pricing;
- the certified V0 Prometheus/Grafana presentation; and
- profile age, evidence age, clock-skew, and report-validity objectives.

The environment and price tenant are authority-bearing inputs. They are hashed
into the report and are never copied into it. The profile must be stored outside
Git, owned by the invoking user, and mode `0600`.

## Minimized report

`CustomerAiFinopsPrerequisiteReport` is the transportable output. Its schema is
[`customer-ai-finops-prerequisite-report.schema.json`](../../contracts/schemas/customer-ai-finops-prerequisite-report.schema.json)
and its example is
[`customer-ai-finops-prerequisite-report.json`](../../contracts/examples/customer-ai-finops-prerequisite-report.json).

The report has a closed qualification level and boundary:

- `qualificationLevel`: `customer-ai-finops-prerequisites-v1`
- `qualificationBoundary`: `prerequisite-aggregation-only`
- `status`: `prerequisites-ready` or `not-ready`

It contains six ordered evidence entries for `ReleaseReadinessReport`,
`AiFinopsRuntimeCompatibilityReport`,
`CustomerDeploymentQualificationReport`,
`CustomerOtlpReceiverQualificationReport`,
`CustomerBedrockQualificationReport`, and
`AiPriceCatalogQualificationReport`. Missing and rejected evidence remains
visible with stable error codes.

Ten content and binding digests make the report reproducible without retaining
customer values. Sixteen ordered checks cover source cleanliness, profile age,
release identity, the current `production-ai-finops-v1` deployment profile,
including its verified PostgreSQL transport requirement, all six source
statuses, release-to-runtime and deployment-to-receiver chains, catalog
selection, metadata-only direct collection, freshness, and output
minimization.

The report expires at the earliest of its configured validity, the Bedrock
qualification expiry, and the catalog qualification expiry. Its `cafp_`
identifier is derived from all retained metadata and specification fields.

## Privacy and trust boundary

The report cannot contain environment or tenant identifiers, endpoints,
regions, model identifiers, credentials, secrets, prompts, responses, token
quantities, rates, or monetary values. It retains only counts already bounded
by the source contracts and non-reversible SHA-256 bindings.

`prerequisites-ready` means the six independent prerequisites are current and
consistent. It does not mean that one live Bedrock invocation reached the
customer Collector, deployed IIP ledger, cost engine, or dashboard. The seven
ordered limitations are mandatory and cannot be removed by configuration.

The profile and report are offline operational artifacts and are not served by
the control-plane API, so this additive contract does not change OpenAPI.

The transport schema and SDK type continue to recognize retained
`production-ai-finops-v0` reports for offline inspection. Current generation
always emits `production-ai-finops-v1`; a v0 customer deployment fails the
deployment-profile check and a v0 prerequisite report cannot enter the current
same-invocation or pilot admission chains.

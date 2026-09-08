# Customer pilot readiness

**Status:** Executable `customer-ai-finops-design-partner-v2` preflight

Run this workflow after the exact release and customer-environment gates have
completed. It creates one minimized artifact answering whether that build can
enter a private AI FinOps design-partner evaluation. It does not install,
publish, sign, generate traffic, call a provider, start a pilot, or approve
production use.

## Required evidence

Retain the exact current files from these owning workflows:

1. local release readiness;
2. exact registry publication;
3. organizational keyless signature verification;
4. customer deployment qualification;
5. bounded customer control-plane load qualification;
6. bounded sustained customer core-workload qualification;
7. customer-approved planned-failure overlap qualification;
8. customer AI FinOps prerequisite aggregation; and
9. customer same-invocation AI FinOps qualification; and
10. customer operational-alert qualification for the complete
    `ai-finops-v0` rule set and one synthetic firing/recovery route.

Both load workflows must run after the customer deployment report is generated.
The sustained report must bind the protected workload profile selected in this
preflight, the same API target, and the deployment-qualified OTLP receiver. The
failure-overlap report must bind that exact deployment report, sustained report
and profile, cluster, API target, and OTLP target. The live AI workflow already
binds its prerequisite report, and that prerequisite report must bind the same
local-readiness and customer-deployment files supplied here. The operational-
alert report must start after deployment qualification and bind the same exact
release, required migration, cluster, and namespace. Its Prometheus target must
equal the one used by the same-invocation AI FinOps report, and its report must
still be current when pilot readiness is assessed.

## Prepare the protected profile

Copy `contracts/examples/customer-pilot-readiness-profile.json` outside the
repository, replace every example value with the exact reviewed release and
customer binding digests, and update its review/expiry/objective values. Keep
the file mode at `0600` because its digest set can link customer operational
evidence.

Copy the `profileDigest` and `otlpTargetBindingDigest` from the sustained
workload report into `sustainedWorkloadProfileDigest` and `otlpTargetDigest`.
The sustained report's API target must equal `controlPlaneTargetDigest`.
Copy the `profileDigest` from the qualified failure-overlap report into
`failureOverlapProfileDigest`. Its deployment, sustained-workload, cluster,
API, and OTLP bindings must match the exact files and values selected here.

Set `qualificationLevel` to
`customer-ai-finops-design-partner-v2` and the profile envelope `apiVersion` to
`iip.platform/v1alpha2`. Copy the deployment report's
`namespaceBindingDigest` and the alert report's `profileDigest` into the
corresponding `namespaceBindingDigest` and `operationalAlertProfileDigest`
fields. Set `operationalAlertBindingSetDigest` to the SHA-256 digest of the
RFC 8785/JCS-equivalent canonical JSON serialization of the alert report's
complete `spec.bindings` object. Generate it with the assessor rather than
hashing a pretty-printed fragment or selecting individual fields:

```bash
PYTHONPATH=scripts:src:sdks/python/src .venv/bin/python \
  scripts/assess_customer_pilot_readiness.py \
  operational-alert-binding-set-digest \
  --operational-alerts /evidence/customer-operational-alert-qualification-report.json
```

The assessor separately proves that this set belongs to the selected
deployment and AI FinOps Prometheus target.

After editing, compute the content-derived ID, place the returned value in
`metadata.id`, and repeat until the command returns that same value:

```bash
chmod 0600 /protected/path/customer-pilot-readiness-profile.json
PYTHONPATH=scripts:src:sdks/python/src .venv/bin/python \
  scripts/assess_customer_pilot_readiness.py profile-id \
  --profile /protected/path/customer-pilot-readiness-profile.json
```

Calculate the publication target-set digest from the validated publication
report with:

```bash
PYTHONPATH=scripts:src:sdks/python/src .venv/bin/python \
  scripts/assess_customer_pilot_readiness.py publication-target-set-digest \
  --release-publication /evidence/release-publication-report.json
```

Place that digest in the protected profile; repository names never enter the
pilot report. The cluster, environment, and control-plane target digests come
from the owning customer reports.

## Assess the candidate

```bash
IIP_CUSTOMER_PILOT_READINESS_PROFILE=/protected/path/customer-pilot-readiness-profile.json \
IIP_RELEASE_READINESS_REPORT=/evidence/release-readiness-report.json \
IIP_RELEASE_PUBLICATION_REPORT=/evidence/release-publication-report.json \
IIP_RELEASE_SIGNATURE_REPORT=/evidence/release-signature-verification-report.json \
IIP_CUSTOMER_DEPLOYMENT_QUALIFICATION_REPORT=/evidence/customer-deployment-qualification-report.json \
IIP_CONTROL_PLANE_LOAD_REPORT=/evidence/control-plane-load-qualification-report.json \
IIP_CUSTOMER_SUSTAINED_WORKLOAD_REPORT=/evidence/customer-sustained-workload-qualification-report.json \
IIP_CUSTOMER_FAILURE_OVERLAP_REPORT=/evidence/customer-failure-overlap-qualification-report.json \
IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_REPORT=/evidence/customer-ai-finops-prerequisite-report.json \
IIP_CUSTOMER_AI_FINOPS_FLOW_REPORT=/evidence/customer-ai-finops-flow-qualification-report.json \
IIP_CUSTOMER_OPERATIONAL_ALERT_REPORT=/evidence/customer-operational-alert-qualification-report.json \
IIP_CUSTOMER_PILOT_READINESS_REPORT=/evidence/customer-pilot-readiness-report.json \
  make assess-customer-pilot-readiness PYTHON=.venv/bin/python
```

A successful command writes `design-partner-candidate`. Valid but stale,
expired, locally insufficient, unsigned, unhealthy, under-objective, or
otherwise unsuccessful evidence writes `not-candidate` and exits nonzero.
Malformed, crossed, symlinked, over-sized, or output-overlapping inputs fail
without emitting a new credible report.

The generated report expires at the earliest applicable profile, evidence-age,
sustained-workload, failure-overlap, AI FinOps, operational-alert, or report-
validity boundary. An
unsuccessful but temporally current input fails its owning qualification check
without being mislabeled as stale.

## Verify retained evidence

Use the same profile and ten source paths, including
`IIP_CUSTOMER_OPERATIONAL_ALERT_REPORT`:

```bash
make verify-customer-pilot-readiness-report PYTHON=.venv/bin/python
```

Verification revalidates every source report, recomputes every file and
cross-binding digest, reconstructs the report at its original assessment time,
requires the same clean source revision, checks the operational-alert report's
`ai-finops-v0` profile and post-deployment window, and rejects an expired
source or aggregate report. A copied report without its exact source evidence
cannot be promoted.

## Migration from the historical v1 preflight

The nine-input `customer-ai-finops-design-partner-v1` profile and report do not
satisfy current admission. Retain them as historical evidence; do not edit or
relabel them. Create a new v2 profile with the namespace and operational-alert
bindings, run a fresh post-deployment `ai-finops-v0` alert qualification, and
assess all ten exact source files. Current schema and offline verification
reject v1 or mixed-semantic inputs.

## Privacy and authority boundary

The portable report contains only public release identity, digests, bounded
timestamps and ages, stable results, limitations, and external-gate IDs. It
contains no registry repository, customer or tenant identifier, endpoint,
credential, model, region, application/team label, prompt/response, trace/span,
token quantity, price, or amount.

`design-partner-candidate` is a preflight status, not approval. Before a public
or production release, complete actual partner operation and acceptance,
representative production-traffic qualification, involuntary node/zone/region
and automatic database-failover objectives, long-window SLOs, and the
license/legal/brand/governance decisions listed in the report.

The assessor also has no monitoring authority: it does not install or change
rules, fire an alert, change a route, read notification bodies, stop an IIP
component, or contact a person. The customer's monitoring owner performs the
synthetic lifecycle under the separate alert-qualification runbook.

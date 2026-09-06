# Customer pilot readiness

**Status:** Executable private design-partner preflight

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
7. customer AI FinOps prerequisite aggregation; and
8. customer same-invocation AI FinOps qualification.

Both load workflows must run after the customer deployment report is generated.
The sustained report must bind the protected workload profile selected in this
preflight, the same API target, and the deployment-qualified OTLP receiver. The
live AI workflow already binds its prerequisite report, and that prerequisite
report must bind the same local-readiness and customer-deployment files
supplied here.

## Prepare the protected profile

Copy `contracts/examples/customer-pilot-readiness-profile.json` outside the
repository, replace every example value with the exact reviewed release and
customer binding digests, and update its review/expiry/objective values. Keep
the file mode at `0600` because its digest set can link customer operational
evidence.

Copy the `profileDigest` and `otlpTargetBindingDigest` from the sustained
workload report into `sustainedWorkloadProfileDigest` and `otlpTargetDigest`.
The sustained report's API target must equal `controlPlaneTargetDigest`.

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
IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_REPORT=/evidence/customer-ai-finops-prerequisite-report.json \
IIP_CUSTOMER_AI_FINOPS_FLOW_REPORT=/evidence/customer-ai-finops-flow-qualification-report.json \
IIP_CUSTOMER_PILOT_READINESS_REPORT=/evidence/customer-pilot-readiness-report.json \
  make assess-customer-pilot-readiness PYTHON=.venv/bin/python
```

A successful command writes `design-partner-candidate`. Valid but stale,
expired, locally insufficient, unsigned, unhealthy, under-objective, or
otherwise unsuccessful evidence writes `not-candidate` and exits nonzero.
Malformed, crossed, symlinked, over-sized, or output-overlapping inputs fail
without emitting a new credible report.

The generated report expires at the earliest applicable profile, evidence-age,
sustained-workload, AI FinOps input, or report-validity boundary. An
unsuccessful but temporally current input fails its owning qualification check
without being mislabeled as stale.

## Verify retained evidence

Use the same profile and eight source paths:

```bash
make verify-customer-pilot-readiness-report PYTHON=.venv/bin/python
```

Verification revalidates every source report, recomputes every file and
cross-binding digest, reconstructs the report at its original assessment time,
requires the same clean source revision, and rejects an expired report. A
copied report without its exact source evidence cannot be promoted.

## Privacy and authority boundary

The portable report contains only public release identity, digests, bounded
timestamps and ages, stable results, limitations, and external-gate IDs. It
contains no registry repository, customer or tenant identifier, endpoint,
credential, model, region, application/team label, prompt/response, trace/span,
token quantity, price, or amount.

`design-partner-candidate` is a preflight status, not approval. Before a public
or production release, complete actual partner operation and acceptance,
customer-reviewed representative and failure-overlap workloads, recovery
objectives, and the license/legal/brand/governance decisions listed in the
report.

# Customer AI FinOps prerequisite qualification

This host-side gate binds the current release, deployed customer environment,
customer Collector, live Bedrock, and production catalog evidence before the
true end-to-end V0 qualification is attempted. It performs no provider call,
sends no telemetry, and changes no deployment.

## Required evidence

Generate and independently verify these six reports first:

1. `dist/release-readiness-report.json`
2. `dist/ai-finops-runtime-compatibility-report.json`
3. `dist/customer-deployment-qualification-report.json`
4. `dist/customer-otlp-receiver-qualification-report.json`
5. `dist/customer-bedrock-qualification-report.json`
6. `dist/ai-price-catalog-qualification-report.json`

All source-bound reports must describe the same clean Git revision. The
release, deployment, receiver, and Bedrock reports must agree on application
and immutable image identity. Release readiness must contain the exact runtime
report digest, and customer deployment must contain the exact receiver report
and target-binding digests. The price report must be a current
`production-catalog` qualification for the catalog and policy selected by the
protected profile.

## Prepare the protected profile

Copy the example outside the repository, replace every placeholder with the
reviewed release, environment, and catalog selection, and protect it:

```sh
cp contracts/examples/customer-ai-finops-prerequisite-profile.json \
  /secure/iip/customer-ai-finops-prerequisite-profile.json
chmod 600 /secure/iip/customer-ai-finops-prerequisite-profile.json
```

The first certified profile is Bedrock `ConverseStream`, asynchronous OTel,
metadata-only collection, calculated-estimate pricing, and Prometheus/Grafana.
The environment and price tenant remain only in this protected input.

## Generate the aggregate

From the same clean checkout named by the profile:

```sh
make qualify-customer-ai-finops-prerequisites \
  IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE=/secure/iip/customer-ai-finops-prerequisite-profile.json
```

Override the six report variables only when the verified reports are stored
outside their default `dist/` locations. The command exits non-zero for missing,
stale, expired, rejected, or crossed evidence. A generated `not-ready` report
still records the bounded failure inventory.

## Verify retained evidence

Verification reloads the profile and all six source files, recomputes their raw
file digests and every cross-binding, requires the current source tree to be
clean, and rejects an expired or non-ready report:

```sh
make verify-customer-ai-finops-prerequisite-report \
  IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE=/secure/iip/customer-ai-finops-prerequisite-profile.json
```

The minimized output is safe to retain with release evidence. The protected
profile and any provider credentials are not.

## Interpret the result

`prerequisites-ready` means the separate evidence is current and internally
consistent. It does **not** prove that the live Bedrock request observed by the
provider qualifier entered the selected customer Collector or the deployed IIP
usage and cost ledgers. Do not present this report as an end-to-end, invoice,
availability, or backend-portability certification. The exact correlation
mechanism is defined by the [AI invocation observation
contract](../specifications/ai-invocation-observation-contract.md); a separate
customer-flow report must still execute and bind the selected live path.

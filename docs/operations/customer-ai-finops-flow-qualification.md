# Customer AI FinOps same-invocation qualification

This gate makes one live, potentially billable AWS Bedrock `ConverseStream`
request and proves that the same metadata-only invocation reaches the selected
customer Collector, IIP usage/attribution/cost path, Prometheus aggregate, and
provisioned Grafana dashboard. It is never run by `make verify`.

## 1. Complete the prerequisites

First generate and verify the aggregate described in [customer AI FinOps
prerequisites](customer-ai-finops-prerequisites.md). Keep these exact files:

- the mode-`0600` prerequisite profile;
- the minimized prerequisite report;
- the mode-`0600` customer Bedrock profile used by the aggregate; and
- the clean checkout and immutable image named by those files.

The prerequisite report must remain current through the complete live run.

## 2. Prepare the protected flow profile

Copy the example outside the repository and set its exact release, environment,
expected protected application/team, and four HTTPS targets:

```sh
cp contracts/examples/customer-ai-finops-flow-qualification-profile.json \
  /secure/iip/customer-ai-finops-flow-profile.json
chmod 600 /secure/iip/customer-ai-finops-flow-profile.json
```

Set `spec.prerequisites.profileDigest` and `spec.bedrock.profileDigest` to
canonical document digests. Set `spec.prerequisites.reportDigest` to the SHA-256
of the exact report file bytes. The report verifier recomputes all three.

```sh
PYTHONPATH=scripts .venv/bin/python -c \
  'import json,sys; from pathlib import Path; from qualify_customer_ai_finops_flow import _digest; print(_digest(json.loads(Path(sys.argv[1]).read_text())))' \
  /secure/iip/customer-ai-finops-prerequisite-profile.json
shasum -a 256 dist/customer-ai-finops-prerequisite-report.json
PYTHONPATH=scripts .venv/bin/python -c \
  'import json,sys; from pathlib import Path; from qualify_customer_ai_finops_flow import _digest; print(_digest(json.loads(Path(sys.argv[1]).read_text())))' \
  /secure/iip/customer-bedrock-profile.json
```

The expected application/team must already be selected by the active protected
attribution policy for the qualification service identity. The price catalog
must be the exact production-qualified catalog in the prerequisite profile.

## 3. Prepare credentials and trust

Use the dedicated temporary-session AWS credentials file required by the
Bedrock profile. Create four separate owner-only JSON header files for the
control plane, OTLP trace route, Prometheus, and Grafana. Each file is a flat
JSON object of HTTP header names and values; do not reuse paths or credentials
across trust boundaries.

```json
{"Authorization":"Bearer short-lived-value"}
```

```sh
chmod 600 /secure/iip/aws-credentials \
  /secure/iip/control-plane-headers.json \
  /secure/iip/otlp-headers.json \
  /secure/iip/prometheus-headers.json \
  /secure/iip/grafana-headers.json
```

The control-plane credential needs `platform-admin` and an allowed
`ai-economics:qualify` decision. Supply private CA files where system trust is
insufficient. When the OTLP receiver requires mTLS, supply its client
certificate and an owner-only mode-`0600` key together.

Confirm that access logging for the exact invocation-observation POST route
does not record request bodies. The request body contains trace/span identity.

## 4. Run the live gate

The explicit final variable acknowledges one real provider request:

```sh
make qualify-customer-ai-finops-flow PYTHON=.venv/bin/python \
  IIP_CUSTOMER_AI_FINOPS_FLOW_PROFILE=/secure/iip/customer-ai-finops-flow-profile.json \
  IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE=/secure/iip/customer-ai-finops-prerequisite-profile.json \
  IIP_CUSTOMER_AI_FINOPS_FLOW_BEDROCK_PROFILE=/secure/iip/customer-bedrock-profile.json \
  IIP_CUSTOMER_AI_FINOPS_FLOW_AWS_CREDENTIALS_FILE=/secure/iip/aws-credentials \
  IIP_CUSTOMER_AI_FINOPS_FLOW_CONTROL_HEADERS=/secure/iip/control-plane-headers.json \
  IIP_CUSTOMER_AI_FINOPS_FLOW_OTLP_HEADERS=/secure/iip/otlp-headers.json \
  IIP_CUSTOMER_AI_FINOPS_FLOW_PROMETHEUS_HEADERS=/secure/iip/prometheus-headers.json \
  IIP_CUSTOMER_AI_FINOPS_FLOW_GRAFANA_HEADERS=/secure/iip/grafana-headers.json \
  IIP_CUSTOMER_AI_FINOPS_FLOW_ALLOW_PROVIDER_CALL=true
```

Set the optional `..._CONTROL_CA`, `..._OTLP_CA`, `..._PROMETHEUS_CA`,
`..._GRAFANA_CA`, `..._OTLP_CLIENT_CERT`, and `..._OTLP_CLIENT_KEY` variables
when required. The script disables environment proxies and redirects for its
HTTPS reads and removes ambient `AWS_*` variables before the pinned container
runs.

## 5. Verify and retain

The qualifier writes three protected mode-`0600` artifacts and one minimized
mode-`0644` report under `dist/` by default. Rebind all evidence before using
the result:

```sh
make verify-customer-ai-finops-flow-report PYTHON=.venv/bin/python \
  IIP_CUSTOMER_AI_FINOPS_FLOW_PROFILE=/secure/iip/customer-ai-finops-flow-profile.json \
  IIP_CUSTOMER_AI_FINOPS_PREREQUISITE_PROFILE=/secure/iip/customer-ai-finops-prerequisite-profile.json \
  IIP_CUSTOMER_AI_FINOPS_FLOW_BEDROCK_PROFILE=/secure/iip/customer-bedrock-profile.json
```

Retain `dist/customer-ai-finops-flow-qualification-report.json` with release
evidence. Under customer retention policy, securely delete the protected run
evidence, live compatibility output, and invocation observation after
verification; regenerating or verifying later requires rerunning the billable
flow or retaining those protected sources.

## Interpretation

`qualified` proves one same-invocation path and one protected-dimension
aggregate delta. It does not prove invoice agreement, arbitrary models or
regions, sustained traffic, Collector/backend lifecycle, or node/zone/region
availability. Grafana panel presence and its underlying Prometheus aggregate
are verified separately; trace identity never becomes a dashboard label.

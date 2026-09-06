# Customer worker and receiver processing continuity

**Status:** Explicitly enabled customer-environment qualification

This workflow sends authenticated synthetic API and OTLP traffic while
sequentially evicting one workflow-worker pod and one OTLP-receiver pod. It is
disruptive and must run only in a reviewed maintenance window with an approved
qualification tenant.

## Prerequisites

- a clean checkout of the exact release revision;
- an installed release using one immutable image digest;
- at least two Ready worker and receiver replicas, zero-unavailable rollouts,
  and enabled PodDisruptionBudgets;
- an existing qualification Resource and receiver channel that allowlists the
  profile's metric;
- a control-plane Bearer credential for the declared tenant and actor;
- a separate OTLP channel Bearer credential;
- verified HTTPS access to the API and mutual-TLS HTTPS access to the receiver;
- read access to Deployments, ReplicaSets, Pods, PDBs, and Kubernetes version;
  and `create` on `pods/eviction` for the two selected workloads.

Copy the example profile to a protected mode-`0600` path and replace every
example identity and metric binding with the approved customer values. Do not
commit the customer profile, tokens, client key, or certificates.

```bash
IIP_PROCESSING_ALLOW_DISRUPTION=true \
IIP_PROCESSING_API_BASE_URL=https://iip.example.com \
IIP_PROCESSING_API_TOKEN_FILE=/protected/api-token \
IIP_PROCESSING_OTLP_BASE_URL=https://otlp.example.com:4318 \
IIP_PROCESSING_OTLP_TOKEN_FILE=/protected/otlp-token \
IIP_PROCESSING_OTLP_CLIENT_CERT_FILE=/protected/client.crt \
IIP_PROCESSING_OTLP_CLIENT_KEY_FILE=/protected/client.key \
IIP_PROCESSING_PROFILE=/protected/customer-processing-profile.json \
IIP_PROCESSING_IMAGE_DIGEST=sha256:<64 lowercase hex characters> \
IIP_KUBERNETES_CONTEXT=customer-production \
IIP_DEPLOYMENT_NAMESPACE=iip-system \
  make qualify-customer-processing-continuity PYTHON=.venv/bin/python
```

Set the optional API and OTLP CA files when the endpoints do not chain to the
system trust store. The report is written to
`dist/customer-processing-continuity-qualification-report.json`.

Verification recomputes the report, profile, current source, and exact
environment bindings without reading credential contents into the report:

```bash
IIP_PROCESSING_PROFILE=/protected/customer-processing-profile.json \
IIP_PROCESSING_API_BASE_URL=https://iip.example.com \
IIP_PROCESSING_OTLP_BASE_URL=https://otlp.example.com:4318 \
IIP_KUBERNETES_CONTEXT=customer-production \
IIP_DEPLOYMENT_NAMESPACE=iip-system \
IIP_PROCESSING_IMAGE_DIGEST=sha256:<64 lowercase hex characters> \
  make verify-customer-processing-continuity-report PYTHON=.venv/bin/python
```

The run creates metric Evidence and four investigation records in the selected
tenant. Apply the customer's normal retention and audit policy. A report from a
different target, profile, source revision, image, or later-modified file is not
interchangeable.

Run a fresh deployment diagnostic after this workflow has recovered. The
customer deployment aggregator then binds this report to the preflight,
customer ingress/API continuity, and post-disruption health evidence; it will
not accept a report from another profile, endpoint, context, namespace,
deployment pair, image, or source revision.

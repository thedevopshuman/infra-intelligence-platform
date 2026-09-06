# Customer control-plane continuity qualification

**Status:** Executable customer-environment gate

This gate runs a sustained external probe against one exact release while it
evicts one ready API pod through the Kubernetes Eviction API. It is intended
for a controlled staging or production-readiness window after deployment
preflight, post-install diagnostics, and ordinary ingress qualification have
passed.

## Safety and prerequisites

- Run from the clean checkout used to build the deployed release.
- Use an explicit Kubernetes context and namespace. The command never reads or
  changes the caller's current context.
- Ensure the API Deployment has at least two ready replicas and a matching
  PodDisruptionBudget. The harness refuses ambiguous or degraded state.
- Use a short-lived, read-only IIP credential file prepared as described in
  [external ingress qualification](ingress-availability-qualification.md).
- Supply the exact OCI image digest configured in Helm.
- Schedule a reviewed disruption window. `IIP_CONTINUITY_ALLOW_DISRUPTION`
  must equal `true`; there is no implicit or dry-run-to-live transition.

The Kubernetes identity needs only `get` on the named Deployment, pods and
PodDisruptionBudget plus `create` on `pods/eviction` in the exact namespace.
It does not need Secret, log, exec, node, or cluster-wide access.

## Run

```bash
IIP_CONTINUITY_ALLOW_DISRUPTION=true \
IIP_KUBERNETES_CONTEXT=customer-production \
IIP_DEPLOYMENT_NAMESPACE=iip-system \
IIP_CONTINUITY_DEPLOYMENT=iip-infra-intelligence \
IIP_CONTINUITY_BASE_URL=https://iip.example.com \
IIP_CONTINUITY_TOKEN_FILE=/secure/temporary/iip-probe-token \
IIP_CONTINUITY_IMAGE_DIGEST=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
make qualify-customer-continuity PYTHON=.venv/bin/python
```

Defaults schedule 721 probe cycles at 500 ms intervals, a minimum 300-second
window, 60 seconds of baseline and post-recovery observation, 99.90%
availability, p95 at or below 2,000 ms, and replacement within 120 seconds.
Each cycle calls liveness, readiness, and authenticated runtime identity
through the external ingress. Review any override against the customer SLO;
the contract enforces closed lower and upper bounds.

For a private ingress CA, set `IIP_CONTINUITY_CA_FILE`. There is no insecure
TLS mode. The probe disables ambient proxies and redirects.

The command writes two records:

- `dist/customer-continuity-ingress-report.json` contains the existing
  external ingress measurements; and
- `dist/customer-continuity-qualification-report.json` binds that report to
  the controlled Eviction and recovery evidence.

## Verify retained evidence

```bash
make verify-customer-continuity-report PYTHON=.venv/bin/python
```

Verification is offline. It requires the current clean revision, the exact
referenced ingress report, a `qualified` continuity result, and consistent
identity, objective, timing, arithmetic, and content digests. It does not
recontact the ingress or Kubernetes API.

Retain both reports beside the release evidence and then remove the temporary
credential. Neither report belongs inside the immutable release bundle.

## Failure and rollback

The Eviction carries the observed pod UID as a precondition and is admitted
through the active PDB. Kubernetes creates the replacement; the harness makes
no rollout, scale, configuration, or Secret changes. If the run aborts after
Eviction, stop promotion, confirm the named Deployment returns to its declared
replica count, run `make diagnose-deployment`, and investigate the stable
error. Do not repeat the disruption until capacity is healthy.

This gate remains intentionally narrow. Database failover, worker processing,
OTLP receiver continuity, node/zone loss, sustained customer traffic, and
regional SLOs require their own environment-specific evidence.


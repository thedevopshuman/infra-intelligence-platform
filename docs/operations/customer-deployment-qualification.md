# Customer deployment qualification

**Status:** Executable customer-environment gate

This workflow turns the separate live preflight, sustained ingress/pod
Eviction, and post-install diagnostic reports into one exact-release evidence
chain. It does not install IIP and it does not perform another disruption.
Run it only after the owning workflows have completed in the same selected
cluster.

## Required order

1. Generate a cluster-mode, `install-ready` preflight using the exact protected
   production values.
2. Install the verified immutable release.
3. Run the explicitly enabled customer continuity workflow. That workflow
   performs the single API-pod Eviction and retains its nested ingress report.
4. Run deployment diagnostics after continuity has completed, so health is
   observed after recovery.
5. Aggregate the four reports while their timestamps remain within the chosen
   evidence-age objective.

For a core deployment with one values file:

```bash
IIP_KUBERNETES_CONTEXT=customer-production \
IIP_DEPLOYMENT_NAMESPACE=iip-system \
IIP_DEPLOYMENT_VALUES=/absolute/protected/customer.values.yaml \
IIP_CUSTOMER_QUALIFICATION_VALUES=/absolute/protected/customer.values.yaml \
IIP_CONTINUITY_IMAGE_DIGEST=sha256:<64 lowercase hex characters> \
  make qualify-customer-deployment PYTHON=.venv/bin/python
```

For the AI FinOps profile, set `IIP_CUSTOMER_QUALIFICATION_VALUES` to the
space-separated core and AI values paths in the same order used for the live
preflight. Paths containing spaces should be avoided for this Make wrapper;
call `scripts/qualify_customer_deployment.py` directly with repeated
`--values` arguments when such paths are unavoidable.

The defaults consume:

- `dist/customer-deployment-preflight-report.json`;
- `dist/deployment-diagnostic-report.json`;
- `dist/customer-continuity-ingress-report.json`; and
- `dist/customer-continuity-qualification-report.json`.

The aggregate is written to
`dist/customer-deployment-qualification-report.json`. The command retains a
valid `not-qualified` report and exits nonzero when an owning report is
unsuccessful, stale, or not ordered correctly. Crossed, malformed, dirty,
changed-during-read, or wrong-target inputs fail without synthesizing a
credible chain.

## Verification

Recompute the exact source, values, report digests, target hashes, nested
ingress chain, and current namespace UID/server binding:

```bash
IIP_KUBERNETES_CONTEXT=customer-production \
IIP_DEPLOYMENT_NAMESPACE=iip-system \
IIP_CUSTOMER_QUALIFICATION_VALUES=/absolute/protected/customer.values.yaml \
IIP_CONTINUITY_IMAGE_DIGEST=sha256:<64 lowercase hex characters> \
  make verify-customer-deployment-qualification-report \
  PYTHON=.venv/bin/python
```

The verifier is deliberately strict: moving a context alias to another
cluster, changing a source report, changing Helm values, or checking out a
different revision invalidates the evidence. The underlying script can omit
`--require-current-cluster` for an offline support-side verification of the
historical chain, but the Make gate always rechecks the cluster.

## Authority and privacy

Aggregation requires only the existing preflight/diagnostic read authority:
Kubernetes `/version` and `get` on the selected Namespace, plus local access to
the protected values and evidence files. It reads no Secret values and has no
mutation authority. The separate continuity step remains the only disruptive
operation and still requires its explicit enable flag and narrow Eviction
authority.

The aggregate retains no customer, cluster, namespace, release, Deployment,
Pod, endpoint, or credential value. Share the minimized report only under the
customer's evidence-handling policy; retain the source reports and protected
values separately for reproducible verification.

## Non-claims

This profile does not qualify artifact publication/signatures/vulnerabilities,
automatic database failover or disaster recovery, worker/receiver continuity, customer
identity/policy/broker/Collector interoperability, live AI providers or price
authority, regional capacity/SLOs, a design-partner outcome, or public
legal/brand/governance approval. Those remain independent release gates.

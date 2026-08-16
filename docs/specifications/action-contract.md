# Governed action contracts

**Status:** v1alpha1

An `ActionProposal` is an immutable, idempotent request for a narrow reversible operation. It contains a policy decision, expiry, risk, dry-run mode, and target but no execution credential. The proposal must bind to a completed, non-failed investigation whose accepted request granted `propose` authority and whose scope contains the target. The service records the investigation, target, and exact policy-input digests.

A distinct actor records `ActionApproval`; self-approval is rejected by the application service regardless of payload contents. Approval binds the decision to the exact proposal digest. Execution requires a third actor with the `executor` role and performs another current policy decision over the proposal digest, approval, immutable parameters, and current target digest.

`ActionExecutionStatus` is persisted before the executor is called. Its `executing` state carries a bounded lease and attempt `1`. A concurrent duplicate receives `action.execution.in-progress`. If the process disappears before a terminal result is committed and the lease expires, the state moves to `manual-reconciliation-required`; the platform never automatically replays an operation whose impact is unknown. `ActionResult` and the terminal execution state commit atomically and cite the exact proposal, approval, execution policy input, verification, and audit references.

The only accepted parameter shape for `kubernetes.restart-workload` is `namespace`, `workloadKind`, and `workloadName`. Unknown keys are rejected, and all three values must match the canonical target resource identity. Dry-run is the default. Live execution remains disabled until the request-scoped mutation executor is explicitly configured; duplicate idempotency keys return the original proposal/result and a claimed execution can never be claimed again.

The terminal status values `dry-run`, `succeeded`, `failed`, and `rolled-back` correspond to `ActionResult.spec.outcome`. `manual-reconciliation-required` deliberately has no `ActionResult`: an operator must compare the provider state with the immutable proposal and audit trail before deciding any follow-up.

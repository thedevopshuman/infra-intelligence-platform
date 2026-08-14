# Governed action contracts

**Status:** v1alpha1

An `ActionProposal` is an immutable, idempotent request for a narrow reversible operation. It contains a policy decision, expiry, risk, dry-run mode, and target but no execution credential. A distinct actor records `ActionApproval`; self-approval is rejected by the application service regardless of payload contents. `ActionResult` cites the exact proposal digest and approval, and records execution, verification, and audit references.

The reference action is `kubernetes.restart-workload`. Dry-run is the default. Live execution remains behind explicit approval and a request-scoped executor; duplicate idempotency keys return the original proposal/result and cannot duplicate impact.

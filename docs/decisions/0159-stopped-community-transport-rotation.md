# ADR 0159: Stopped-stack community transport rotation

**Status:** Accepted implementation boundary

**Date:** 2026-10-04

## Context

The persistent community preview generates a local CA and five leaf keypairs
for PostgreSQL, the isolated receiver, Collector delivery/intake, and receiver
health. Its leaf certificates expire after 365 days. The CA signing key is
deliberately discarded, so replacing expiring leaves under the same issuer is
not available. Reinitializing installation state would also change database
passwords and channel credentials, and is not a safe renewal mechanism.

The first cold-recovery boundary preserves those certificates without
extending their lifetime. Operators need a narrow, explicit transport lifecycle
that can also recover an expired installation while preserving data and
non-transport credentials. The source-only learning demo remains separate.

## Decision

Add `scripts/community_trust.py` as host-side operational composition, not an
API, SDK, agent, plugin, or certificate-authority service. It offers `status`,
`prepare`, `activate`, `rollback`, `finalize`, and `cancel`. Commands use the
existing installation lock and exact local Docker binding. No command stops
or starts serving containers, updates an external exporter, calls a provider,
or changes tenant, pricing/attribution inputs, data volumes, passwords, or
Bearer credentials.

- Represent each complete CA plus five-leaf/key set as an immutable,
  content-addressed directory under `transport-generations/<hash>`. A protected
  atomically replaced `transport-state.json` selects the active generation,
  pending rotation, and bounded history. On first enrollment, preserve the
  original flat `transport/` directory and import an exact immutable copy;
  thereafter the pointer, not the legacy location, is authoritative.
- `prepare` requires all installation containers removed. Validate the old
  material structurally even when expired, generate an entirely new CA and
  all five leaf keypairs, and discard its signing key. Publish the public
  old-plus-new CA overlap bundle at `transport-overlap/<rotation-id>.crt`.
  Leave the old generation selected and block startup in the prepared phase.
- The operator stages that public overlap bundle in every external exporter
  that must verify Collector intake. `activate` requires both the stopped
  installation and an explicit `--external-trust-staged` assertion. It selects
  the valid new generation; ordinary explicit startup projects it into all
  internal clients/servers together. Verified hostname/CA checks and the
  existing SPIFFE/channel and Bearer boundaries remain mandatory.
- Before finalization, `rollback` can select the prior generation only while
  stopped, after the same external-trust assertion, and only if its
  certificates remain valid. Rollback selects transport material; it does not
  replay or roll back database, queue, workflow, or price facts. Expired old
  certificates cannot be made valid by a rollback flag.
- After explicit startup, the launcher records a successful-start receipt.
  `finalize --new-trust-verified` requires the operator's real-intake assertion
  and independently rechecks that the exact recorded containers still match
  and are healthy. The flag refers to the **currently active generation**,
  including the previous generation following rollback. Local readiness is
  not substituted for external exporter or durable-intake verification.
- Ordinary repeated startup is a read-only no-op only for the exact healthy
  unchanged project, with initializer labels matching selected transport,
  installation/credential binding, and operational deployment. Build requests,
  repairs, mismatches, or source/configuration changes require removed
  containers first; the initializer must not rewrite serving trust volumes.
- Finalization closes that rotation's rollback eligibility while the
  installation may remain running. The operator then narrows external trust
  from the overlap bundle to the active CA only, reloads/restarts the exporter
  as required, and verifies intake again. `cancel` is available only before
  activation, while the prepared installation remains stopped.
- Repeating a command in its corresponding completed phase is idempotent;
  invalid transitions fail closed. Configuration changes and offline backup
  are blocked until the rotation is finalized or cancelled. Preserve old
  immutable material/history for diagnosis; do not automatically delete keys
  or claim that retaining a bundle makes it active or revokes another CA.
- `status` returns only the selected generation, CA/bundle paths, phase,
  validity dates, expiry, and a 30-day expiry warning. It prints no private
  key or non-transport credential. It is an operator check, not a scheduled
  renewal or notification service.

## Consequences and non-claims

This supersedes the absence of an implemented community transport-rotation
procedure described in ADR 0156 and referenced by ADR 0158. It does not change
their persistent-preview status, cold-copy compatibility restrictions,
customer authority boundaries, or production qualification gates.

Every activation or rollback has a planned single-host interruption. This is
not hot rotation, HA, a Kubernetes PKI workflow, a hosted CA, OCSP/CRL
revocation, a password/token rotation, an external-exporter manager, or a
guarantee that buffered telemetry survives the interruption. Whole-CA
replacement does not change certificate-verification requirements or silently
reissue trust on ordinary startup.

Unit tests and an owned runtime gate must establish state transitions,
unchanged non-transport state, active/old/overlap trust behavior, receipt-bound
finalization, and expired-material handling for the exact candidate. A harness
or successful local readiness check alone is not real customer-intake or
production-lifecycle evidence. The owned local synthetic Docker gate passed on
2026-10-04, including safe repeated startup, rotation/rollback, and encrypted
fresh restore. The [runbook](../operations/community-trust-rotation.md#evidence-and-remaining-responsibilities)
records the narrow result and commands; it is not customer or public-v1
qualification.

See the [community trust-rotation runbook](../operations/community-trust-rotation.md).

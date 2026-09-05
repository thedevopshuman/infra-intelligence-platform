# ADR 0113: Runtime enforcement of AI price-catalog promotion evidence

**Status:** Accepted
**Date:** 2026-09-06

## Context

ADR 0112 makes candidate price catalogs independently qualifiable, but a
standalone report does not stop a worker from using a different, expired, or
offline-only catalog. Production cost estimates must not silently cross that
gap. The enforcement must also preserve deterministic fixture operation for
local development and avoid placing protected rates or scope lists in a
non-secret deployment profile.

## Decision

Add an explicit production qualification gate to the AI cost worker. When the
gate is enabled, protected configuration must contain exactly one qualification
policy and one report for every enrolled catalog tenant. Startup reconstructs
each report against the exact catalog and policy, requires a qualified
`production-catalog` level, rejects fixture mode, and rejects missing,
duplicate, or cross-tenant bindings.

Before every catalog registration and cost pass, the worker reconstructs the
report again and requires its clock evaluation to fall within
`[generatedAt, validUntil)`. Any failure stops before catalog registration and
uses one stable configuration error. The production AI FinOps Helm overlay and
deployment preflight require this gate and project the policy/report wrapper
from an existing Secret visible only to the worker.

The gate is disabled by default for deterministic local fixtures. A deployment
without the gate is a development profile and cannot pass the
`production-ai-finops-v0` preflight.

## Consequences

- A reviewed report authorizes only its exact tenant, catalog, policy, level,
  qualifier version, and validity window.
- Expiration during a running worker takes effect on the next bounded pass;
  restarting the pod is not required to stop use.
- Qualification remains separate from collection and pricing calculation, and
  the worker makes no provider network request.
- The Secret contains protected policy scopes and must be rotated before the
  report expires. The sanitized deployment profile exposes only that the gate
  is enabled and the referenced Secret name/key.
- Source authority, negotiated-price approval, invoice reconciliation, and
  customer change-control evidence remain customer responsibilities.

# ADR 0143: Aggregate exact evidence before a private customer pilot

**Status:** Accepted
**Date:** 2026-09-08

## Context

The local release-readiness report deliberately leaves publication,
organizational trust, customer installation, live integrations, live AI, and
design-partner operation external. Later customer workflows now provide
machine-verifiable publication, signature, deployment, load, AI prerequisite,
and same-invocation evidence, but a release owner still has to correlate those
artifacts manually. A directory full of individually successful reports can
mix releases, registry targets, installed images, customer environments, or
control-plane endpoints.

The platform needs an honest answer to a narrower question than production or
public-launch readiness: is one exact release a suitable candidate to enter a
private design-partner evaluation?

## Decision

1. Add a protected `CustomerPilotReadinessProfile` that fixes the exact release
   manifest and both OCI index digests; organizational signature-policy and
   publication-target-set digests; customer cluster, environment, and
   control-plane target bindings; and bounded evidence ages.
2. Add a `CustomerPilotReadinessReport` that consumes seven existing owning
   reports in a fixed order: local release readiness, registry publication,
   organizational signature verification, customer deployment qualification,
   bounded control-plane load, AI FinOps prerequisites, and the exact
   same-invocation AI FinOps flow.
3. Require registry index digests to equal the organizationally verified
   signatures and require the published control-plane digest to equal the
   installed, load-tested, and AI-qualified image. Rebind the prerequisite
   report to the exact local-readiness and customer-deployment files, then bind
   the live flow to that exact prerequisite file.
4. Require the customer load window to begin only after deployment
   qualification. A valid failed, stale, expired, or insufficient source yields
   `not-candidate`; malformed or crossed evidence fails without producing a
   credible aggregate.
5. Use `design-partner-candidate` only for the fixed
   `private-design-partner-preflight` boundary. Always retain external gates for
   actual partner operation and acceptance, production operating
   qualification, and public license/legal/brand/governance approval.
6. Retain only release identity, content digests, bounded timestamps and ages,
   stable statuses, checks, limitations, and external gates. Do not retain
   repositories, endpoints, customer identifiers, tenant or ownership labels,
   credentials, provider targets, prompts, responses, trace/span identity,
   token quantities, prices, or amounts.

## Consequences

- Release owners get one fail-closed pre-pilot artifact instead of manually
  correlating seven independently owned evidence chains.
- Organizational publication and trust are no longer blurred with an unsigned
  local candidate, and the installed image cannot differ from the signed OCI
  index.
- The report authorizes no installation, provider call, load generation,
  mutation, publication, signing, pilot start, or production promotion. Those
  actions remain with the owning workflows and human decision makers.
- A candidate report expires at the earliest of the protected profile,
  profile-age, applicable evidence-age, AI prerequisite, live-flow, and
  configured report-validity limits.
- Naming and `IIP` identifiers remain neutral placeholders. This decision does
  not select a public product name, license, trademark policy, company, or
  commercial boundary.

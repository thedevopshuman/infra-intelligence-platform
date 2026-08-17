# ADR 0054: Protected investigation signal catalog

**Status:** Accepted

## Context

The deterministic investigator can safely plan and assess five signal classes, but every candidate currently has to be copied into every request. That makes the product difficult to operate and encourages callers to know integration-specific logical metrics, services, runbooks, and conditions. Generating queries from untrusted alert text or model output would instead create an unsafe authority expansion.

## Decision

Add one protected, versioned signal profile per tenant behind an application-owned catalog port. The profile contains only existing provider-neutral candidate fragments. It has no endpoint or credential authority.

At the authenticated preparation boundary, fill only signal lists the caller left empty. Freeze generated candidates into the durable request together with profile and resolved-subset digests. Separate public preparation from trusted worker execution so callers cannot forge the server-owned snapshot. The worker verifies the frozen snapshot without depending on later catalog changes.

Keep request evidence/tool upper bounds, budgets, root-cause matching, policy, provider allowlists, and credential leases authoritative. Record candidate origin and catalog digests in the report plan. Reject duplicate tenant profiles and invalid catalog content at startup.

## Consequences

- customer investigations can automatically use reviewed operational signals;
- a queued investigation is reproducible even if configuration changes before execution;
- callers can override one signal with an explicit non-empty candidate list without combining two trust sources;
- catalog configuration cannot grant credentials, network destinations, resources, time ranges, or mutation authority;
- adaptive replanning, multiple selectable profiles per tenant, seasonal baselines, and generated free-form queries remain out of scope.

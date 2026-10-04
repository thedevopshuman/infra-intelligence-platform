# ADR 0155: Prioritize public open-source v1

**Status:** Accepted release sequencing; publication decisions pending

**Date:** 2026-10-04

## Context

The repository has executable infrastructure and AI economics workflows,
local release qualification, and a documented private-pilot admission process.
The project owner has now selected a public open-source v1 as the first release,
ahead of a private design-partner pilot.

That selection establishes the release order. It does not grant a license,
select a GitHub organization, approve a product name, or make local test evidence
proof of production readiness.

## Decision

1. Prioritize a publicly installable, documented, useful self-hosted v1. Track
   its remaining work in the [public v1 release plan](../roadmap/public-v1-release-plan.md).
2. Preserve the product constitution, package boundaries, tenant and authority
   rules, and the full infrastructure and AI FinOps roadmaps. This sequencing
   change does not declare their phase exit gates complete.
3. Keep the private-pilot contracts, evidence semantics, operating handoff, and
   admission requirements intact. They continue to govern any private pilot;
   their success status cannot stand in for a public-release decision.
4. Demonstrate the supported public feature set through installation from
   published artifacts, real integration behavior, operational recovery, and
   usable documentation. Retain explicit limitations for experimental features
   and unqualified environments. Do not promote synthetic data or test prices
   into live-provider or billing claims.
5. Obtain the owner's repository/registry namespace and open-source license
   selection before publication. Establish actual support and private security
   reporting routes. No tool may invent these identities or commitments.
6. Preserve neutral IIP identifiers under ADR 0003. Public naming and ownership
   decisions remain explicit; any later rename needs a compatibility plan.

## Consequences

The next product work emphasizes a reproducible public installation, a clear
supported feature matrix, compatible public contracts, and the Bedrock-to-cost
workflow. Private customer acceptance is no longer the first release target.
Live evidence for the environments and reliability claims we support remains
required, and the existing local candidate is still pre-release software.

This decision changes no runtime authority, public schema, license grant,
release workflow permission, or current qualification report status.

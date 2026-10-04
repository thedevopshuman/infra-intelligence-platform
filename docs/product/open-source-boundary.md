# Open-source and commercial boundary

**Status:** Apache-2.0 foundation accepted; commercial packaging undecided
**Date:** 2026-10-04

Original repository code and documentation are licensed under
[Apache-2.0](../../LICENSE), selected for the source-only learning preview in
[ADR 0157](../decisions/0157-apache-licensed-learning-release.md). Third-party
material retains its own licensing. The license is the grant of rights;
the commercial ideas below do not restrict already licensed code or imply a
production support promise.

## Open foundation

- Resource, event, evidence, agent, plugin, action, and investigation contracts.
- Core SDKs and plugin development kit.
- Local/single-tenant resource graph and event timeline.
- Reference investigation runtime and evaluation harness.
- Community integrations and read-only tools.
- Helm chart for self-hosted foundation components.

## Candidate commercial capabilities

- Hosted multi-tenant control plane and managed upgrades.
- Enterprise identity, fine-grained policy administration, compliance exports, and long-term audit retention.
- Fleet-scale graph/event storage, cross-region operation, and enterprise SLAs.
- Certified integrations and signed marketplace governance.
- Advanced collaboration, workflow analytics, cost governance, and managed evaluation packs.

## Decision tests

The open layer must be genuinely useful for a small team, support independent extension, and avoid an intentionally crippled core. The commercial layer should monetize operation, governance, scale, support, and curated intelligence—not contract incompatibility or data hostage-taking.

The selected GitHub owner is `thedevopshuman`. Contributions follow
[CONTRIBUTING.md](../../CONTRIBUTING.md). Formal company/trademark disposition,
additional contributor agreements if needed, dependency/compliance review,
commercial packaging, and production support commitments remain undecided.

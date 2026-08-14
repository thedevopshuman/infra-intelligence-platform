# Naming brief

**Status:** Draft

## What is being named

An infrastructure-intelligence platform that builds a live resource graph and event timeline, then coordinates evidence-backed agents and governed actions across operations domains.

## Desired associations

- Clarity from complex systems.
- Trusted operational judgment.
- Connections, change, and causality.
- Infrastructure depth without sounding like another monitoring dashboard.
- Calm control rather than autonomous magic.

## Avoid

- Names containing `SRE`, `ops`, `cloud`, or `AI` unless category clarity outweighs lack of distinctiveness.
- Militaristic or surveillance-heavy language.
- Claims of omniscience, automatic repair, or zero incidents.
- Hard-to-pronounce invented spellings.
- Names tied to one cloud, runtime, agent framework, or product wedge.

## Candidate scorecard

Score 1–5 for distinctiveness, pronounceability, memorability, category fit, trust, international usability, spelling/searchability, naming-system extensibility, and preliminary legal/domain/package availability. Reject a candidate with a serious negative association or direct infrastructure/software collision regardless of average score.

## Architecture rename seams

When branding is chosen, decide separately whether to rename:

- prose and UI labels;
- repository and Helm chart;
- Python/TypeScript package names;
- environment variable prefix;
- Kubernetes API group;
- event type namespace;
- plugin protocol and marketplace identity.

Stable public identifiers may retain the neutral `iip` namespace through a transition even if the UI brand changes immediately.


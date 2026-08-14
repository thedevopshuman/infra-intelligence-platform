# OpenSRE reference analysis

**Status:** Research snapshot  
**Observed:** 2026-08-14  
**Reference:** [Tracer-Cloud/opensre](https://github.com/tracer-cloud/opensre)

## Executive view

OpenSRE is a strong reference for the **incident-investigation and agent-tooling layer**. Its current repository describes a public-alpha framework for self-hosted AI SRE agents, broad operational integrations, evidence-backed RCA, synthetic and cloud-backed tests, and multiple interactive/deployed surfaces.

Our platform should learn from its boundary discipline and investigation loop, but has a wider center of gravity: a durable resource graph, normalized event plane, evidence service, policy/approval workflows, multi-domain agents, and a permissioned plugin ecosystem.

## What OpenSRE demonstrates well

### Clear implementation tiers

The repository separates surfaces and gateway, a composition root, tools and integrations, core/platform services, and leaf configuration. Its architecture document makes dependency direction explicit and CI-enforced. This is more valuable than copying exact folder names: provider code stays below orchestration surfaces, and composition is treated as an owned concern.

### Bounded investigation mechanics

The documented investigation pipeline resolves integrations, classifies/extracts an alert, plans relevant tools, gathers evidence in a ReAct loop, diagnoses, and delivers a report. It describes concrete loop controls including tool selection caps, duplicate-call caching, context budgets, iteration bounds, and stagnation handling. These are production runtime concerns, not prompt wording.

### Integration and tool separation

Vendor configuration/clients live as integrations while agent-callable capabilities live as tools. This supports provider reuse and keeps orchestration thin. The project also documents how to decide where a tool belongs, reducing the risk that a generic registry becomes a vendor switchboard.

### Evaluation as a product primitive

OpenSRE explicitly treats synthetic incident suites and cloud-backed end-to-end scenarios as the training/evaluation environment for SRE agents. Scenarios that require correct evidence and contain red herrings are a better quality measure than line coverage or a single polished demo.

### Operational safety concerns

The repository calls out identifier masking, guardrails, sandboxing, structured/auditable prompts, local transcript handling, and avoiding silent bulk export of logs. Its agent development guide emphasizes authorization, concurrency, crash behavior, and cross-surface isolation tests.

## What to adopt

| OpenSRE lesson | Platform adaptation |
| --- | --- |
| Dependency tiers and a composition root | Enforce domain → application ports → adapters → surfaces, with bootstrap as the only concrete wiring point. |
| Tools have metadata and schemas | Make tool schemas, scope, cost, risk, evidence type, and authority part of a registry contract. |
| Integrations own vendor clients/config | Package vendor behavior as adapters/plugins behind provider-neutral ports. |
| Bounded evidence-gathering loop | Standardize budgets, tool caps, duplicate detection, stagnation, cancellation, and terminal reasons. |
| Structured evidence-backed diagnosis | Make evidence IDs, hashes, provenance, contradiction, and missing evidence mandatory report concepts. |
| Synthetic and E2E incident scenarios | Create evaluation packs tied to resource graphs, timelines, expected evidence, red herrings, and action safety. |
| Multiple surfaces over shared capabilities | Keep API, CLI, UI, chat, webhook, and MCP surfaces as independent adapters. |

## What to extend

1. **Resource graph as a durable product substrate.** Investigations consume a shared identity/relationship model rather than assembling all context per incident.
2. **Event timeline as an authoritative plane.** Alerts, deploys, config changes, findings, policy decisions, tool calls, and actions share correlation/causation semantics.
3. **Evidence as a service.** Content-addressed artifacts, retention, redaction, provenance, access policy, and reuse across agents become explicit.
4. **Durable workflow and approval.** Investigation and remediation are state machines that survive restarts and re-check policy before side effects.
5. **Multi-domain intelligence.** SRE, network, cost, security, database, and GitOps agents share the same governed substrate.
6. **Plugin isolation and lifecycle.** Signed versions, declared permissions, protocol compatibility, sandboxing, and marketplace review are first-class.
7. **Tenant-grade isolation.** Tenant partitions cover graph, events, evidence, caches, credentials, budgets, prompts, audit, and evaluation data.

## What not to copy blindly

- Repository layout is not the architecture; use names aligned with our domain and language choices.
- A large integration catalog is not early product-market fit. Begin with the Kubernetes investigation wedge and two or three evidence providers.
- A model-driven loop must not own durable workflow, authority, or side-effect retry semantics.
- In-process vendor integrations are acceptable for development but not the final plugin isolation model.
- A current implementation detail from a public-alpha project should not become our public contract without an independent decision.

## Resulting design decisions here

This repository reflects the analysis through strict package directions, a composition root, public contract schemas, an evidence-oriented investigation lifecycle, policy-separated actions, and an evaluation milestone in the roadmap. It intentionally adds the resource/event substrate and plugin permission model outside the reference project's primary investigation focus.

## Primary sources

- [OpenSRE repository and product overview](https://github.com/tracer-cloud/opensre)
- [OpenSRE architecture](https://github.com/tracer-cloud/opensre/blob/main/docs/ARCHITECTURE.md)
- [OpenSRE investigation pipeline architecture](https://github.com/tracer-cloud/opensre/blob/main/docs/investigation-pipeline-architecture.md)
- [OpenSRE development reference](https://github.com/tracer-cloud/opensre/blob/main/AGENTS.md)
- [OpenSRE tool placement policy](https://github.com/tracer-cloud/opensre/blob/main/docs/tool-placement-policy.md)
- [CloudEvents specification](https://github.com/cloudevents/spec)


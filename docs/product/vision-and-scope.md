# Vision and scope

**Status:** Draft  
**Date:** 2026-08-14

## Problem

Infrastructure knowledge is fragmented across cloud APIs, Kubernetes, observability tools, deploy systems, incident channels, repositories, runbooks, and human memory. Existing tools usually optimize one data plane or one incident step. Operators still reconstruct context manually, and generic AI assistants often lack durable identity, evidence lineage, policy, and safe action boundaries.

## Primary users

| User | Core job |
| --- | --- |
| On-call engineer | Explain an incident quickly and show the evidence. |
| Platform/SRE team | Maintain a live model of infrastructure and standardize investigation and remediation. |
| Service owner | Understand dependencies, recent changes, and operational risk without learning every vendor console. |
| Security/FinOps operator | Run domain agents over the same governed resource and event substrate. |
| Extension developer | Add a provider, tool, agent, or workflow without coupling to platform internals. |

## Platform capabilities

1. Discover and normalize infrastructure resources.
2. Maintain typed relationships and observation history.
3. Ingest a common event envelope and build correlated timelines.
4. Gather content-addressed evidence from approved providers.
5. Run bounded domain agents over scoped context and tools.
6. Produce structured investigations with competing hypotheses.
7. Propose, approve, execute, and audit workflows under policy.
8. Expose UI, API, CLI, SDK, webhooks, and MCP-facing surfaces.
9. Support a signed, permissioned plugin ecosystem.
10. Observe generative-AI usage and calculate evidence-backed cost and savings
    without entering the inference request path.

## First wedge

The first end-to-end product slice is **read-only Kubernetes incident investigation**:

- observe clusters, namespaces, workloads, pods, services, and recent changes;
- ingest alerts and deployment events;
- correlate an alert to affected resources and recent changes;
- collect logs, metrics, Kubernetes events, and deployment evidence;
- generate a report with ranked hypotheses, missing evidence, and recommended next checks;
- require human approval before any mutation.

This wedge exercises the platform primitives without prematurely committing to every cloud or observability system.

The first adjacent domain extension is the
[AI FinOps and generative-AI observability slice](ai-finops-vision.md). It
reuses the same tenancy, evidence, event, PostgreSQL, OpenTelemetry, and
deployment primitives; its first wedge is metadata-only AWS Bedrock economics,
not an inference proxy or autonomous agent.

## Foundation-stage non-goals

- General-purpose autonomous operations.
- A replacement for all observability, CMDB, incident, or IaC products.
- A proprietary query language before common user questions are understood.
- Training a foundation model.
- A public plugin marketplace before signing, permission, review, and compatibility models exist.
- Multi-region production scale before tenant isolation and event semantics are proven.

## Success measures for the first pilot

- At least 80% of target Kubernetes resources have stable identity and correct owner/dependency edges.
- A known incident scenario returns the required evidence and correct root-cause class in at least 8 of 10 runs.
- Every conclusion and action recommendation links to evidence or explicitly states the gap.
- Cross-tenant contract tests show no data, credential, event, or cache leakage.
- Median investigation completes within five minutes and a declared cost budget.
- A service owner can trace alert → resource → change → evidence → conclusion in one timeline.

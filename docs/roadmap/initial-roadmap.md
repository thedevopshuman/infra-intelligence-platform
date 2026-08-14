# Initial implementation roadmap

**Status:** Working plan  
**Planning horizon:** Foundation through first design partner

**Implementation note (2026-08-14):** The repository contains one executable local reference slice through every phase: live reconciliation collection and checkpoint ingestion (Phase 1), durable evidence and deterministic investigations (Phase 2), repeated-run scoring (Phase 3), separation-of-duties dry-run actions (Phase 4), and bounded plugin handshake metadata (Phase 5). These are implementation units, not completed phase exit gates. Remaining gate work is listed below and external pilot/legal/brand outcomes cannot be completed by repository code alone.

The roadmap is outcome-based. Dates should be added after team size and pilot constraints are known. Each phase ends with evidence that the next risk is worth taking.

## Phase 0 — foundation (current repository)

**Outcome:** One coherent project can be opened in Codex and extended without rediscovering product or architecture intent.

Delivered:

- product constitution, scope, glossary, open/commercial proposal;
- logical architecture, security baseline, investigation lifecycle, repository boundaries;
- OpenSRE reference analysis and separate brand research track;
- resource, event, agent, and plugin `v1alpha1` contracts and examples;
- runnable resource-ingestion vertical slice with ports/adapters;
- Python and TypeScript SDK boundaries;
- Helm/Kubernetes deployment skeleton and local quality gates.

Exit gate: `make verify` passes and a new contributor can trace resource ingress through authorization, storage, and event emission.

## Phase 1 — resource and event substrate

**Outcome:** A single-tenant local deployment maintains a correct Kubernetes resource graph and replayable timeline.

Deliverables:

- choose and document authoritative graph/observation store and event transport;
- formalize evidence, investigation request/report, pagination, error, and integration-config contracts;
- Kubernetes collector plugin for cluster, namespace, workload, pod, service, ingress, config, and ownership relationships;
- idempotent ingestion, source checkpoints, deduplication, reconciliation, deletion/tombstone semantics;
- event projection and query API for resource history and neighborhood;
- service identity/authentication for collectors;
- tenant-partition and replay tests, backup/restore experiment, and ingestion SLOs.

Exit gate: a seeded cluster can be rebuilt from observations/events; graph correctness and source lag are measurable.

Reference slice delivered: explicit live kind collection, safe image-pull status normalization, host-side collection validation, durable PostgreSQL projection/event/evidence substrate, complete-snapshot membership, deterministic tombstones, exact-result replay, atomic reconciliation-state/checkpoint commit, and a privileged tenant-scoped projection drift/rebuild command from accepted observation history. Remaining: per-type list/watch cursors, `410 Gone` recovery, backup/restore measurements, and freshness SLO telemetry.

## Phase 2 — evidence and investigation vertical slice

**Outcome:** A read-only Kubernetes incident produces a structured, evidence-backed report.

Deliverables:

- durable evidence storage and investigation request/report APIs against the Phase 1 contracts;
- providers for Kubernetes events/status, deployment changes, logs, metrics, and repository/runbook context;
- bounded agent runtime with tool caps, duplicate cache, context/cost/time budgets, cancellation, and terminal reasons;
- correlation between alert, affected resource neighborhood, and recent changes;
- structured hypotheses, contradictions, unknowns, and evidence citations;
- CLI/API report surface and traceable investigation timeline;
- model-provider abstraction with data-handling policy and redaction.

Exit gate: target scenarios meet required evidence and root-cause scoring in repeated runs within declared latency and cost.

Reference slice delivered: durable evidence artifacts, authenticated investigation POST/GET API, bounded deterministic agent, resource-state provider, structured root-cause taxonomy, citations, unknowns, recommendations, and zero-model cost ledger. Remaining: Kubernetes events, logs, metrics, repository/runbook providers, cancellation/crash recovery, redaction policy expansion, and a production model-provider decision.

## Phase 3 — evaluation and operational hardening

**Outcome:** Agent changes can be promoted by measurable reliability rather than demos.

Deliverables:

- scenario format containing graph fixture, timeline, alert, expected root-cause class, required/forbidden evidence, and red herrings;
- deterministic replay and scored repeated-run harness;
- prompt/tool/model version tracking and regression dashboard;
- OpenTelemetry traces, metrics, logs, cost ledger, audit records, and privacy controls;
- concurrency, crash recovery, cancellation, noisy-neighbor, and prompt-injection tests;
- SLOs for ingestion freshness, query availability, and investigation completion.

Exit gate: releases have comparable scorecards, and failures can be explained from platform telemetry.

Reference slice delivered: deterministic weighted scorer, hard gates, budget checks, red-herring resistance, and repeated-run harness. Remaining: version dashboard, OpenTelemetry export, concurrency/noisy-neighbor/prompt-injection suites, privacy controls, and measured release SLOs.

## Phase 4 — governed actions and workflows

**Outcome:** The platform can safely propose and execute a narrow reversible remediation.

Deliverables:

- action proposal/result and approval contracts;
- durable workflow state machine, idempotency, retries, expiry, and verification;
- policy decision service and explainable policy audit;
- one reversible Kubernetes action with dry-run and rollback;
- approval UI/API and separation-of-duties controls;
- credential broker for request-scoped, short-lived access.

Exit gate: duplicate delivery cannot duplicate impact; policy, approval, execution, and verification are fully reconstructable.

Reference slice delivered: versioned proposal/approval/result contracts, durable idempotency, expiry, role separation, current policy checks, audit references, and a no-impact Kubernetes restart dry-run executor. Remaining: a production policy engine, durable retry/timer worker, request-scoped credential broker, approval UI, live server-side dry-run, mutation verification, and tested rollback.

## Phase 5 — plugin SDK and first design partner

**Outcome:** An external contributor can build and operate a least-privilege integration without platform-internal imports.

Deliverables:

- plugin handshake, capability tokens, cancellation, structured errors, and conformance suite;
- out-of-process runner with signing, digest verification, permission enforcement, and resource limits;
- SDK generators or aligned hand-written Python/TypeScript SDKs;
- integration setup/verification experience and compatibility matrix;
- design-partner deployment, onboarding runbook, support/security channels, and feedback telemetry;
- licensing, contributor, trademark, and open/commercial decisions before public launch.

Exit gate: a plugin built only from public docs/SDK passes conformance and runs in a pilot without elevated control-plane credentials.

Reference slice delivered: session contract, declared-capability subset enforcement, token reference/digest handling, expiry/cancellation/limit framing, live observer transport, public SDK types, and conformance tests. Remaining: signed artifacts, digest enforcement, isolated out-of-process runner, OS/network/resource sandboxing, compatibility automation, design-partner deployment, and public governance/legal decisions.

## Parallel track — company and brand

This track never blocks technical discovery but must complete before a public launch:

- positioning interviews and category language;
- name architecture and screened candidate set;
- legal, trademark, domain, package, and social verification;
- visual/voice identity and migration plan from neutral identifiers;
- company, ownership, license, contributor, and trademark policies.

All working material stays under `docs/research/brand/` until accepted.

## Decision backlog

| Decision | Latest responsible phase | Evaluation criteria |
| --- | --- | --- |
| Graph/observation store | **Accepted: ADR 0004** | PostgreSQL 16–18 initially; revisit from measured temporal/graph workload |
| Durable event transport | **Accepted: ADR 0004** | PostgreSQL event log and outbox initially; external broker remains replaceable |
| Workflow engine | Phase 3 end | Durable timers, approvals, retries, audit, self-hosting burden |
| Policy engine | Phase 3 end | Explainability, data isolation, bundle/version lifecycle, ecosystem |
| Plugin runtime | Phase 4 end | Isolation, language support, streaming, operational cost, signing |
| Model/provider strategy | Phase 2 start | Data policy, tool use, structured output, cost, evaluation stability |
| License and governance | Before public pilot | Community utility, commercial sustainability, contributor clarity |

## First ten implementation issues

1. **Completed:** Add evidence and investigation schemas plus examples.
2. **Completed:** Build schema validation in CI using a pinned validator.
3. **Completed:** Specify observation version/checkpoint and stale-write semantics.
4. **Completed:** Implement PostgreSQL-backed resource repository behind the application port.
5. **Completed:** Implement an outbox so resource updates and events are atomic.
6. **Completed:** Create Kubernetes collector plugin skeleton and conformance fixture.
7. **Completed:** Add graph neighborhood and resource timeline API queries.
8. **Completed:** Define the evaluation scenario contract and one known Kubernetes failure.
9. **Completed:** Implement evidence provider ports and content hashing/redaction metadata.
10. **Completed:** Add authentication middleware that derives tenant/actor context and removes development header trust.

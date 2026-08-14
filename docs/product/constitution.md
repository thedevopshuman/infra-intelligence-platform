# Product constitution

**Status:** Accepted foundation  
**Version:** 0.1  
**Date:** 2026-08-14

This constitution governs product and architecture choices until a later accepted decision explicitly supersedes part of it.

## Mission

Turn fragmented infrastructure signals into a trustworthy, time-aware model of what exists, what changed, why it matters, and what may safely happen next.

## Product promise

For an operator facing a question or incident, the platform should assemble relevant resources and events, run a bounded investigation, and return conclusions linked to evidence. When remediation is appropriate, it should propose an auditable action and apply policy before execution.

## Principles

### 1. Evidence before confidence

Every material conclusion records supporting and contradicting evidence. Confidence without traceability is presentation, not intelligence.

### 2. The world is a graph and a timeline

Current state alone cannot explain failure. Resource relationships and immutable changes are equal parts of the system of record.

### 3. Authority is explicit and least-privileged

Reading, proposing, approving, and executing are distinct. An agent, plugin, or user receives only request-scoped authority and never acquires ambient production access.

### 4. Humans retain meaningful control

The platform automates collection and reasoning aggressively; it automates impact cautiously. High-blast-radius, destructive, irreversible, novel, or low-confidence actions require approval.

### 5. Contracts outlive implementations

Resources, events, evidence, agents, plugins, actions, and investigations have versioned contracts. Storage engines, model providers, buses, and user interfaces may change behind them.

### 6. Vendor neutrality is architectural

External systems are adapters and plugins. No cloud, observability vendor, model provider, incident tool, or database owns the core domain.

### 7. Secure by construction

Tenant scope, secret handling, data minimization, audit, retention, and failure behavior are part of feature design. Prompt injection and tool output are treated as boundary threats.

### 8. Bounded agents are production components

Agents declare tools, schemas, budgets, model class, stopping rules, and authority. They are evaluated with scenarios and evidence requirements, not demo quality alone.

### 9. Progressive adoption

The first useful deployment may be read-only, connect a few systems, and run one investigation. A customer should gain value without replacing their existing operational stack.

### 10. Operate the operator

The platform exposes its own health, cost, latency, decisions, tool calls, policy results, and uncertainty. Intelligence that cannot be operated safely is not ready.

## Quality bars

A capability is not production-ready until it has:

- a versioned input/output contract;
- explicit tenant and actor scope;
- bounded time, cost, and tool calls;
- structured telemetry and audit events;
- stable failure codes without sensitive details;
- replay or idempotency semantics where side effects are possible;
- tests for authorization, tenant isolation, and representative failure modes;
- user-facing documentation and an operational rollback path.

## Governance

- This file changes only through an accepted architecture decision record.
- A roadmap deadline does not waive a principle; it narrows scope.
- Exceptions state owner, reason, expiry date, and mitigation.
- Brand and company decisions remain a parallel research track and cannot silently reshape platform contracts.


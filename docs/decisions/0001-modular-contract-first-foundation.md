# ADR 0001: Modular, contract-first foundation

**Status:** Accepted  
**Date:** 2026-08-14

## Context

The platform spans resource discovery, event correlation, agents, workflows, policy, and plugins. Splitting every concept into a service now would freeze uncertain boundaries and add operational cost. A single undifferentiated application would couple vendors and transports to domain rules.

## Decision

Begin as a modular monolith with domain, application ports/use cases, adapters, surfaces, and a composition root. Define public resource, event, agent, and plugin contracts before scaling their implementations. Deploy additional processes only for concrete scaling, isolation, or failure-domain needs.

## Consequences

Local development and refactoring remain cheap while package boundaries model future service ownership. Import and contract checks are required. Some scaling decisions remain open, intentionally, until workloads and failure modes are measured.


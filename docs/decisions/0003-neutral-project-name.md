# ADR 0003: Neutral placeholder naming

**Status:** Accepted  
**Date:** 2026-08-14

## Context

Architecture and implementation should begin before company and product branding is final. Premature naming would spread a disposable brand through package names and contracts.

## Decision

Use `Infrastructure Intelligence Platform` for prose, `infra-intelligence-platform` for the repository, and `IIP`/`iip` for technical prefixes. Treat all as neutral placeholders. Conduct naming work only in `docs/research/brand` until a separate branding decision is accepted.

## Consequences

The repo can progress without implying a final brand. A future rename requires a migration plan for public event types, package names, environment variables, chart names, and API groups; human-facing labels can change earlier than stable contract identifiers.


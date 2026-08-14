# ADR 0002: CloudEvents-compatible event envelope

**Status:** Accepted  
**Date:** 2026-08-14

## Context

The event plane must accept facts from many providers and move across HTTP, queues, streams, SDKs, and plugins. Inventing a complete envelope would create avoidable adapters and terminology.

## Decision

Use CloudEvents 1.0 structured JSON attributes with mandatory `tenantid` and optional correlation, causation, and trace extensions. Use `(tenantid, source, id)` for deduplication and version event data through the `type` suffix.

## Consequences

Existing CloudEvents tooling can carry the envelope. Platform-specific event semantics still require schemas and taxonomy governance. Tenant identity is verified from authenticated transport context rather than trusted from the extension alone.


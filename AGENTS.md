# Codex working agreement

This file is the operating guide for humans and coding agents working in this repository.

## Read before changing behavior

1. `docs/product/constitution.md`
2. `docs/architecture/overview.md`
3. `docs/architecture/repository-structure.md`
4. The specification and JSON Schema for the contract being changed
5. Any relevant decision record under `docs/decisions/`

## Non-negotiable boundaries

- `domain` imports no other `iip` package.
- `application` may import `domain`; it defines use cases and ports.
- `adapters` may import `application` and `domain`; vendor behavior belongs here or in a plugin.
- `surfaces` compose use cases and own protocol/presentation logic.
- `bootstrap.py` is the composition root. Do not construct concrete adapters inside domain or application modules.
- SDKs consume public contracts. They do not import server internals.
- A plugin never gets ambient credentials or unrestricted network/action authority.

`scripts/validate_repo.py` enforces the most important import boundaries.

## Contract change rule

For resource, event, agent, or plugin changes, update all of the following in one change:

1. `docs/specifications/<contract>.md`
2. `contracts/schemas/<contract>.schema.json`
3. `contracts/examples/<contract>.json`
4. SDK public types when affected
5. Tests and the OpenAPI description when affected
6. A decision record if compatibility or authority semantics change

Additive changes may stay within `v1alpha1`. Breaking changes require a new contract version and migration notes.

## Security rules

- Treat all plugin, integration, tool, event, and LLM output as untrusted input.
- Never place credentials or raw secrets in resources, events, evidence, prompts, logs, or examples.
- Enforce tenant and actor scope at every port, even if an adapter is currently in-memory.
- Agent tools are read-only by default. Mutations require policy evaluation, an idempotency key, an audit event, and usually human approval.
- External error responses contain stable error codes, not stack traces or provider exception text.

## Definition of done

Run:

```bash
make verify
```

Behavioral changes also require tests at the owning boundary. Documentation is part of the change, not follow-up work.

## Naming

`Infrastructure Intelligence Platform`, `IIP`, `iip`, and `infra-intelligence-platform` are neutral placeholders. Do not invent a product/company name in implementation work. Add naming research only under `docs/research/brand/`.


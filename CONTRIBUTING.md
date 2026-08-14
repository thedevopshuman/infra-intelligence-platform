# Contributing

This repository is in foundation stage. Prefer small vertical slices that preserve the documented boundaries over broad scaffolding with no executable contract.

## Workflow

Install verification dependencies once with `make install-verify-deps`.

1. Link the change to a roadmap outcome or write a short decision record.
2. Change the public contract first when observable behavior changes.
3. Add focused tests for the domain rule or boundary.
4. Run `make verify`.
5. Update living documentation in the same change.

Use conventional commit prefixes (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `build:`). Do not commit secrets, local credentials, provider payloads, or production evidence.

## Compatibility

Contracts are `v1alpha1`: they may evolve, but changes must remain explicit. Prefer additive fields. Breaking changes require a new schema identifier, API version, examples, SDK updates, and migration notes.

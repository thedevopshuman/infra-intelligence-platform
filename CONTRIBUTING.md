# Contributing

Start with [AGENTS.md](AGENTS.md), the product constitution, architecture, and
the specification/schema for the boundary you change. Preserve tenant and
authority checks and package dependency directions. Contract changes include
specification, schema, example, SDK types, relevant tests/OpenAPI, and a
decision record when semantics change.

Use a branch and pull request. Explain the user outcome, scope, tests, and
limitations. Run `make verify` with Python 3.11+, the pinned verification
dependencies, Node/npm, and Helm installed as described in README. Behavioral
changes need owning-boundary tests. The Docker learning launcher alone does not
run the full contributor verification suite.

Prefer small executable slices. Link a change to a roadmap outcome or decision
record, change its contract first, and update living documentation in the same
change. Use conventional commit prefixes (`feat:`, `fix:`, `docs:`, `test:`,
`refactor:`, `build:`). Install pinned checks with `make install-verify-deps`.
Contracts are `v1alpha1`: prefer additive changes; breaking changes require a
new schema identifier/API version, examples, SDK updates, and migration notes.

Original contributions are under Apache-2.0, the repository license. Only
submit work you have the right to contribute. Preserve third-party notices and
identify any copied or adapted material and its license. Never commit secrets,
private customer data, generated `.iip` state, or raw model content. Do not
invent a company or product name; IIP is still a neutral placeholder.

Use [GitHub issues](https://github.com/thedevopshuman/infra-intelligence-platform/issues)
for ordinary bugs and learning feedback; use [SECURITY.md](SECURITY.md) for
security reports. Include your OS, Docker/Compose versions, release, the failed
step and redacted output. Public feedback is best-effort community support,
not an enterprise service agreement.

# Kubernetes observer example

This directory shows the package boundary for a read-only plugin. The manifest is real and schema-valid; the executable protocol is intentionally deferred until the handshake decision in roadmap phase 5.

The example requests only Kubernetes API read access, emits canonical resource observations and events, and may return evidence references. It requests no action permission. A future implementation must:

1. reconcile watches with periodic lists and source checkpoints;
2. derive tenant scope from the host capability token;
3. omit Kubernetes Secrets and redact sensitive annotations;
4. support deadline, cancellation, health, and bounded output;
5. pass the plugin conformance suite without importing server internals.

`plugin.json` is duplicated into `contracts/examples/plugin-manifest.json` as the canonical documentation example; repository validation keeps them equal.

